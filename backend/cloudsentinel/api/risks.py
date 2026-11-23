"""Risk intelligence findings."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from cloudsentinel.config import get_settings
from cloudsentinel.db import get_db, session_scope
from cloudsentinel.deps import audit, require_operator, require_viewer
from cloudsentinel.models import RiskFinding, RiskStatus, User
from cloudsentinel.schemas import IncidentAction, RiskOut
from cloudsentinel.services.risk import run_analysis

router = APIRouter(tags=["risks"])

SEVERITY_ORDER = case({"critical": 0, "major": 1, "minor": 2, "info": 3}, value=RiskFinding.severity, else_=4)


@router.get("/risks", response_model=list[RiskOut])
def list_risks(
    status: str | None = Query(default="open", description="open | dismissed | resolved | all"),
    category: str | None = None,
    component_id: int | None = None,
    _: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> list[RiskFinding]:
    q = select(RiskFinding)
    if status and status != "all":
        q = q.where(RiskFinding.status == status)
    if category:
        q = q.where(RiskFinding.category == category)
    if component_id is not None:
        q = q.where(RiskFinding.component_id == component_id)
    return list(db.scalars(q.order_by(SEVERITY_ORDER, RiskFinding.last_seen_at.desc()).limit(500)))


@router.post("/risks/analyze")
async def analyze_now(user: User = Depends(require_operator)) -> dict[str, int]:
    def _run() -> dict[str, int]:
        with session_scope() as db:
            stats = run_analysis(db, demo_mode=get_settings().demo_mode)
            audit(db, user.email, "risk.analysis_run")
            return stats

    return await asyncio.to_thread(_run)


def _get(db: Session, risk_id: int) -> RiskFinding:
    r = db.get(RiskFinding, risk_id)
    if r is None:
        raise HTTPException(404, "Finding not found")
    return r


@router.post("/risks/{risk_id}/dismiss", response_model=RiskOut)
def dismiss(
    risk_id: int, body: IncidentAction, user: User = Depends(require_operator), db: Session = Depends(get_db)
) -> RiskFinding:
    r = _get(db, risk_id)
    if r.status == RiskStatus.resolved:
        raise HTTPException(409, "Finding is already resolved")
    r.status = RiskStatus.dismissed
    r.dismissed_by = user.email
    audit(db, user, "risk.dismissed", r.fingerprint, note=body.note)
    db.commit()
    return r


@router.post("/risks/{risk_id}/reopen", response_model=RiskOut)
def reopen(
    risk_id: int, user: User = Depends(require_operator), db: Session = Depends(get_db)
) -> RiskFinding:
    r = _get(db, risk_id)
    if r.status != RiskStatus.dismissed:
        raise HTTPException(409, "Only dismissed findings can be reopened")
    r.status = RiskStatus.open
    r.dismissed_by = None
    audit(db, user, "risk.reopened", r.fingerprint)
    db.commit()
    return r
