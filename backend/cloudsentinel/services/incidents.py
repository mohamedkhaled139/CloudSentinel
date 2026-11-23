"""Incident lifecycle: automatic detection, correlation, acknowledgement and resolution.

Lifecycle: ``detected`` -> ``investigating`` (acknowledged) -> ``resolved``.
Incidents open only after ``failure_threshold`` consecutive failing checks, so a single blip never
creates an incident. Correlation is based on the declared dependency graph and is always presented
as a *probable* cause, never as a confirmed root cause.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cloudsentinel.models import (
    CheckResult,
    CheckStatus,
    Component,
    Criticality,
    Incident,
    IncidentEvent,
    IncidentStatus,
    Monitor,
    Severity,
    utcnow,
)
from cloudsentinel.services.settings_store import get_app_settings
from cloudsentinel.services.topology import load_graph

log = logging.getLogger(__name__)

OPEN_STATUSES = (IncidentStatus.detected, IncidentStatus.investigating)

_SEVERITY_BY_CRITICALITY = {
    Criticality.critical: Severity.critical,
    Criticality.high: Severity.major,
    Criticality.medium: Severity.major,
    Criticality.low: Severity.minor,
}


class IncidentStateError(ValueError):
    pass


def is_failing(monitor: Monitor, status: str) -> bool:
    return status == CheckStatus.down or (
        status == CheckStatus.degraded and monitor.open_incident_on_degraded
    )


def open_incident_for_monitor(db: Session, monitor_id: int) -> Incident | None:
    return db.scalars(
        select(Incident)
        .where(Incident.monitor_id == monitor_id, Incident.status.in_(OPEN_STATUSES))
        .order_by(Incident.started_at.desc())
        .limit(1)
    ).first()


def add_event(
    db: Session,
    incident: Incident,
    kind: str,
    message: str,
    actor: str = "system",
    data: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> IncidentEvent:
    event = IncidentEvent(
        incident_id=incident.id, kind=kind, message=message, actor=actor, data=data or {}, at=at or utcnow()
    )
    db.add(event)
    return event


def handle_check_result(
    db: Session, monitor: Monitor, result: CheckResult, previous_status: str
) -> Incident | None:
    """Called after a result is recorded and the monitor's counters are updated."""
    failing = is_failing(monitor, result.status)
    if (
        not failing
        and previous_status == CheckStatus.up
        and monitor.consecutive_successes > monitor.recovery_threshold
    ):
        return None  # steady healthy state: nothing can change (fast path, avoids a query per check)
    if result.id is None:
        db.flush()
    incident = open_incident_for_monitor(db, monitor.id)

    if incident is None:
        if failing and monitor.consecutive_failures >= monitor.failure_threshold:
            return _open_incident(db, monitor, result)
        return None

    if failing and result.status != previous_status:
        add_event(
            db,
            incident,
            "check_failed",
            f"Check result changed to {result.status}: {result.message}",
            data={"result_id": result.id, "status": result.status},
            at=result.checked_at,
        )
    elif result.status == CheckStatus.up and previous_status != CheckStatus.up:
        add_event(
            db,
            incident,
            "check_recovering",
            f"Check passing again: {result.message}",
            data={"result_id": result.id},
            at=result.checked_at,
        )

    if result.status == CheckStatus.up and monitor.consecutive_successes >= monitor.recovery_threshold:
        add_event(
            db,
            incident,
            "recovered",
            f"Monitor healthy for {monitor.consecutive_successes} consecutive checks",
            at=result.checked_at,
        )
        if get_app_settings(db).auto_resolve_incidents:
            resolve(
                db,
                incident,
                actor="system",
                notes="Automatically resolved after the monitor recovered.",
                at=result.checked_at,
            )
    return incident


def _open_incident(db: Session, monitor: Monitor, result: CheckResult) -> Incident:
    component = monitor.component
    subject = component.name if component else monitor.name
    criticality = component.criticality if component else Criticality.medium
    if result.status == CheckStatus.degraded:
        severity = Severity.minor
        title = f"{subject} is degraded"
    else:
        severity = _SEVERITY_BY_CRITICALITY.get(Criticality(criticality), Severity.major)
        title = f"{subject} is unavailable"

    recent = db.scalars(
        select(CheckResult)
        .where(CheckResult.monitor_id == monitor.id)
        .order_by(CheckResult.checked_at.desc())
        .limit(monitor.failure_threshold)
    ).all()
    first_failure = min((r.checked_at for r in recent), default=result.checked_at)

    incident = Incident(
        title=title,
        status=IncidentStatus.detected,
        severity=severity,
        component_id=monitor.component_id,
        monitor_id=monitor.id,
        summary=f"Monitor '{monitor.name}' ({monitor.kind}) failed {monitor.consecutive_failures} consecutive "
        f"checks. Last result: {result.message}",
        started_at=first_failure,
        is_synthetic=monitor.is_synthetic,
    )
    db.add(incident)
    db.flush()
    add_event(
        db,
        incident,
        "detected",
        f"Incident opened after {monitor.consecutive_failures} consecutive failing checks "
        f"(threshold {monitor.failure_threshold}).",
        data={
            "evidence": [
                {
                    "result_id": r.id,
                    "checked_at": r.checked_at.isoformat(),
                    "status": r.status,
                    "message": r.message,
                }
                for r in reversed(recent)
            ]
        },
        at=result.checked_at,
    )
    if monitor.component_id is not None:
        correlate(db, incident)
    log.info(
        "incident opened", extra={"incident_id": incident.id, "monitor_id": monitor.id, "severity": severity}
    )
    return incident


