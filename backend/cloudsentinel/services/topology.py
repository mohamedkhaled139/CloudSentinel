"""Dependency graph operations and health propagation."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cloudsentinel.models import CheckStatus, Component, Dependency, Monitor

# Order used to pick the "worst" status.
_RANK = {"down": 3, "degraded": 2, "up": 1, "unknown": 0}


def worst(statuses: list[str]) -> str:
    known = [s for s in statuses if s != CheckStatus.unknown]
    if not known:
        return CheckStatus.unknown
    return max(known, key=lambda s: _RANK.get(s, 0))


@dataclass
class Graph:
    # deps[a] = components that a depends on; dependents[b] = components depending on b
    deps: dict[int, set[int]] = field(default_factory=lambda: defaultdict(set))
    dependents: dict[int, set[int]] = field(default_factory=lambda: defaultdict(set))

    def add(self, source: int, target: int) -> None:
        self.deps[source].add(target)
        self.dependents[target].add(source)

    def _walk(self, start: int, edges: dict[int, set[int]]) -> set[int]:
        seen: set[int] = set()
        queue = deque(edges.get(start, ()))
        while queue:
            node = queue.popleft()
            if node in seen or node == start:
                continue
            seen.add(node)
            queue.extend(edges.get(node, ()))
        return seen

    def upstream(self, cid: int) -> set[int]:
        """Everything ``cid`` (transitively) depends on."""
        return self._walk(cid, self.deps)

    def downstream(self, cid: int) -> set[int]:
        """Everything that (transitively) depends on ``cid`` — the blast radius."""
        return self._walk(cid, self.dependents)

    def would_create_cycle(self, source: int, target: int) -> bool:
        return source == target or source in self.upstream(target)


def load_graph(db: Session) -> Graph:
    g = Graph()
    for src, tgt in db.execute(select(Dependency.source_id, Dependency.target_id)):
        g.add(src, tgt)
    return g


@dataclass
class ComponentHealth:
    component_id: int
    own_status: str  # from the component's own monitors; "unmonitored" when it has none
    effective_status: str  # own status, or "impacted" if an upstream dependency is down
    impacted_by: list[int]  # upstream components that are down
    monitor_count: int

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def compute_health(db: Session, graph: Graph | None = None) -> dict[int, ComponentHealth]:
    graph = graph or load_graph(db)
    by_component: dict[int, list[str]] = defaultdict(list)
    for cid, status in db.execute(
        select(Monitor.component_id, Monitor.last_status).where(
            Monitor.enabled.is_(True), Monitor.component_id.is_not(None)
        )
    ):
        if cid is not None:
            by_component[cid].append(status)

    own: dict[int, str] = {}
    counts: dict[int, int] = {}
    for (cid,) in db.execute(select(Component.id)):
        statuses = by_component.get(cid, [])
        counts[cid] = len(statuses)
        own[cid] = worst(statuses) if statuses else "unmonitored"

    result: dict[int, ComponentHealth] = {}
    for cid, status in own.items():
        down_upstream = sorted(u for u in graph.upstream(cid) if own.get(u) == CheckStatus.down)
        effective = status
        if down_upstream and status in (
            CheckStatus.up,
            CheckStatus.degraded,
            CheckStatus.unknown,
            "unmonitored",
        ):
            effective = "impacted"
        result[cid] = ComponentHealth(cid, status, effective, down_upstream, counts[cid])
    return result
