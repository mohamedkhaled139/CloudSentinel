"""Data retention: prune historical data according to the configured policy."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, Delete, delete
from sqlalchemy.orm import Session

from cloudsentinel.models import (
    AgentReport,
    CheckResult,
    Incident,
    IncidentStatus,
    RiskFinding,
    RiskStatus,
    utcnow,
)
from cloudsentinel.services.settings_store import get_app_settings

log = logging.getLogger(__name__)


def _run(db: Session, stmt: Delete) -> int:
    result = cast(CursorResult[Any], db.execute(stmt))
    return int(result.rowcount or 0)


def apply_retention(db: Session, now: datetime | None = None) -> dict[str, int]:
    now = now or utcnow()
    cfg = get_app_settings(db)
    out = {
        "check_results": _run(
            db,
            delete(CheckResult).where(
                CheckResult.checked_at < now - timedelta(days=cfg.retention_check_results_days)
            ),
        ),
        "agent_reports": _run(
            db,
            delete(AgentReport).where(
                AgentReport.received_at < now - timedelta(days=cfg.retention_agent_reports_days)
            ),
        ),
        "incidents": _run(
            db,
            delete(Incident).where(
                Incident.status == IncidentStatus.resolved,
                Incident.resolved_at < now - timedelta(days=cfg.retention_resolved_incidents_days),
            ),
        ),
        "risk_findings": _run(
            db,
            delete(RiskFinding).where(
                RiskFinding.status == RiskStatus.resolved,
                RiskFinding.resolved_at < now - timedelta(days=cfg.retention_resolved_risks_days),
            ),
        ),
    }
    db.flush()
    log.info("retention applied", extra={f"deleted_{k}": v for k, v in out.items()})
    return out
