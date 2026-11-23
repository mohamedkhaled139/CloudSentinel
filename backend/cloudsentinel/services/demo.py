"""Synthetic demo environment.

Creates a fictional multi-tier platform with dependencies and *synthetic* monitors, then backfills
a week of simulated history through the real monitoring/incident pipeline. Everything created here
is flagged ``is_synthetic=True`` and is shown with a "synthetic" badge in the UI.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from cloudsentinel.models import (
    CheckResult,
    Component,
    Dependency,
    Incident,
    Monitor,
    RiskFinding,
    utcnow,
)
from cloudsentinel.providers.synthetic import SyntheticConfig, simulate
from cloudsentinel.services import incidents as incident_service
from cloudsentinel.services.monitoring import record_outcome
from cloudsentinel.services.risk import run_analysis

# name, kind, criticality, environment, owner, description
COMPONENTS: list[tuple[str, str, str, str, str, str]] = [
    ("edge-load-balancer", "network", "critical", "prod", "netops", "Public L7 load balancer pair"),
    ("dns-resolvers", "network", "critical", "prod", "netops", "Internal recursive DNS"),
    ("api-gateway", "service", "critical", "prod", "platform", "Routes public API traffic"),
    ("auth-service", "service", "critical", "prod", "identity", "Token issuance and validation"),
    ("orders-service", "service", "high", "prod", "commerce", "Order lifecycle API"),
    ("catalog-service", "service", "medium", "prod", "commerce", "Product catalog API"),
    ("payments-service", "service", "critical", "prod", "payments", "Payment orchestration"),
    ("postgres-primary", "database", "critical", "prod", "dba", "Primary OLTP database"),
    ("redis-cache", "database", "high", "prod", "platform", "Session and response cache"),
    ("message-queue", "service", "high", "prod", "platform", "Event bus for async processing"),
    ("object-storage", "storage", "medium", "prod", "storage", "S3-compatible object store"),
    ("k8s-prod-cluster", "kubernetes", "critical", "prod", "platform", "Production Kubernetes cluster"),
    ("backup-server", "server", "high", "prod", "storage", "Nightly backup target (unmonitored)"),
]

# source depends on target
DEPENDENCIES: list[tuple[str, str]] = [
    ("edge-load-balancer", "dns-resolvers"),
    ("api-gateway", "edge-load-balancer"),
    ("api-gateway", "auth-service"),
    ("api-gateway", "orders-service"),
    ("api-gateway", "catalog-service"),
    ("orders-service", "postgres-primary"),
    ("orders-service", "redis-cache"),
    ("orders-service", "payments-service"),
    ("orders-service", "message-queue"),
    ("payments-service", "postgres-primary"),
    ("auth-service", "redis-cache"),
    ("auth-service", "postgres-primary"),
    ("catalog-service", "object-storage"),
    ("catalog-service", "redis-cache"),
    ("auth-service", "k8s-prod-cluster"),
    ("orders-service", "k8s-prod-cluster"),
    ("catalog-service", "k8s-prod-cluster"),
    ("payments-service", "k8s-prod-cluster"),
    ("postgres-primary", "backup-server"),
]

REDIS_OUTAGE = {"outage_minute_start": 20, "outage_minutes": 14, "outage_every_hours": 31}

# monitor name, component, config, extra monitor fields
MONITORS: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = [
    ("LB health endpoint", "edge-load-balancer", {"base_latency_ms": 18, "jitter_ms": 4}, {}),
    ("DNS resolution", "dns-resolvers", {"base_latency_ms": 4, "jitter_ms": 1}, {}),
    ("Gateway /healthz", "api-gateway", {"base_latency_ms": 45, "jitter_ms": 10, "failure_rate": 0.002}, {}),
    ("Auth token endpoint", "auth-service", {"base_latency_ms": 70, "jitter_ms": 15, **REDIS_OUTAGE}, {}),
    ("Orders API", "orders-service", {"base_latency_ms": 120, "jitter_ms": 30, **REDIS_OUTAGE}, {}),
    (
        "Catalog API",
        "catalog-service",
        {"base_latency_ms": 260, "jitter_ms": 40},
        {"degraded_latency_ms": 600},
    ),
    (
        "Payments health",
        "payments-service",
        {"base_latency_ms": 95, "jitter_ms": 20, "failure_rate": 0.03},
        {"failure_threshold": 2},
    ),
    ("Postgres TCP 5432", "postgres-primary", {"base_latency_ms": 2, "jitter_ms": 1}, {}),
    ("Redis TCP 6379", "redis-cache", {"base_latency_ms": 1.5, "jitter_ms": 0.5, **REDIS_OUTAGE}, {}),
    ("Queue broker", "message-queue", {"base_latency_ms": 8, "jitter_ms": 2, "degraded_rate": 0.01}, {}),
    ("Object storage", "object-storage", {"base_latency_ms": 140, "jitter_ms": 35}, {}),
    ("Cluster nodes", "k8s-prod-cluster", {"base_latency_ms": 30, "jitter_ms": 5}, {}),
]

# Catalog latency regression: the baseline before the last 24 hours was much lower.
CATALOG_BASELINE_MS = 90


def clear_demo_data(db: Session) -> None:
    db.execute(delete(RiskFinding).where(RiskFinding.is_synthetic.is_(True)))
    db.execute(delete(Incident).where(Incident.is_synthetic.is_(True)))
    synthetic_monitors = select(Monitor.id).where(Monitor.is_synthetic.is_(True))
    db.execute(delete(CheckResult).where(CheckResult.monitor_id.in_(synthetic_monitors)))
    db.execute(delete(Monitor).where(Monitor.is_synthetic.is_(True)))
    synthetic_components = select(Component.id).where(Component.is_synthetic.is_(True))
    db.execute(delete(Dependency).where(Dependency.source_id.in_(synthetic_components)))
    db.execute(delete(Component).where(Component.is_synthetic.is_(True)))
    db.flush()


def seed_demo(
    db: Session, days: int = 7, step_minutes: int = 5, seed: int = 42, now: datetime | None = None
) -> dict[str, int]:
    now = (now or utcnow()).replace(second=0, microsecond=0)
    clear_demo_data(db)
    comps: dict[str, Component] = {}
    for name, kind, crit, env, owner, desc in COMPONENTS:
        c = Component(
            name=name,
            kind=kind,
            criticality=crit,
            environment=env,
            owner=owner,
            description=desc,
            tags=["demo"],
            is_synthetic=True,
        )
        db.add(c)
        comps[name] = c
    db.flush()
    for src, tgt in DEPENDENCIES:
        db.add(Dependency(source_id=comps[src].id, target_id=comps[tgt].id))
    db.flush()

    monitors: list[Monitor] = []
    for name, comp, cfg, extra in MONITORS:
        m = Monitor(
            name=name,
            kind="synthetic",
            component_id=comps[comp].id,
            config=SyntheticConfig(**cfg).model_dump(),
            interval_seconds=60,
            timeout_seconds=5,
            failure_threshold=extra.get("failure_threshold", 2),
            recovery_threshold=2,
            degraded_latency_ms=extra.get("degraded_latency_ms"),
            is_synthetic=True,
        )
        db.add(m)
        monitors.append(m)
    db.flush()
    for m in monitors:
        db.refresh(m, ["component"])

    rng = random.Random(seed)
    start = now - timedelta(days=days)
    t = start
    last_t = start
    results = 0
    cutoff = now - timedelta(hours=24)
    while t < now:
        for m in monitors:
            sim_cfg = SyntheticConfig.model_validate(m.config)
            if m.name == "Catalog API" and t < cutoff:
                sim_cfg = sim_cfg.model_copy(update={"base_latency_ms": CATALOG_BASELINE_MS, "jitter_ms": 15})
            outcome = simulate(sim_cfg, t, rng)
            if (
                m.degraded_latency_ms
                and outcome.latency_ms
                and outcome.latency_ms > m.degraded_latency_ms
                and outcome.status == "up"
            ):
                outcome.status = "degraded"  # type: ignore[assignment]
            record_outcome(db, m, outcome, at=t)
            results += 1
        last_t = t
        t += timedelta(minutes=step_minutes)
    for m in monitors:
        m.next_run_at = None

    # Add realistic human activity to resolved incidents (acknowledgement + note), clearly synthetic.
    for inc in db.scalars(select(Incident).where(Incident.is_synthetic.is_(True))):
        if inc.resolved_at is not None and inc.parent_id is None:
            detected_at = min((e.at for e in inc.events if e.kind == "detected"), default=inc.started_at)
            inc.acknowledged_at = min(detected_at + timedelta(minutes=2), inc.resolved_at)
            inc.acknowledged_by = "demo-operator@example.invalid"
            incident_service.add_event(
                db,
                inc,
                "acknowledged",
                "Acknowledged (synthetic demo activity).",
                actor="demo-operator@example.invalid",
                at=inc.acknowledged_at,
            )
            incident_service.add_event(
                db,
                inc,
                "note",
                "Cache node failover observed; monitoring recovery (synthetic demo note).",
                actor="demo-operator@example.invalid",
                at=min(inc.acknowledged_at + timedelta(minutes=1), inc.resolved_at),
            )
    db.flush()
    # Analyse as of the last simulated check so that the backfill gap is not reported as stale data.
    risk_stats = run_analysis(db, now=last_t + timedelta(seconds=30), demo_mode=True)
    return {
        "components": len(comps),
        "dependencies": len(DEPENDENCIES),
        "monitors": len(monitors),
        "results": results,
        "incidents": len(db.scalars(select(Incident.id).where(Incident.is_synthetic.is_(True))).all()),
        "risks": risk_stats["created"],
    }
