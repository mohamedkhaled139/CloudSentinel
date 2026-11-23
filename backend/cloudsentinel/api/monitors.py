"""Monitors, check execution and monitoring history."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cloudsentinel.config import get_settings
from cloudsentinel.db import get_db, session_factory
from cloudsentinel.deps import audit, require_operator, require_viewer
from cloudsentinel.models import CheckResult, CheckStatus, Component, Integration, Monitor, User, utcnow
from cloudsentinel.providers.registry import registry
from cloudsentinel.schemas import CheckResultOut, MonitorCreate, MonitorOut, MonitorStats, MonitorUpdate, Page
from cloudsentinel.services.monitoring import build_context, execute_check, run_monitor_once

router = APIRouter(tags=["monitors"])


@router.get("/providers")
def list_providers(_: User = Depends(require_viewer)) -> dict[str, Any]:
    demo = get_settings().demo_mode
    return {
        "providers": [p.describe() for p in registry.providers.values() if demo or not p.synthetic],
        "integrations": [a.describe() for a in registry.adapters.values()],
    }


def _validate(
    db: Session, kind: str, config: dict[str, Any], integration_id: int | None, component_id: int | None
) -> dict[str, Any]:
    try:
        provider = registry.provider(kind)
    except KeyError as exc:
        raise HTTPException(422, str(exc)) from None
    if provider.synthetic and not get_settings().demo_mode:
        raise HTTPException(422, "Synthetic monitors are only available in demo mode")
    try:
        normalized = provider.validate_config(config).model_dump(mode="json")
    except ValidationError as exc:
        raise HTTPException(
            422,
            {
                "message": "Invalid monitor configuration",
                "errors": exc.errors(include_url=False, include_context=False),
            },
        ) from None
    if provider.integration_kind:
        integ = db.get(Integration, integration_id) if integration_id else None
        if integ is None or integ.kind != provider.integration_kind:
            raise HTTPException(
                422, f"Monitor kind '{kind}' requires a '{provider.integration_kind}' integration"
            )
    elif integration_id is not None:
        raise HTTPException(422, f"Monitor kind '{kind}' does not use an integration")
    if component_id is not None and db.get(Component, component_id) is None:
        raise HTTPException(422, "Component not found")
    return normalized


def _get(db: Session, monitor_id: int) -> Monitor:
    m = db.get(Monitor, monitor_id)
    if m is None:
        raise HTTPException(404, "Monitor not found")
    return m


@router.get("/monitors", response_model=list[MonitorOut])
def list_monitors(
    component_id: int | None = None,
    kind: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    _: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> list[Monitor]:
    q = select(Monitor).order_by(Monitor.name)
    if component_id is not None:
        q = q.where(Monitor.component_id == component_id)
    if kind:
        q = q.where(Monitor.kind == kind)
    if status_filter:
        q = q.where(Monitor.last_status == status_filter)
    return list(db.scalars(q))


@router.post("/monitors", response_model=MonitorOut, status_code=201)
def create_monitor(
    body: MonitorCreate, user: User = Depends(require_operator), db: Session = Depends(get_db)
) -> Monitor:
    config = _validate(db, body.kind, body.config, body.integration_id, body.component_id)
    m = Monitor(**{**body.model_dump(), "config": config}, next_run_at=utcnow())
    db.add(m)
    db.flush()
    audit(db, user, "monitor.created", m.name, kind=m.kind)
    db.commit()
    return m


@router.post("/monitors/test")
async def test_monitor_config(
    body: MonitorCreate, request: Request, _: User = Depends(require_operator), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Execute a check from an unsaved configuration and return the outcome without recording it."""
    config = _validate(db, body.kind, body.config, body.integration_id, body.component_id)
    transient = Monitor(
        id=0,
        kind=body.kind,
        config=config,
        integration_id=body.integration_id,
        timeout_seconds=body.timeout_seconds,
        degraded_latency_ms=body.degraded_latency_ms,
    )
    ctx = build_context(db, transient, session_factory(), get_settings().allow_private_targets)
    outcome = await execute_check(ctx, body.kind)
    return {
        "status": outcome.status,
        "message": outcome.message,
        "latency_ms": outcome.latency_ms,
        "details": outcome.details,
    }


@router.get("/monitors/{monitor_id}", response_model=MonitorOut)
def get_monitor(monitor_id: int, _: User = Depends(require_viewer), db: Session = Depends(get_db)) -> Monitor:
    return _get(db, monitor_id)


