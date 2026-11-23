"""Incident management and investigation."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from cloudsentinel.db import get_db
from cloudsentinel.deps import require_operator, require_viewer
from cloudsentinel.models import CheckResult, Component, Incident, User, utcnow
from cloudsentinel.schemas import (
    CheckResultOut,
    IncidentAction,
    IncidentCreate,
    IncidentDetail,
    IncidentEventOut,
    IncidentNote,
    IncidentOut,
    Page,
)
from cloudsentinel.services import incidents as svc
from cloudsentinel.services.topology import load_graph

router = APIRouter(tags=["incidents"])


def to_out(db: Session, items: list[Incident]) -> list[IncidentOut]:
    ids = [i.id for i in items]
    children = (
        Counter(db.scalars(select(Incident.parent_id).where(Incident.parent_id.in_(ids))))
        if ids
        else Counter()
    )
    out = []
    for i in items:
        o = IncidentOut.model_validate(i)
        o.component_name = i.component.name if i.component else None
        o.monitor_name = i.monitor.name if i.monitor else None
        o.child_count = children.get(i.id, 0)
        out.append(o)
    return out


def _get(db: Session, incident_id: int) -> Incident:
    inc = db.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(404, "Incident not found")
    return inc


@router.get("/incidents", response_model=Page)
def list_incidents(
    status: str | None = Query(default=None, description="detected | investigating | resolved | open"),
    severity: str | None = None,
    component_id: int | None = None,
    q: str | None = Query(default=None, max_length=200),
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Page:
    query = select(Incident)
    if status == "open":
        query = query.where(Incident.status.in_(svc.OPEN_STATUSES))
    elif status:
        query = query.where(Incident.status == status)
    if severity:
        query = query.where(Incident.severity == severity)
    if component_id is not None:
        query = query.where(Incident.component_id == component_id)
    if q:
        like = f"%{q}%"
        query = query.where(or_(Incident.title.ilike(like), Incident.summary.ilike(like)))
    if start:
        query = query.where(or_(Incident.resolved_at.is_(None), Incident.resolved_at >= start))
    if end:
        query = query.where(Incident.started_at < end)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Incident.started_at.desc()).limit(limit).offset(offset)).all()
    return Page(total=total, items=[o.model_dump(mode="json") for o in to_out(db, list(rows))])


@router.post("/incidents", response_model=IncidentOut, status_code=201)
def declare_incident(
    body: IncidentCreate, user: User = Depends(require_operator), db: Session = Depends(get_db)
) -> IncidentOut:
    component = db.get(Component, body.component_id) if body.component_id else None
    if body.component_id and component is None:
        raise HTTPException(422, "Component not found")
    inc = svc.create_manual_incident(db, body.title, body.severity, component, body.summary, user.email)
    db.commit()
    return to_out(db, [inc])[0]


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(
    incident_id: int, _: User = Depends(require_viewer), db: Session = Depends(get_db)
) -> IncidentDetail:
    inc = _get(db, incident_id)
    base = to_out(db, [inc])[0]
    children = list(
        db.scalars(select(Incident).where(Incident.parent_id == inc.id).order_by(Incident.started_at))
    )
    parent = db.get(Incident, inc.parent_id) if inc.parent_id else None
    evidence: list[CheckResult] = []
    if inc.monitor_id:
        window_start = inc.started_at - timedelta(minutes=30)
        window_end = (inc.resolved_at or utcnow()) + timedelta(minutes=10)
        evidence = list(
            db.scalars(
                select(CheckResult)
                .where(
                    CheckResult.monitor_id == inc.monitor_id,
                    CheckResult.checked_at >= window_start,
                    CheckResult.checked_at <= window_end,
                )
                .order_by(CheckResult.checked_at.desc())
                .limit(200)
            )
        )
    affected = sorted(load_graph(db).downstream(inc.component_id)) if inc.component_id else []
    return IncidentDetail(
        **base.model_dump(),
        events=[IncidentEventOut.model_validate(e) for e in inc.events],
        children=to_out(db, children),
        parent=to_out(db, [parent])[0] if parent else None,
        evidence_results=[CheckResultOut.model_validate(r) for r in evidence],
        affected_components=affected,
    )


@router.post("/incidents/{incident_id}/acknowledge", response_model=IncidentOut)
def acknowledge(
    incident_id: int,
    body: IncidentAction,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> IncidentOut:
    inc = _get(db, incident_id)
    try:
        svc.acknowledge(db, inc, user.email, body.note)
    except svc.IncidentStateError as exc:
        raise HTTPException(409, str(exc)) from None
    db.commit()
    return to_out(db, [inc])[0]


@router.post("/incidents/{incident_id}/notes", response_model=IncidentEventOut, status_code=201)
def add_note(
    incident_id: int,
    body: IncidentNote,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> IncidentEventOut:
    inc = _get(db, incident_id)
    event = svc.add_note(db, inc, user.email, body.note)
    db.commit()
    return IncidentEventOut.model_validate(event)


@router.post("/incidents/{incident_id}/resolve", response_model=IncidentOut)
def resolve(
    incident_id: int,
    body: IncidentAction,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> IncidentOut:
    inc = _get(db, incident_id)
    try:
        svc.resolve(db, inc, user.email, body.note)
    except svc.IncidentStateError as exc:
        raise HTTPException(409, str(exc)) from None
    db.commit()
    return to_out(db, [inc])[0]


@router.post("/incidents/{incident_id}/reopen", response_model=IncidentOut)
def reopen(
    incident_id: int,
    body: IncidentAction,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> IncidentOut:
    inc = _get(db, incident_id)
    try:
        svc.reopen(db, inc, user.email, body.note)
    except svc.IncidentStateError as exc:
        raise HTTPException(409, str(exc)) from None
    db.commit()
    return to_out(db, [inc])[0]
