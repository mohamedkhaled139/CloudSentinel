"""Application settings, system information, audit log, and health/readiness probes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from cloudsentinel import __version__
from cloudsentinel.config import get_settings
from cloudsentinel.db import get_db, get_engine
from cloudsentinel.deps import audit, require_admin, require_viewer
from cloudsentinel.models import AuditLog, User
from cloudsentinel.schemas import AppSettingsUpdate, SystemInfo
from cloudsentinel.services.retention import apply_retention
from cloudsentinel.services.settings_store import AppSettings, get_app_settings, update_app_settings

router = APIRouter(tags=["system"])
probes = APIRouter(tags=["probes"])


@router.get("/settings", response_model=AppSettings)
def read_settings(_: User = Depends(require_viewer), db: Session = Depends(get_db)) -> AppSettings:
    return get_app_settings(db)


@router.put("/settings", response_model=AppSettings)
def write_settings(
    body: AppSettingsUpdate, user: User = Depends(require_admin), db: Session = Depends(get_db)
) -> AppSettings:
    changes = body.model_dump(exclude_none=True)
    result = update_app_settings(db, changes)
    audit(db, user, "settings.updated", "app", fields=sorted(changes))
    db.commit()
    return result


@router.post("/settings/retention/run")
def run_retention(user: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, int]:
    result = apply_retention(db)
    audit(db, user, "retention.run", "", **result)
    db.commit()
    return result


@router.get("/system/info", response_model=SystemInfo)
def system_info(request: Request, _: User = Depends(require_viewer)) -> SystemInfo:
    s = get_settings()
    scheduler = getattr(request.app.state, "scheduler", None)
    return SystemInfo(
        version=__version__,
        environment=s.environment,
        database="sqlite" if s.is_sqlite else "postgresql",
        demo_mode=s.demo_mode,
        scheduler_enabled=s.scheduler_enabled,
        scheduler_healthy=scheduler.healthy if scheduler else None,
        allow_private_targets=s.allow_private_targets,
    )


@router.get("/audit")
def audit_log(
    limit: int = Query(default=100, ge=1, le=1000),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.at.desc()).limit(limit))
    return [
        {
            "id": r.id,
            "at": r.at.isoformat(),
            "actor": r.actor,
            "action": r.action,
            "target": r.target,
            "data": r.data,
        }
        for r in rows
    ]


@probes.get("/healthz", include_in_schema=True)
def healthz() -> dict[str, str]:
    """Liveness: the process is running."""
    return {"status": "ok"}


@probes.get("/readyz")
def readyz(request: Request, response: Response) -> dict[str, Any]:
    """Readiness: database reachable and (if enabled) scheduler ticking."""
    checks: dict[str, str] = {}
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # report, never raise from a probe
        checks["database"] = f"error: {exc.__class__.__name__}"
    scheduler = getattr(request.app.state, "scheduler", None)
    if get_settings().scheduler_enabled:
        checks["scheduler"] = "ok" if scheduler is not None and scheduler.healthy else "starting_or_stalled"
    ok = all(v == "ok" for v in checks.values())
    response.status_code = 200 if ok else 503
    return {"status": "ok" if ok else "unavailable", "checks": checks}