@router.patch("/monitors/{monitor_id}", response_model=MonitorOut)
def update_monitor(
    monitor_id: int,
    body: MonitorUpdate,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> Monitor:
    m = _get(db, monitor_id)
    changes = body.model_dump(exclude_unset=True)
    integration_id = changes.get("integration_id", m.integration_id)
    component_id = changes.get("component_id", m.component_id)
    config = _validate(db, m.kind, changes.get("config") or m.config, integration_id, component_id)
    for key, value in changes.items():
        if key == "config":
            continue
        if value is None and key not in ("component_id", "integration_id", "degraded_latency_ms"):
            continue
        setattr(m, key, value)
    m.config = config
    if changes.get("enabled") is True or "interval_seconds" in changes:
        m.next_run_at = utcnow()
    audit(db, user, "monitor.updated", m.name)
    db.commit()
    return m


@router.delete("/monitors/{monitor_id}", status_code=204)
def delete_monitor(
    monitor_id: int, user: User = Depends(require_operator), db: Session = Depends(get_db)
) -> Response:
    m = _get(db, monitor_id)
    db.delete(m)
    audit(db, user, "monitor.deleted", m.name)
    db.commit()
    return Response(status_code=204)


@router.post("/monitors/{monitor_id}/run", response_model=CheckResultOut)
async def run_now(
    monitor_id: int, _: User = Depends(require_operator), db: Session = Depends(get_db)
) -> CheckResult:
    m = _get(db, monitor_id)
    provider = registry.providers.get(m.kind)
    if provider is not None and provider.synthetic and not get_settings().demo_mode:
        raise HTTPException(status.HTTP_409_CONFLICT, "Synthetic monitors only run in demo mode")
    result = await run_monitor_once(monitor_id, session_factory(), get_settings().allow_private_targets)
    if result is None:
        raise HTTPException(404, "Monitor not found")
    return result


@router.get("/monitors/{monitor_id}/results", response_model=Page)
def results(
    monitor_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Page:
    _get(db, monitor_id)
    q = select(CheckResult).where(CheckResult.monitor_id == monitor_id)
    if start:
        q = q.where(CheckResult.checked_at >= start)
    if end:
        q = q.where(CheckResult.checked_at < end)
    if status_filter:
        q = q.where(CheckResult.status == status_filter)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(CheckResult.checked_at.desc()).limit(limit).offset(offset)).all()
    return Page(total=total, items=[CheckResultOut.model_validate(r).model_dump(mode="json") for r in rows])


@router.get("/monitors/{monitor_id}/stats", response_model=MonitorStats)
def stats(
    monitor_id: int,
    hours: int = Query(default=24, ge=1, le=24 * 90),
    _: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> MonitorStats:
    _get(db, monitor_id)
    now = utcnow()
    since = now - timedelta(hours=hours)
    rows = db.execute(
        select(CheckResult.checked_at, CheckResult.status, CheckResult.latency_ms)
        .where(CheckResult.monitor_id == monitor_id, CheckResult.checked_at >= since)
        .order_by(CheckResult.checked_at)
    ).all()
    return MonitorStats(window_hours=hours, **summarize(rows, since, now))


def summarize(rows: Sequence[Any], since: datetime, now: datetime, buckets: int = 96) -> dict[str, Any]:
    known = [r for r in rows if r.status != CheckStatus.unknown]
    failed = sum(1 for r in known if r.status == CheckStatus.down)
    lat = sorted(r.latency_ms for r in rows if r.latency_ms is not None)
    size = (now - since) / buckets
    series: list[dict[str, Any]] = []
    grouped: dict[int, list[Any]] = {}
    for r in rows:
        idx = min(buckets - 1, int((r.checked_at - since) / size))
        grouped.setdefault(idx, []).append(r)
    for i in range(buckets):
        bucket = grouped.get(i, [])
        b_known = [r for r in bucket if r.status != CheckStatus.unknown]
        b_lat = [r.latency_ms for r in bucket if r.latency_ms is not None]
        statuses = {r.status for r in bucket}
        worst = next((s for s in ("down", "degraded", "up", "unknown") if s in statuses), None)
        series.append(
            {
                "t": (since + size * i).isoformat(),
                "availability": round(100 * sum(1 for r in b_known if r.status != "down") / len(b_known), 3)
                if b_known
                else None,
                "latency_ms": round(sum(b_lat) / len(b_lat), 2) if b_lat else None,
                "status": worst,
                "checks": len(bucket),
            }
        )
    return {
        "checks": len(rows),
        "availability_pct": round(100 * (len(known) - failed) / len(known), 4) if known else None,
        "avg_latency_ms": round(sum(lat) / len(lat), 2) if lat else None,
        "p95_latency_ms": round(lat[min(len(lat) - 1, int(0.95 * (len(lat) - 1) + 0.5))], 2) if lat else None,
        "series": series,
    }
