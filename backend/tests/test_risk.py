from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from cloudsentinel.models import Agent, AgentReport, CheckResult, Integration, RiskFinding, RiskStatus
from cloudsentinel.services.risk import Category, analyze, run_analysis
from tests.helpers import component, depends, feed, monitor

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def categories(findings):
    return {f.category for f in findings}


def test_recurring_failures_detected(db):
    m = monitor(db, "flaky", component(db, "svc"), interval_seconds=60)
    start = NOW - timedelta(hours=10)
    feed(db, m, ("u" * 20 + "dd") * 4 + "u" * 5, start)
    m.last_checked_at = NOW
    found = [f for f in analyze(db, NOW) if f.category == Category.recurring_failure]
    assert len(found) == 1
    assert found[0].evidence["episode_count"] == 4
    assert found[0].monitor_id == m.id


def test_latency_regression(db):
    m = monitor(db, "api", component(db, "api"), interval_seconds=600)
    t = NOW - timedelta(days=5)
    while t < NOW - timedelta(hours=24):
        feed(db, m, "u", t, latency=100)
        t += timedelta(hours=1)
    t = NOW - timedelta(hours=23)
    while t < NOW:
        feed(db, m, "u", t, latency=350)
        t += timedelta(minutes=30)
    m.last_checked_at = NOW
    found = [f for f in analyze(db, NOW) if f.category == Category.latency_regression]
    assert len(found) == 1
    assert found[0].evidence["ratio"] == 3.5
    assert found[0].severity == "major"


def test_no_latency_finding_with_insufficient_samples(db):
    m = monitor(db, "api", component(db, "api"))
    feed(db, m, "u" * 5, NOW - timedelta(hours=2), latency=500)
    m.last_checked_at = NOW
    assert Category.latency_regression not in categories(analyze(db, NOW))


def test_stale_and_inconclusive(db):
    m = monitor(db, "stale", component(db, "a"), interval_seconds=60)
    feed(db, m, "uuuuu", NOW - timedelta(hours=1))
    m2 = monitor(db, "broken", component(db, "b"), interval_seconds=60)
    feed(db, m2, "xxxxx", NOW - timedelta(minutes=5))
    m2.last_checked_at = NOW
    found = {(f.category, f.monitor_id) for f in analyze(db, NOW)}
    assert (Category.stale_monitor, m.id) in found
    assert (Category.inconclusive, m2.id) in found
    assert (Category.stale_monitor, m2.id) not in found


def test_coverage_gap_for_critical_or_shared_components(db):
    core = component(db, "core-db", criticality="critical")
    shared = component(db, "shared")
    a, b = component(db, "a"), component(db, "b")
    depends(db, a, shared)
    depends(db, b, shared)
    for c in (a, b):
        mm = monitor(db, f"m-{c.name}", c)
        mm.last_checked_at = NOW
    gaps = {f.component_id for f in analyze(db, NOW) if f.category == Category.coverage_gap}
    assert gaps == {core.id, shared.id}


def test_kubernetes_restart_increase(db):
    m = monitor(db, "deploy", component(db, "k8s"), kind="kubernetes")
    for i, restarts in enumerate([0, 1, 1, 4, 6]):
        db.add(CheckResult(monitor_id=m.id, checked_at=NOW - timedelta(hours=5 - i), status="up",
                           details={"restarts": {"api-1": restarts, "api-2": 2}}))
    m.last_checked_at = NOW
    db.flush()
    found = [f for f in analyze(db, NOW) if f.category == Category.restarts]
    assert len(found) == 1 and found[0].evidence["total_restarts"] == 6


def test_agent_reboots(db):
    agent = Agent(name="host-1", token_hash="h", token_prefix="csa_")
    db.add(agent)
    db.flush()
    for day in range(4):
        boot = NOW - timedelta(days=3 - day, hours=1)
        db.add(AgentReport(agent_id=agent.id, received_at=boot + timedelta(minutes=5), boot_time=boot))
        db.add(AgentReport(agent_id=agent.id, received_at=boot + timedelta(minutes=10),
                           boot_time=boot + timedelta(seconds=1)))  # jitter
    db.flush()
    found = [f for f in analyze(db, NOW) if f.category == Category.restarts]
    assert len(found) == 1 and "rebooted 3 times" in found[0].title


def test_tls_expiry(db):
    m = monitor(db, "web", component(db, "web"), kind="http")
    db.add(CheckResult(monitor_id=m.id, checked_at=NOW, status="up", latency_ms=10,
                       details={"tls_days_remaining": 5.0, "tls_not_after": "2026-03-15T00:00:00+00:00"}))
    m.last_checked_at = NOW
    db.flush()
    found = [f for f in analyze(db, NOW) if f.category == Category.tls_expiry]
    assert found and found[0].severity == "major"


def test_integration_error(db):
    db.add(Integration(name="prom", kind="prometheus", config={}, status="error", last_error="connection refused"))
    db.flush()
    assert Category.integration_error in categories(analyze(db, NOW))


def test_persistence_dedup_dismiss_and_autoresolve(db):
    m = monitor(db, "stale", component(db, "a"), interval_seconds=60)
    feed(db, m, "u", NOW - timedelta(hours=2))
    assert run_analysis(db, NOW)["created"] == 1
    assert run_analysis(db, NOW)["created"] == 0  # de-duplicated
    row = db.scalars(select(RiskFinding)).one()
    row.status = RiskStatus.dismissed
    db.flush()
    run_analysis(db, NOW)
    assert db.scalars(select(RiskFinding)).one().status == RiskStatus.dismissed  # dismissal respected
    m.last_checked_at = NOW
    stats = run_analysis(db, NOW)
    assert stats["resolved"] == 1
    assert db.scalars(select(RiskFinding)).one().status == RiskStatus.resolved
