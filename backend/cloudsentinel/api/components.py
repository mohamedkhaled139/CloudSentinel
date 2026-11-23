"""Components (services and infrastructure), dependencies and topology."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cloudsentinel.db import get_db
from cloudsentinel.deps import audit, require_operator, require_viewer
from cloudsentinel.models import (
    CheckResult,
    CheckStatus,
    Component,
    Dependency,
    Incident,
    Monitor,
    User,
    utcnow,
)
from cloudsentinel.schemas import (
    ComponentCreate,
    ComponentHealthOut,
    ComponentOut,
    ComponentUpdate,
    DependencyCreate,
    DependencyOut,
    ImpactOut,
    TopologyOut,
)
from cloudsentinel.services.incidents import OPEN_STATUSES
from cloudsentinel.services.topology import compute_health, load_graph

router = APIRouter(tags=["components"])


def _enrich(db: Session, components: list[Component]) -> list[ComponentOut]:
    graph = load_graph(db)
    health = compute_health(db, graph)
    open_counts = Counter(
        db.scalars(
            select(Incident.component_id).where(
                Incident.status.in_(OPEN_STATUSES), Incident.component_id.is_not(None)
            )
        )
    )
    since = utcnow() - timedelta(hours=24)
    failed = case((CheckResult.status == CheckStatus.down, 1), else_=0)
    avail: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for cid, n, f in db.execute(
        select(Monitor.component_id, func.count(CheckResult.id), func.sum(failed))
        .join(Monitor, Monitor.id == CheckResult.monitor_id)
        .where(
            CheckResult.checked_at >= since,
            CheckResult.status != CheckStatus.unknown,
            Monitor.component_id.is_not(None),
        )
        .group_by(Monitor.component_id)
    ):
        avail[cid] = [int(n), int(f or 0)]

    out = []
    for c in components:
        item = ComponentOut.model_validate(c)
        h = health.get(c.id)
        if h:
            item.health = ComponentHealthOut(**{k: v for k, v in h.as_dict().items() if k != "component_id"})
        item.depends_on = sorted(graph.deps.get(c.id, set()))
        item.dependents = sorted(graph.dependents.get(c.id, set()))
        item.open_incidents = open_counts.get(c.id, 0)
        n, f = avail.get(c.id, [0, 0])
        item.availability_24h = round(100 * (n - f) / n, 3) if n else None
        out.append(item)
    return out


def _get(db: Session, component_id: int) -> Component:
    comp = db.get(Component, component_id)
    if comp is None:
        raise HTTPException(404, "Component not found")
    return comp


@router.get("/components", response_model=list[ComponentOut])
def list_components(_: User = Depends(require_viewer), db: Session = Depends(get_db)) -> list[ComponentOut]:
    return _enrich(db, list(db.scalars(select(Component).order_by(Component.name))))


@router.post("/components", response_model=ComponentOut, status_code=201)
def create_component(
    body: ComponentCreate, user: User = Depends(require_operator), db: Session = Depends(get_db)
) -> ComponentOut:
    comp = Component(**body.model_dump())
    db.add(comp)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "A component with this name already exists") from None
    audit(db, user, "component.created", comp.name)
    db.commit()
    return _enrich(db, [comp])[0]


@router.get("/components/{component_id}", response_model=ComponentOut)
def get_component(
    component_id: int, _: User = Depends(require_viewer), db: Session = Depends(get_db)
) -> ComponentOut:
    return _enrich(db, [_get(db, component_id)])[0]


@router.patch("/components/{component_id}", response_model=ComponentOut)
def update_component(
    component_id: int,
    body: ComponentUpdate,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> ComponentOut:
    comp = _get(db, component_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(comp, key, value)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "A component with this name already exists") from None
    audit(db, user, "component.updated", comp.name)
    db.commit()
    return _enrich(db, [comp])[0]


@router.delete("/components/{component_id}", status_code=204)
def delete_component(
    component_id: int, user: User = Depends(require_operator), db: Session = Depends(get_db)
) -> Response:
    comp = _get(db, component_id)
    db.execute(
        delete(Dependency).where(
            (Dependency.source_id == component_id) | (Dependency.target_id == component_id)
        )
    )
    db.delete(comp)
    audit(db, user, "component.deleted", comp.name)
    db.commit()
    return Response(status_code=204)


@router.get("/topology", response_model=TopologyOut)
def topology(_: User = Depends(require_viewer), db: Session = Depends(get_db)) -> TopologyOut:
    nodes = _enrich(db, list(db.scalars(select(Component).order_by(Component.name))))
    edges = [DependencyOut.model_validate(d) for d in db.scalars(select(Dependency))]
    return TopologyOut(nodes=nodes, edges=edges)


@router.get("/components/{component_id}/impact", response_model=ImpactOut)
def impact(component_id: int, _: User = Depends(require_viewer), db: Session = Depends(get_db)) -> ImpactOut:
    _get(db, component_id)
    graph = load_graph(db)
    return ImpactOut(
        component_id=component_id,
        upstream=sorted(graph.upstream(component_id)),
        downstream=sorted(graph.downstream(component_id)),
    )


@router.post("/dependencies", response_model=DependencyOut, status_code=201)
def create_dependency(
    body: DependencyCreate, user: User = Depends(require_operator), db: Session = Depends(get_db)
) -> Dependency:
    src, tgt = _get(db, body.source_id), _get(db, body.target_id)
    graph = load_graph(db)
    if graph.would_create_cycle(src.id, tgt.id):
        raise HTTPException(422, "This dependency would create a cycle in the topology")
    dep = Dependency(source_id=src.id, target_id=tgt.id, description=body.description)
    db.add(dep)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Dependency already exists") from None
    audit(db, user, "dependency.created", f"{src.name} -> {tgt.name}")
    db.commit()
    return dep


@router.delete("/dependencies/{dependency_id}", status_code=204)
def delete_dependency(
    dependency_id: int, user: User = Depends(require_operator), db: Session = Depends(get_db)
) -> Response:
    dep = db.get(Dependency, dependency_id)
    if dep is None:
        raise HTTPException(404, "Dependency not found")
    db.delete(dep)
    audit(db, user, "dependency.deleted", str(dependency_id))
    db.commit()
    return Response(status_code=204)
