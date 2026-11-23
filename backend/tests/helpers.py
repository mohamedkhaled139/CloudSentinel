from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from cloudsentinel.models import CheckStatus, Component, Dependency, Monitor
from cloudsentinel.providers.base import CheckOutcome
from cloudsentinel.services.monitoring import record_outcome


def component(db: Session, name: str, criticality: str = "medium") -> Component:
    c = Component(name=name, criticality=criticality)
    db.add(c)
    db.flush()
    return c


def depends(db: Session, source: Component, target: Component) -> None:
    db.add(Dependency(source_id=source.id, target_id=target.id))
    db.flush()


def monitor(db: Session, name: str, comp: Component | None = None, **kw) -> Monitor:
    m = Monitor(
        name=name,
        kind=kw.pop("kind", "tcp"),
        config=kw.pop("config", {"host": "127.0.0.1", "port": 1}),
        component_id=comp.id if comp else None,
        failure_threshold=kw.pop("failure_threshold", 2),
        recovery_threshold=kw.pop("recovery_threshold", 2),
        **kw,
    )
    db.add(m)
    db.flush()
    db.refresh(m)
    return m


def feed(
    db: Session,
    m: Monitor,
    statuses: str,
    start: datetime,
    step: timedelta = timedelta(minutes=1),
    latency: float | None = 50.0,
) -> datetime:
    """Record a sequence of results: u=up, d=down, g=degraded, x=unknown."""
    mapping = {
        "u": CheckStatus.up,
        "d": CheckStatus.down,
        "g": CheckStatus.degraded,
        "x": CheckStatus.unknown,
    }
    t = start
    for ch in statuses:
        st = mapping[ch]
        record_outcome(
            db, m, CheckOutcome(st, f"result {ch}", latency if st != CheckStatus.down else None), at=t
        )
        t += step
    db.flush()
    return t
