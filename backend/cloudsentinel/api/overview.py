"""Overview dashboard data."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from cloudsentinel.api.incidents import to_out
from cloudsentinel.api.risks import SEVERITY_ORDER
from cloudsentinel.config import get_settings
from cloudsentinel.db import get_db
from cloudsentinel.deps import require_viewer
from cloudsentinel.models import (
    CheckResult,
    CheckStatus,
    Component,
    Incident,
    Monitor,
    RiskFinding,
    RiskStatus,
    User,
    utcnow,
)
from cloudsentinel.schemas import OverviewOut, RiskOut
from cloudsentinel.services.incidents import OPEN_STATUSES
from cloudsentinel.services.topology import compute_health

router = APIRouter(tags=["overview"])


def _availability(db: Session, hours: int) -> float | None:
    since = utcnow() - timedelta(hours=hours)
    failed = case((CheckResult.status == CheckStatus.down, 1), else_=0)
    n, f = db.execute(
        select(func.count(CheckResult.id), func.sum(failed)).where(
            CheckResult.checked_at >= since, CheckResult.status != CheckStatus.unknown
        )
    ).one()
    return round(100 * (n - (f or 0)) / n, 3) if n else None


@router.get("/overview", response_model=OverviewOut)
def overview(_: User = Depends(require_viewer), db: Session = Depends(get_db)) -> OverviewOut:
    now = utcnow()
    health = compute_health(db)
    comp_counts = Counter(h.effective_status for h in health.values())
    mon_counts = Counter(
        s if enabled else "disabled"
        for s, enabled in db.execute(select(Monitor.last_status, Monitor.enabled))
    )
    open_incidents = list(
        db.scalars(
            select(Incident)
            .where(Incident.status.in_(OPEN_STATUSES))
            .order_by(Incident.started_at.desc())
            .limit(20)
        )
    )
    risk_counts = Counter(
        db.scalars(select(RiskFinding.severity).where(RiskFinding.status == RiskStatus.open))
    )
    top_risks = list(
        db.scalars(
            select(RiskFinding)
            .where(RiskFinding.status == RiskStatus.open)
            .order_by(SEVERITY_ORDER, RiskFinding.last_seen_at.desc())
            .limit(5)
        )
    )

    # Hourly availability for the last 24 hours.
    since = now - timedelta(hours=24)
    buckets: dict[int, list[int]] = {}
    for at, status in db.execute(
        select(CheckResult.checked_at, CheckResult.status).where(
            CheckResult.checked_at >= since, CheckResult.status != CheckStatus.unknown
        )
    ):
        idx = min(23, int((at - since).total_seconds() // 3600))
        b = buckets.setdefault(idx, [0, 0])
        b[0] += 1
        b[1] += status == CheckStatus.down
    trend = [
        {
            "t": (since + timedelta(hours=i)).isoformat(),
            "availability": round(100 * (buckets[i][0] - buckets[i][1]) / buckets[i][0], 3)
            if i in buckets
            else None,
            "checks": buckets.get(i, [0])[0],
        }
        for i in range(24)
    ]
    recent = db.execute(
        select(CheckResult.checked_at, CheckResult.status, CheckResult.message, Monitor.name, Monitor.id)
        .join(Monitor, Monitor.id == CheckResult.monitor_id)
        .where(CheckResult.status != CheckStatus.up)
        .order_by(CheckResult.checked_at.desc())
        .limit(10)
    ).all()
    synthetic = bool(
        db.scalar(select(func.count(Component.id)).where(Component.is_synthetic.is_(True)))
        or db.scalar(select(func.count(Monitor.id)).where(Monitor.is_synthetic.is_(True)))
    )
    return OverviewOut(
        generated_at=now,
        demo_mode=get_settings().demo_mode,
        contains_synthetic_data=synthetic,
        components={"total": len(health), **comp_counts},
        monitors={"total": sum(mon_counts.values()), **mon_counts},
        availability_24h=_availability(db, 24),
        availability_7d=_availability(db, 24 * 7),
        open_incidents=to_out(db, open_incidents),
        open_risks={"total": sum(risk_counts.values()), **risk_counts},
        top_risks=[RiskOut.model_validate(r) for r in top_risks],
        recent_results=[
            {
                "checked_at": r[0].isoformat(),
                "status": r[1],
                "message": r[2],
                "monitor": r[3],
                "monitor_id": r[4],
            }
            for r in recent
        ],
        availability_trend=trend,
    )
