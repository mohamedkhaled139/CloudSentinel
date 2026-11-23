"""Risk intelligence: evidence-based detection of conditions that tend to precede outages.

Every finding carries the evidence it was derived from and a recommendation. Findings are *risks*,
not incidents: they describe patterns in recorded data and never claim that an outage occurred.
Findings are de-duplicated by fingerprint, refreshed on each analysis run, automatically resolved
when the condition no longer holds, and stay dismissed when an operator dismisses them.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cloudsentinel.models import (
    Agent,
    AgentReport,
    CheckResult,
    CheckStatus,
    Component,
    Criticality,
    Integration,
    Monitor,
    RiskFinding,
    RiskStatus,
    Severity,
    utcnow,
)
from cloudsentinel.providers.registry import registry
from cloudsentinel.services.settings_store import AppSettings, get_app_settings
from cloudsentinel.services.topology import load_graph

log = logging.getLogger(__name__)


class Category:
    recurring_failure = "recurring_failure"
    latency_regression = "latency_regression"
    availability_slo = "availability_below_target"
    restarts = "repeated_restarts"
    stale_monitor = "stale_monitoring_data"
    inconclusive = "inconclusive_monitor"
    coverage_gap = "monitoring_coverage_gap"
    integration_error = "integration_unreachable"
    tls_expiry = "tls_certificate_expiry"


@dataclass
class Finding:
    fingerprint: str
    category: str
    severity: str
    title: str
    description: str
    recommendation: str
    evidence: dict[str, Any] = field(default_factory=dict)
    component_id: int | None = None
    monitor_id: int | None = None
    is_synthetic: bool = False


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]


def _episodes(rows: list[tuple[datetime, str]]) -> list[dict[str, Any]]:
    """Group consecutive DOWN results into episodes."""
    episodes: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for at, status in rows:
        if status == CheckStatus.down:
            if current is None:
                current = {"start": at, "end": at, "checks": 0}
            current["end"] = at
            current["checks"] += 1
        elif status in (CheckStatus.up, CheckStatus.degraded) and current is not None:
            current["recovered_at"] = at
            episodes.append(current)
            current = None
    if current is not None:
        episodes.append(current)
    return episodes


def analyze(db: Session, now: datetime | None = None, demo_mode: bool = False) -> list[Finding]:
    now = now or utcnow()
    cfg = get_app_settings(db)
    since = now - timedelta(days=cfg.risk_lookback_days)
    findings: list[Finding] = []

    synthetic_kinds = {k for k, p in registry.providers.items() if p.synthetic}
    monitors = [
        m
        for m in db.scalars(select(Monitor).where(Monitor.enabled.is_(True)))
        if demo_mode or m.kind not in synthetic_kinds
    ]
    for m in monitors:
        rows = db.execute(
            select(CheckResult.checked_at, CheckResult.status, CheckResult.latency_ms, CheckResult.details)
            .where(CheckResult.monitor_id == m.id, CheckResult.checked_at >= since)
            .order_by(CheckResult.checked_at)
        ).all()
        findings.extend(_monitor_findings(m, rows, cfg, now))
        findings.extend(_stale_findings(m, cfg, now))

    findings.extend(_coverage_findings(db))
    findings.extend(_integration_findings(db))
    findings.extend(_agent_restart_findings(db, since, cfg))
    return findings


def _subject(m: Monitor) -> str:
    return f"{m.name}" + (f" ({m.component.name})" if m.component else "")


def _monitor_findings(m: Monitor, rows: Sequence[Any], cfg: AppSettings, now: datetime) -> list[Finding]:
    out: list[Finding] = []
    base: dict[str, Any] = {
        "component_id": m.component_id,
        "monitor_id": m.id,
        "is_synthetic": m.is_synthetic,
    }
    if not rows:
        return out

    # 1. Recurring failures
    episodes = _episodes([(r.checked_at, r.status) for r in rows])
    if len(episodes) >= cfg.recurring_failure_min_episodes:
        sev = Severity.major if len(episodes) >= 2 * cfg.recurring_failure_min_episodes else Severity.minor
        out.append(
            Finding(
                fingerprint=f"{Category.recurring_failure}:monitor:{m.id}",
                category=Category.recurring_failure,
                severity=sev,
                title=f"Recurring failures on {_subject(m)}",
                description=(
                    f"{len(episodes)} separate outage episodes were recorded in the last {cfg.risk_lookback_days} days. "
                    "Repeated short failures often indicate an unresolved underlying problem (resource exhaustion, "
                    "flapping network path, failing dependency) that can escalate into a prolonged outage."
                ),
                recommendation="Review the episodes for a common time pattern or trigger, check the component's logs "
                "around each start time, and consider a post-incident review even if each episode self-recovered.",
                evidence={
                    "episodes": [
                        {
                            "start": e["start"].isoformat(),
                            "end": e["end"].isoformat(),
                            "failed_checks": e["checks"],
                        }
                        for e in episodes[-20:]
                    ],
                    "episode_count": len(episodes),
                    "window_days": cfg.risk_lookback_days,
                },
                **base,
            )
        )

    # 2. Availability below target
    known = [r for r in rows if r.status != CheckStatus.unknown]
    if len(known) >= cfg.latency_min_samples:
        available = sum(1 for r in known if r.status in (CheckStatus.up, CheckStatus.degraded))
        pct = 100.0 * available / len(known)
        if pct < cfg.availability_target_pct:
            sev = Severity.major if pct < cfg.availability_target_pct - 1 else Severity.minor
            out.append(
                Finding(
                    fingerprint=f"{Category.availability_slo}:monitor:{m.id}",
                    category=Category.availability_slo,
                    severity=sev,
                    title=f"Availability of {_subject(m)} below {cfg.availability_target_pct:g}% target",
                    description=f"Measured availability over the last {cfg.risk_lookback_days} days is {pct:.3f}% "
                    f"({len(known) - available} failed of {len(known)} checks).",
                    recommendation="Investigate the failure periods listed in the monitor history, and either address "
                    "the cause or revisit the availability target if it is not realistic for this component.",
                    evidence={
                        "availability_pct": round(pct, 4),
                        "checks": len(known),
                        "failed": len(known) - available,
                        "target_pct": cfg.availability_target_pct,
                    },
                    **base,
                )
            )

    # 3. Latency regression: last 24h vs the rest of the window
    split = now - timedelta(hours=24)
    recent = [
        r.latency_ms
        for r in rows
        if r.latency_ms is not None and r.checked_at >= split and r.status != CheckStatus.down
    ]
    baseline = [
        r.latency_ms
        for r in rows
        if r.latency_ms is not None and r.checked_at < split and r.status != CheckStatus.down
    ]
    if len(recent) >= cfg.latency_min_samples and len(baseline) >= cfg.latency_min_samples:
        recent_p50, base_p50 = statistics.median(recent), statistics.median(baseline)
        recent_p95, base_p95 = _pct(recent, 0.95), _pct(baseline, 0.95)
        ratio = recent_p50 / base_p50 if base_p50 > 0 else 0
        if ratio >= cfg.latency_regression_ratio and recent_p50 - base_p50 >= 5:
            out.append(
                Finding(
                    fingerprint=f"{Category.latency_regression}:monitor:{m.id}",
                    category=Category.latency_regression,
                    severity=Severity.major if ratio >= 3 else Severity.minor,
                    title=f"Response time of {_subject(m)} increased {ratio:.1f}x",
                    description=f"Median response time over the last 24 hours is {recent_p50:.0f} ms versus a baseline "
                    f"of {base_p50:.0f} ms (p95 {recent_p95:.0f} ms vs {base_p95:.0f} ms). Sustained latency growth "
                    "frequently precedes saturation-related outages.",
                    recommendation="Check recent deployments or configuration changes, resource saturation (CPU, "
                    "memory, connection pools) and the latency of this component's dependencies.",
                    evidence={
                        "recent_p50_ms": round(recent_p50, 1),
                        "baseline_p50_ms": round(base_p50, 1),
                        "recent_p95_ms": round(recent_p95, 1),
                        "baseline_p95_ms": round(base_p95, 1),
                        "recent_samples": len(recent),
                        "baseline_samples": len(baseline),
                        "ratio": round(ratio, 2),
                    },
                    **base,
                )
            )

    # 4. Kubernetes pod restarts (restart counters are monotonic per pod)
    first_seen: dict[str, int] = {}
    last_seen: dict[str, int] = {}
    for r in rows:
        restarts = (r.details or {}).get("restarts")
        if isinstance(restarts, dict):
            for pod, count in restarts.items():
                first_seen.setdefault(pod, int(count))
                last_seen[pod] = int(count)
    increases = {
        pod: last_seen[pod] - first_seen[pod] for pod in last_seen if last_seen[pod] > first_seen[pod]
    }
    total = sum(increases.values())
    if total >= cfg.restart_min_count:
        out.append(
            Finding(
                fingerprint=f"{Category.restarts}:monitor:{m.id}",
                category=Category.restarts,
                severity=Severity.major if total >= 3 * cfg.restart_min_count else Severity.minor,
                title=f"Repeated container restarts in {_subject(m)}",
                description=f"Containers restarted {total} times in the last {cfg.risk_lookback_days} days according "
                "to Kubernetes restart counters. Restart loops reduce capacity and often hide crashes or OOM kills.",
                recommendation="Inspect `kubectl describe pod` and previous container logs for OOMKilled or crash "
                "reasons; review memory limits and liveness probe settings.",
                evidence={
                    "restart_increase_by_pod": dict(sorted(increases.items(), key=lambda x: -x[1])[:20]),
                    "total_restarts": total,
                },
                **base,
            )
        )

    # 5. TLS certificate expiry
    last = rows[-1]
    days = (last.details or {}).get("tls_days_remaining")
    if isinstance(days, (int, float)) and days < cfg.tls_expiry_risk_days:
        out.append(
            Finding(
                fingerprint=f"{Category.tls_expiry}:monitor:{m.id}",
                category=Category.tls_expiry,
                severity=Severity.critical if days < 3 else Severity.major if days < 7 else Severity.minor,
                title=f"TLS certificate for {_subject(m)} expires in {days:.0f} days",
                description=f"The certificate presented by {(last.details or {}).get('url', 'the endpoint')} is valid "
                f"until {(last.details or {}).get('tls_not_after')}.",
                recommendation="Renew the certificate and verify that automated renewal (e.g. ACME/cert-manager) is "
                "working for this endpoint.",
                evidence={
                    "tls_days_remaining": days,
                    "tls_not_after": (last.details or {}).get("tls_not_after"),
                },
                **base,
            )
        )

    # 6. Inconclusive: the latest results could not produce a verdict
    tail = rows[-5:]
    if len(tail) == 5 and all(r.status == CheckStatus.unknown for r in tail):
        out.append(
            Finding(
                fingerprint=f"{Category.inconclusive}:monitor:{m.id}",
                category=Category.inconclusive,
                severity=Severity.minor,
                title=f"Monitor {_subject(m)} cannot determine health",
                description="The last 5 checks returned no verdict (configuration or data-source error), so this "
                "component is effectively unmonitored.",
                recommendation="Open the monitor history to see the error message and fix the configuration or the "
                "integration it depends on.",
                evidence={"last_checked_at": tail[-1].checked_at.isoformat()},
                **base,
            )
        )
    return out


def _stale_findings(m: Monitor, cfg: AppSettings, now: datetime) -> list[Finding]:
    limit = timedelta(seconds=m.interval_seconds * cfg.stale_interval_multiplier + 60)
    reference = m.last_checked_at or m.created_at
    if now - reference <= limit:
        return []
    return [
        Finding(
            fingerprint=f"{Category.stale_monitor}:monitor:{m.id}",
            category=Category.stale_monitor,
            severity=Severity.major,
            title=f"No fresh data for {_subject(m)}",
            description=(
                f"The last result was recorded at {m.last_checked_at.isoformat()}"
                if m.last_checked_at
                else "This monitor has never produced a result"
            )
            + f", but it is scheduled every {m.interval_seconds}s. Its displayed status may be out of date.",
            recommendation="Verify that the CloudSentinel scheduler is running (see /readyz) and that the check is "
            "not hanging.",
            evidence={
                "last_checked_at": m.last_checked_at.isoformat() if m.last_checked_at else None,
                "interval_seconds": m.interval_seconds,
            },
            component_id=m.component_id,
            monitor_id=m.id,
            is_synthetic=m.is_synthetic,
        )
    ]


def _coverage_findings(db: Session) -> list[Finding]:
    out: list[Finding] = []
    graph = load_graph(db)
    monitored = set(
        db.scalars(
            select(Monitor.component_id).where(Monitor.enabled.is_(True), Monitor.component_id.is_not(None))
        )
    )
    for comp in db.scalars(select(Component)):
        if comp.id in monitored:
            continue
        dependents = graph.downstream(comp.id)
        if comp.criticality in (Criticality.high, Criticality.critical) or len(dependents) >= 2:
            out.append(
                Finding(
                    fingerprint=f"{Category.coverage_gap}:component:{comp.id}",
                    category=Category.coverage_gap,
                    severity=Severity.major
                    if comp.criticality == Criticality.critical or len(dependents) >= 3
                    else Severity.minor,
                    title=f"{comp.name} has no active monitoring",
                    description=f"{comp.name} is marked {comp.criticality} criticality and {len(dependents)} components "
                    "depend on it, but no enabled monitor covers it. Failures would only be noticed through their "
                    "effects on dependents.",
                    recommendation="Add at least one direct health check (HTTP, TCP, agent, Kubernetes or Prometheus) "
                    "for this component.",
                    evidence={"dependent_component_ids": sorted(dependents), "criticality": comp.criticality},
                    component_id=comp.id,
                    is_synthetic=comp.is_synthetic,
                )
            )
    return out


def _integration_findings(db: Session) -> list[Finding]:
    out: list[Finding] = []
    for integ in db.scalars(
        select(Integration).where(Integration.enabled.is_(True), Integration.status == "error")
    ):
        count = db.scalar(select(func.count(Monitor.id)).where(Monitor.integration_id == integ.id)) or 0
        out.append(
            Finding(
                fingerprint=f"{Category.integration_error}:integration:{integ.id}",
                category=Category.integration_error,
                severity=Severity.major if count else Severity.minor,
                title=f"Integration '{integ.name}' is unreachable",
                description=f"The last connection test failed: {integ.last_error}. {count} monitors rely on it and "
                "cannot evaluate health while it is unavailable.",
                recommendation="Check network reachability and credentials for this integration on the Integrations page.",
                evidence={
                    "last_error": integ.last_error,
                    "last_checked_at": integ.last_checked_at.isoformat() if integ.last_checked_at else None,
                    "dependent_monitors": count,
                },
            )
        )
    return out


def _agent_restart_findings(db: Session, since: datetime, cfg: AppSettings) -> list[Finding]:
    out: list[Finding] = []
    for agent in db.scalars(select(Agent)):
        boots = sorted(
            {
                bt
                for bt in db.scalars(
                    select(AgentReport.boot_time).where(
                        AgentReport.agent_id == agent.id,
                        AgentReport.received_at >= since,
                        AgentReport.boot_time.is_not(None),
                    )
                )
                if bt is not None
            }
        )
        # Collapse clock jitter: boot times within 2 minutes are the same boot.
        distinct: list[datetime] = []
        for bt in boots:
            if not distinct or (bt - distinct[-1]).total_seconds() > 120:
                distinct.append(bt)
        reboots = len(distinct) - 1
        if reboots >= cfg.restart_min_count:
            out.append(
                Finding(
                    fingerprint=f"{Category.restarts}:agent:{agent.id}",
                    category=Category.restarts,
                    severity=Severity.major,
                    title=f"Host {agent.hostname or agent.name} rebooted {reboots} times",
                    description=f"The agent reported {reboots} reboots in the last {cfg.risk_lookback_days} days. "
                    "Unplanned reboots can indicate kernel panics, hardware faults, or power issues.",
                    recommendation="Review `journalctl --list-boots`, kernel logs and hardware/BMC event logs.",
                    evidence={"boot_times": [b.isoformat() for b in distinct[-20:]]},
                )
            )
    return out


def run_analysis(db: Session, now: datetime | None = None, demo_mode: bool = False) -> dict[str, int]:
    """Analyze and persist findings. Returns counts of created/updated/resolved findings."""
    now = now or utcnow()
    findings = analyze(db, now, demo_mode)
    current = {f.fingerprint: f for f in findings}
    existing = {
        r.fingerprint: r
        for r in db.scalars(select(RiskFinding).where(RiskFinding.status != RiskStatus.resolved))
    }
    stats = {"created": 0, "updated": 0, "resolved": 0}
    for fp, f in current.items():
        row = existing.get(fp)
        if row is None:
            db.add(
                RiskFinding(
                    fingerprint=fp,
                    category=f.category,
                    severity=f.severity,
                    title=f.title,
                    description=f.description,
                    recommendation=f.recommendation,
                    evidence=f.evidence,
                    component_id=f.component_id,
                    monitor_id=f.monitor_id,
                    is_synthetic=f.is_synthetic,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            stats["created"] += 1
        else:
            row.severity, row.title, row.description = f.severity, f.title, f.description
            row.recommendation, row.evidence, row.last_seen_at = f.recommendation, f.evidence, now
            stats["updated"] += 1
    for fp, row in existing.items():
        if fp not in current:
            row.status = RiskStatus.resolved
            row.resolved_at = now
            stats["resolved"] += 1
    db.flush()
    log.info("risk analysis complete", extra={f"findings_{k}": v for k, v in stats.items()})
    return stats
