from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from cloudsentinel.models import Incident, IncidentStatus
from cloudsentinel.services import incidents as svc
from cloudsentinel.services.settings_store import update_app_settings
from cloudsentinel.services.topology import compute_health, load_graph
from tests.helpers import component, depends, feed, monitor

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_graph_traversal_and_cycles(db):
    web, api, dbc = component(db, "web"), component(db, "api"), component(db, "db")
    depends(db, web, api)
    depends(db, api, dbc)
    g = load_graph(db)
    assert g.upstream(web.id) == {api.id, dbc.id}
    assert g.downstream(dbc.id) == {web.id, api.id}
    assert g.would_create_cycle(dbc.id, web.id)
    assert g.would_create_cycle(web.id, web.id)
    assert not g.would_create_cycle(web.id, dbc.id)


def test_health_propagation(db):
    web, api, dbc, lonely = (component(db, n) for n in ("web", "api", "db", "lonely"))
    depends(db, web, api)
    depends(db, api, dbc)
    for c in (web, api, dbc):
        monitor(db, f"m-{c.name}", c, last_status="up")
    m_db = monitor(db, "m-db2", dbc, last_status="down")
    db.flush()
    h = compute_health(db)
    assert h[dbc.id].own_status == "down" and h[dbc.id].effective_status == "down"
    assert h[api.id].effective_status == "impacted" and h[api.id].impacted_by == [dbc.id]
    assert h[web.id].effective_status == "impacted"
    assert h[lonely.id].own_status == "unmonitored"
    m_db.last_status = "degraded"
    db.flush()
    assert compute_health(db)[api.id].effective_status == "up"


def test_incident_opens_after_threshold_and_auto_resolves(db):
    c = component(db, "api", criticality="critical")
    m = monitor(db, "api-health", c, failure_threshold=3, recovery_threshold=2)
    feed(db, m, "udd", T0)
    assert svc.open_incident_for_monitor(db, m.id) is None  # 2 failures < threshold 3
    feed(db, m, "d", T0 + timedelta(minutes=3))
    inc = svc.open_incident_for_monitor(db, m.id)
    assert inc is not None
    assert inc.severity == "critical" and inc.status == IncidentStatus.detected
    assert inc.started_at == T0 + timedelta(minutes=1)  # first failing check
    detected = inc.events[0]
    assert detected.kind == "detected" and len(detected.data["evidence"]) == 3

    feed(db, m, "u", T0 + timedelta(minutes=4))
    db.refresh(inc)
    assert inc.status != IncidentStatus.resolved  # recovery threshold is 2
    feed(db, m, "u", T0 + timedelta(minutes=5))
    db.refresh(inc)
    assert inc.status == IncidentStatus.resolved
    assert inc.resolved_by == "system"
    kinds = [e.kind for e in inc.events]
    assert kinds == ["detected", "check_recovering", "recovered", "resolved"]


def test_single_blip_never_creates_incident(db):
    m = monitor(db, "flappy", component(db, "svc"), failure_threshold=2)
    feed(db, m, "ududududu", T0)
    assert db.scalars(select(Incident)).all() == []


def test_unknown_results_do_not_reset_or_count(db):
    m = monitor(db, "m", component(db, "svc"), failure_threshold=2)
    feed(db, m, "dxd", T0)
    assert svc.open_incident_for_monitor(db, m.id) is not None


def test_degraded_opens_incident_only_when_enabled(db):
    m = monitor(db, "m", component(db, "svc"), failure_threshold=2)
    feed(db, m, "ggg", T0)
    assert svc.open_incident_for_monitor(db, m.id) is None
    m2 = monitor(db, "m2", component(db, "svc2"), failure_threshold=2, open_incident_on_degraded=True)
    feed(db, m2, "gg", T0)
    inc = svc.open_incident_for_monitor(db, m2.id)
    assert inc is not None and inc.severity == "minor" and "degraded" in inc.title


def test_auto_resolve_can_be_disabled(db):
    update_app_settings(db, {"auto_resolve_incidents": False})
    m = monitor(db, "m", component(db, "svc"))
    feed(db, m, "dduuuu", T0)
    inc = db.scalars(select(Incident)).one()
    assert inc.status == IncidentStatus.detected
    assert "recovered" in [e.kind for e in inc.events]


def test_correlation_upstream_first(db):
    api, cache = component(db, "api"), component(db, "cache")
    depends(db, api, cache)
    m_cache, m_api = monitor(db, "cache", cache), monitor(db, "api", api)
    feed(db, m_cache, "dd", T0)
    feed(db, m_api, "dd", T0 + timedelta(minutes=1))
    root = svc.open_incident_for_monitor(db, m_cache.id)
    child = svc.open_incident_for_monitor(db, m_api.id)
    assert child.parent_id == root.id
    assert any(e.kind == "correlated" and "Probably caused" in e.message for e in child.events)


def test_correlation_downstream_first_relinks(db):
    api, cache = component(db, "api"), component(db, "cache")
    depends(db, api, cache)
    m_cache, m_api = monitor(db, "cache", cache), monitor(db, "api", api)
    feed(db, m_api, "dd", T0)
    feed(db, m_cache, "dd", T0 + timedelta(minutes=1))
    child = svc.open_incident_for_monitor(db, m_api.id)
    root = svc.open_incident_for_monitor(db, m_cache.id)
    assert child.parent_id == root.id


def test_manual_lifecycle(db):
    inc = svc.create_manual_incident(db, "Storage latency", "major", None, "", "ops@example.com")
    svc.acknowledge(db, inc, "ops@example.com")
    assert inc.status == IncidentStatus.investigating and inc.acknowledged_by == "ops@example.com"
    svc.add_note(db, inc, "ops@example.com", "Vendor case opened")
    svc.resolve(db, inc, "ops@example.com", "Replaced failed controller")
    assert inc.status == IncidentStatus.resolved and inc.resolution_notes == "Replaced failed controller"
    import pytest

    with pytest.raises(svc.IncidentStateError):
        svc.resolve(db, inc, "x")
    with pytest.raises(svc.IncidentStateError):
        svc.acknowledge(db, inc, "x")
    svc.reopen(db, inc, "ops@example.com")
    assert inc.status == IncidentStatus.investigating and inc.resolved_at is None


def test_same_component_monitors_are_grouped(db):
    c = component(db, "web")
    m_http, m_tcp = monitor(db, "http", c), monitor(db, "tcp", c)
    feed(db, m_http, "dd", T0)
    feed(db, m_tcp, "dd", T0)
    first = svc.open_incident_for_monitor(db, m_http.id)
    second = svc.open_incident_for_monitor(db, m_tcp.id)
    assert second.parent_id == first.id
    assert any(e.data.get("relation") == "same_component" for e in first.events)
