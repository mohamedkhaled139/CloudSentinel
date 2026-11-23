"""Integration (data source) configuration and Linux agents."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cloudsentinel.db import get_db, session_factory
from cloudsentinel.deps import audit, require_admin, require_viewer
from cloudsentinel.models import Agent, AgentReport, Integration, Monitor, User, utcnow
from cloudsentinel.providers.registry import registry
from cloudsentinel.schemas import (
    AgentCreate,
    AgentCreated,
    AgentHeartbeat,
    AgentOut,
    ConnectionTestOut,
    IntegrationCreate,
    IntegrationOut,
    IntegrationUpdate,
)
from cloudsentinel.security import decrypt_secrets, encrypt_secrets, generate_agent_token, hash_token
from cloudsentinel.services.scheduler import test_integration

router = APIRouter(tags=["integrations"])


def _out(db: Session, integ: Integration) -> IntegrationOut:
    out = IntegrationOut.model_validate(integ)
    try:
        out.secret_fields_set = sorted(k for k, v in decrypt_secrets(integ.secret_encrypted).items() if v)
    except RuntimeError:
        out.secret_fields_set = []
    out.monitor_count = (
        db.scalar(select(func.count(Monitor.id)).where(Monitor.integration_id == integ.id)) or 0
    )
    return out


def _validate(
    kind: str, config: dict[str, object], secrets: dict[str, object]
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        adapter = registry.adapter(kind)
    except KeyError as exc:
        raise HTTPException(422, str(exc)) from None
    try:
        cfg = adapter.config_model.model_validate(config).model_dump(mode="json")
        sec = adapter.secret_model.model_validate(secrets).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise HTTPException(
            422,
            {
                "message": "Invalid integration configuration",
                "errors": exc.errors(include_url=False, include_context=False, include_input=False),
            },
        ) from None
    return cfg, sec


@router.get("/integrations", response_model=list[IntegrationOut])
def list_integrations(
    _: User = Depends(require_viewer), db: Session = Depends(get_db)
) -> list[IntegrationOut]:
    return [_out(db, i) for i in db.scalars(select(Integration).order_by(Integration.name))]


@router.post("/integrations", response_model=IntegrationOut, status_code=201)
def create_integration(
    body: IntegrationCreate, user: User = Depends(require_admin), db: Session = Depends(get_db)
) -> IntegrationOut:
    cfg, sec = _validate(body.kind, body.config, body.secrets)
    integ = Integration(
        name=body.name,
        kind=body.kind,
        config=cfg,
        secret_encrypted=encrypt_secrets(sec),
        enabled=body.enabled,
    )
    db.add(integ)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(409, "An integration with this name already exists") from None
    audit(db, user, "integration.created", integ.name, kind=integ.kind)
    db.commit()
    return _out(db, integ)


def _get(db: Session, integration_id: int) -> Integration:
    integ = db.get(Integration, integration_id)
    if integ is None:
        raise HTTPException(404, "Integration not found")
    return integ


@router.patch("/integrations/{integration_id}", response_model=IntegrationOut)
def update_integration(
    integration_id: int,
    body: IntegrationUpdate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> IntegrationOut:
    integ = _get(db, integration_id)
    try:
        current = decrypt_secrets(integ.secret_encrypted)
    except RuntimeError:
        current = {}
    merged = dict(current)
    for k, v in (body.secrets or {}).items():
        if v is None or v == "":
            merged.pop(k, None)
        else:
            merged[k] = v
    cfg, sec = _validate(integ.kind, body.config if body.config is not None else integ.config, merged)
    integ.config, integ.secret_encrypted = cfg, encrypt_secrets(sec)
    if body.name is not None:
        integ.name = body.name
    if body.enabled is not None:
        integ.enabled = body.enabled
    integ.status = "unknown"
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(409, "An integration with this name already exists") from None
    audit(db, user, "integration.updated", integ.name, secrets_changed=sorted((body.secrets or {}).keys()))
    db.commit()
    return _out(db, integ)


@router.delete("/integrations/{integration_id}", status_code=204)
def delete_integration(
    integration_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)
) -> Response:
    integ = _get(db, integration_id)
    db.delete(integ)
    audit(db, user, "integration.deleted", integ.name)
    db.commit()
    return Response(status_code=204)


@router.post("/integrations/{integration_id}/test", response_model=ConnectionTestOut)
async def test(
    integration_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> ConnectionTestOut:
    _get(db, integration_id)
    ok, message, details = await test_integration(session_factory(), integration_id)
    return ConnectionTestOut(ok=ok, message=message, details=details)


# ---- Linux agents ---------------------------------------------------------------------------------
@router.get("/agents", response_model=list[AgentOut])
def list_agents(_: User = Depends(require_viewer), db: Session = Depends(get_db)) -> list[Agent]:
    return list(db.scalars(select(Agent).order_by(Agent.name)))


@router.post("/agents", response_model=AgentCreated, status_code=201)
def create_agent(
    body: AgentCreate, user: User = Depends(require_admin), db: Session = Depends(get_db)
) -> AgentCreated:
    token = generate_agent_token()
    agent = Agent(name=body.name, token_hash=hash_token(token), token_prefix=token[:10])
    db.add(agent)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(409, "An agent with this name already exists") from None
    audit(db, user, "agent.created", agent.name)
    db.commit()
    return AgentCreated(**AgentOut.model_validate(agent).model_dump(), token=token)


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(
    agent_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)
) -> Response:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    db.delete(agent)
    audit(db, user, "agent.deleted", agent.name)
    db.commit()
    return Response(status_code=204)


@router.post("/agent/heartbeat", status_code=202)
def heartbeat(
    body: AgentHeartbeat, authorization: str = Header(default=""), db: Session = Depends(get_db)
) -> dict[str, str]:
    """Endpoint used by the CloudSentinel Linux agent (authenticated with its agent token)."""
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    agent = db.scalars(select(Agent).where(Agent.token_hash == hash_token(token))).first() if token else None
    if agent is None:
        raise HTTPException(401, "Invalid agent token", headers={"WWW-Authenticate": "Bearer"})
    now = utcnow()
    agent.last_seen_at = now
    agent.hostname = body.hostname
    agent.agent_version = body.agent_version
    fs = [f.model_dump() for f in body.filesystems]
    db.add(
        AgentReport(
            agent_id=agent.id,
            received_at=now,
            boot_time=body.boot_time.astimezone(UTC) if body.boot_time else None,
            load1=body.load1,
            cpu_count=body.cpu_count,
            memory_used_pct=body.memory_used_pct,
            disk_used_pct=max((f["used_pct"] for f in fs), default=None),
            data={"filesystems": fs, "uptime_seconds": body.uptime_seconds},
        )
    )
    db.commit()
    return {"status": "accepted"}