def correlate(db: Session, incident: Incident) -> None:
    """Link incidents along the dependency graph.

    * If a component this one depends on already has an open incident, this incident is linked as a
      probable consequence of it.
    * Open incidents on dependents that are not yet linked are re-linked under this one, which is a
      probable upstream cause.
    """
    assert incident.component_id is not None
    graph = load_graph(db)
    upstream = graph.upstream(incident.component_id)
    downstream = graph.downstream(incident.component_id)
    others = db.scalars(
        select(Incident).where(
            Incident.status.in_(OPEN_STATUSES), Incident.id != incident.id, Incident.component_id.is_not(None)
        )
    ).all()

    upstream_open = sorted((i for i in others if i.component_id in upstream), key=lambda i: i.started_at)
    if upstream_open:
        cause = upstream_open[0]
        root = cause
        # Point at the root of an existing chain.
        while root.parent_id is not None:
            parent = db.get(Incident, root.parent_id)
            if parent is None or parent.status == IncidentStatus.resolved:
                break
            root = parent
        incident.parent_id = root.id
        add_event(
            db,
            incident,
            "correlated",
            f"Probably caused by incident #{root.id} ({root.title}): this component depends on it.",
            data={"related_incident_id": root.id, "relation": "probable_cause"},
        )
        add_event(
            db,
            root,
            "correlated",
            f"Related incident #{incident.id} ({incident.title}) on a dependent component.",
            data={"related_incident_id": incident.id, "relation": "probable_effect"},
        )
        return

    for other in others:
        if other.component_id in downstream and other.parent_id is None:
            other.parent_id = incident.id
            add_event(
                db,
                other,
                "correlated",
                f"Probably caused by incident #{incident.id} ({incident.title}) on an upstream dependency.",
                data={"related_incident_id": incident.id, "relation": "probable_cause"},
            )
            add_event(
                db,
                incident,
                "correlated",
                f"Dependent incident #{other.id} ({other.title}) linked as a probable effect.",
                data={"related_incident_id": other.id, "relation": "probable_effect"},
            )


def acknowledge(db: Session, incident: Incident, actor: str, note: str = "") -> Incident:
    if incident.status == IncidentStatus.resolved:
        raise IncidentStateError("incident is already resolved")
    if incident.acknowledged_at is None:
        incident.acknowledged_at = utcnow()
        incident.acknowledged_by = actor
    incident.status = IncidentStatus.investigating
    add_event(db, incident, "acknowledged", note or f"Acknowledged by {actor}; investigating.", actor=actor)
    return incident


def add_note(db: Session, incident: Incident, actor: str, note: str) -> IncidentEvent:
    return add_event(db, incident, "note", note, actor=actor)


def resolve(
    db: Session, incident: Incident, actor: str, notes: str = "", at: datetime | None = None
) -> Incident:
    if incident.status == IncidentStatus.resolved:
        raise IncidentStateError("incident is already resolved")
    incident.status = IncidentStatus.resolved
    incident.resolved_at = at or utcnow()
    incident.resolved_by = actor
    if notes:
        incident.resolution_notes = notes
    add_event(
        db, incident, "resolved", notes or f"Resolved by {actor}.", actor=actor, at=incident.resolved_at
    )
    return incident


def reopen(db: Session, incident: Incident, actor: str, note: str = "") -> Incident:
    if incident.status != IncidentStatus.resolved:
        raise IncidentStateError("only resolved incidents can be reopened")
    incident.status = IncidentStatus.investigating
    incident.resolved_at = None
    incident.resolved_by = None
    add_event(db, incident, "reopened", note or f"Reopened by {actor}.", actor=actor)
    return incident


def create_manual_incident(
    db: Session, title: str, severity: str, component: Component | None, summary: str, actor: str
) -> Incident:
    incident = Incident(
        title=title,
        severity=severity,
        component_id=component.id if component else None,
        summary=summary,
        status=IncidentStatus.detected,
    )
    db.add(incident)
    db.flush()
    add_event(db, incident, "detected", f"Incident declared manually by {actor}.", actor=actor)
    if component is not None:
        correlate(db, incident)
    return incident
