import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from cloudsentinel.models import Integration


def test_components_dependencies_topology(client, operator):
    ids = {}
    for name in ("web", "api", "db"):
        r = client.post("/api/v1/components", json={"name": name, "criticality": "high"}, headers=operator)
        ids[name] = r.json()["id"]
    assert client.post("/api/v1/components", json={"name": "web"}, headers=operator).status_code == 409
    assert client.post("/api/v1/dependencies", json={"source_id": ids["web"], "target_id": ids["api"]},
                       headers=operator).status_code == 201
    assert client.post("/api/v1/dependencies", json={"source_id": ids["api"], "target_id": ids["db"]},
                       headers=operator).status_code == 201
    r = client.post("/api/v1/dependencies", json={"source_id": ids["db"], "target_id": ids["web"]}, headers=operator)
    assert r.status_code == 422 and "cycle" in r.json()["detail"]
    topo = client.get("/api/v1/topology", headers=operator).json()
    assert len(topo["nodes"]) == 3 and len(topo["edges"]) == 2
    web = next(n for n in topo["nodes"] if n["name"] == "web")
    assert web["depends_on"] == [ids["api"]] and web["health"]["own_status"] == "unmonitored"
    impact = client.get(f"/api/v1/components/{ids['db']}/impact", headers=operator).json()
    assert impact["downstream"] == sorted([ids["web"], ids["api"]])
    r = client.patch(f"/api/v1/components/{ids['db']}", json={"owner": "dba"}, headers=operator)
    assert r.json()["owner"] == "dba"
    assert client.delete(f"/api/v1/components/{ids['api']}", headers=operator).status_code == 204
    assert client.get("/api/v1/topology", headers=operator).json()["edges"] == []


def test_monitor_validation(client, operator):
    r = client.post("/api/v1/monitors", json={"name": "x", "kind": "nope"}, headers=operator)
    assert r.status_code == 422
    r = client.post("/api/v1/monitors", json={"name": "x", "kind": "http", "config": {"url": "not a url"}},
                    headers=operator)
    assert r.status_code == 422 and r.json()["detail"]["errors"]
    r = client.post("/api/v1/monitors", json={"name": "x", "kind": "prometheus", "config": {"query": "up"}},
                    headers=operator)
    assert r.status_code == 422 and "integration" in r.json()["detail"]
    r = client.post("/api/v1/monitors", json={"name": "x", "kind": "synthetic"}, headers=operator)
    assert r.status_code == 422 and "demo" in r.json()["detail"]
    r = client.post("/api/v1/monitors", json={"name": "x", "kind": "tcp", "interval_seconds": 1,
                                              "config": {"host": "h", "port": 1}}, headers=operator)
    assert r.status_code == 422


def test_monitor_run_now_results_and_stats(client, operator):
    async def start():
        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        return server

    # A plain listening socket is enough for the TCP check.
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    port = sock.getsockname()[1]
    try:
        r = client.post("/api/v1/monitors", json={"name": "local tcp", "kind": "tcp",
                                                  "config": {"host": "127.0.0.1", "port": port}}, headers=operator)
        assert r.status_code == 201, r.text
        mid = r.json()["id"]
        r = client.post(f"/api/v1/monitors/{mid}/run", headers=operator)
        assert r.status_code == 200 and r.json()["status"] == "up"
    finally:
        sock.close()
    r = client.post(f"/api/v1/monitors/{mid}/run", headers=operator)
    assert r.json()["status"] == "down"
    page = client.get(f"/api/v1/monitors/{mid}/results", headers=operator).json()
    assert page["total"] == 2 and page["items"][0]["status"] == "down"
    stats = client.get(f"/api/v1/monitors/{mid}/stats?hours=1", headers=operator).json()
    assert stats["checks"] == 2 and stats["availability_pct"] == 50.0 and len(stats["series"]) == 96
    m = client.get(f"/api/v1/monitors/{mid}", headers=operator).json()
    assert m["last_status"] == "down" and m["consecutive_failures"] == 1
    r = client.patch(f"/api/v1/monitors/{mid}", json={"enabled": False, "failure_threshold": 5}, headers=operator)
    assert r.json()["enabled"] is False and r.json()["failure_threshold"] == 5
    assert client.delete(f"/api/v1/monitors/{mid}", headers=operator).status_code == 204


def test_monitor_test_endpoint(client, operator):
    r = client.post("/api/v1/monitors/test", json={"name": "t", "kind": "tcp", "timeout_seconds": 1,
                                                   "config": {"host": "127.0.0.1", "port": 9}}, headers=operator)
    assert r.status_code == 200 and r.json()["status"] == "down"
    assert client.get("/api/v1/monitors", headers=operator).json() == []  # nothing persisted


def test_integration_secrets_never_returned(client, admin, db):
    r = client.post("/api/v1/integrations", json={
        "name": "prom", "kind": "prometheus", "config": {"base_url": "http://prom.invalid:9090"},
        "secrets": {"bearer_token": "super-secret-token"}}, headers=admin)
    assert r.status_code == 201, r.text
    body = r.json()
    assert "super-secret-token" not in r.text and body["secret_fields_set"] == ["bearer_token"]
    stored = db.scalars(select(Integration)).one()
    assert "super-secret-token" not in (stored.secret_encrypted or "")
    iid = body["id"]
    r = client.patch(f"/api/v1/integrations/{iid}", json={"name": "prom-main"}, headers=admin)
    assert r.json()["secret_fields_set"] == ["bearer_token"]  # kept when omitted
    r = client.patch(f"/api/v1/integrations/{iid}", json={"secrets": {"bearer_token": None}}, headers=admin)
    assert r.json()["secret_fields_set"] == []
    r = client.post(f"/api/v1/integrations/{iid}/test", headers=admin)
    assert r.status_code == 200 and r.json()["ok"] is False
    assert client.get("/api/v1/integrations", headers=admin).json()[0]["status"] == "error"
    bad = client.post("/api/v1/integrations", json={"name": "k", "kind": "kubernetes", "config": {}}, headers=admin)
    assert bad.status_code == 422


def test_monitor_requires_matching_integration(client, admin):
    iid = client.post("/api/v1/integrations", json={"name": "k8s", "kind": "kubernetes",
                                                    "config": {"api_server_url": "https://k8s.invalid"}},
                      headers=admin).json()["id"]
    r = client.post("/api/v1/monitors", json={"name": "q", "kind": "prometheus", "integration_id": iid,
                                              "config": {"query": "up"}}, headers=admin)
    assert r.status_code == 422
    r = client.post("/api/v1/monitors", json={"name": "nodes", "kind": "kubernetes", "integration_id": iid,
                                              "config": {"target": "nodes"}}, headers=admin)
    assert r.status_code == 201


def test_agent_registration_heartbeat_and_check(client, admin):
    r = client.post("/api/v1/agents", json={"name": "web-01"}, headers=admin)
    assert r.status_code == 201
    token, aid = r.json()["token"], r.json()["id"]
    assert "token" not in client.get("/api/v1/agents", headers=admin).json()[0]
    hb = {"hostname": "web-01.internal", "agent_version": "0.1.0", "boot_time": "2026-01-01T00:00:00Z",
          "load1": 0.4, "cpu_count": 4, "memory_used_pct": 40.0,
          "filesystems": [{"mount": "/", "used_pct": 91.0}, {"mount": "/data", "used_pct": 20.0}]}
    assert client.post("/api/v1/agent/heartbeat", json=hb).status_code == 401
    assert client.post("/api/v1/agent/heartbeat", json=hb, headers={"Authorization": "Bearer csa_wrong"}).status_code == 401
    assert client.post("/api/v1/agent/heartbeat", json=hb,
                       headers={"Authorization": f"Bearer {token}"}).status_code == 202
    mid = client.post("/api/v1/monitors", json={"name": "web-01 host", "kind": "agent",
                                                "config": {"agent_id": aid}}, headers=admin).json()["id"]
    res = client.post(f"/api/v1/monitors/{mid}/run", headers=admin).json()
    assert res["status"] == "degraded" and "disk 91%" in res["message"]
    agent = client.get("/api/v1/agents", headers=admin).json()[0]
    assert agent["hostname"] == "web-01.internal" and agent["last_seen_at"]


def test_incident_api_flow(client, operator):
    cid = client.post("/api/v1/components", json={"name": "san"}, headers=operator).json()["id"]
    r = client.post("/api/v1/incidents", json={"title": "SAN latency spike", "severity": "major",
                                               "component_id": cid}, headers=operator)
    assert r.status_code == 201
    iid = r.json()["id"]
    assert client.post(f"/api/v1/incidents/{iid}/acknowledge", json={}, headers=operator).json()["status"] == \
        "investigating"
    assert client.post(f"/api/v1/incidents/{iid}/notes", json={"note": "Vendor engaged"},
                       headers=operator).status_code == 201
    r = client.post(f"/api/v1/incidents/{iid}/resolve", json={"note": "Firmware rolled back"}, headers=operator)
    assert r.json()["status"] == "resolved" and r.json()["resolution_notes"] == "Firmware rolled back"
    assert client.post(f"/api/v1/incidents/{iid}/resolve", json={}, headers=operator).status_code == 409
    detail = client.get(f"/api/v1/incidents/{iid}", headers=operator).json()
    assert [e["kind"] for e in detail["events"]] == ["detected", "acknowledged", "note", "resolved"]
    page = client.get("/api/v1/incidents?status=resolved&q=SAN", headers=operator).json()
    assert page["total"] == 1
    assert client.get("/api/v1/incidents?status=open", headers=operator).json()["total"] == 0


def test_risks_reports_overview_settings(client, admin, operator):
    cid = client.post("/api/v1/components", json={"name": "core", "criticality": "critical"},
                      headers=admin).json()["id"]
    stats = client.post("/api/v1/risks/analyze", headers=operator).json()
    assert stats["created"] >= 1
    risks = client.get("/api/v1/risks", headers=operator).json()
    gap = next(r for r in risks if r["category"] == "monitoring_coverage_gap")
    assert gap["component_id"] == cid
    r = client.post(f"/api/v1/risks/{gap['id']}/dismiss", json={"note": "planned"}, headers=operator)
    assert r.json()["status"] == "dismissed"
    assert client.get("/api/v1/risks", headers=operator).json() == []
    assert client.post(f"/api/v1/risks/{gap['id']}/reopen", headers=operator).json()["status"] == "open"

    ov = client.get("/api/v1/overview", headers=operator).json()
    assert ov["components"]["total"] == 1 and len(ov["availability_trend"]) == 24
    assert ov["contains_synthetic_data"] is False

    now = datetime.now(UTC).isoformat()
    s = client.get("/api/v1/reports/summary", headers=operator).json()
    assert s["incident_count"] == 0
    assert client.get(f"/api/v1/reports/summary?start={now}&end=2020-01-01T00:00:00Z",
                      headers=operator).status_code == 422
    csv_r = client.get("/api/v1/reports/export.csv?section=components", headers=operator)
    assert csv_r.headers["content-type"].startswith("text/csv") and "core" in csv_r.text
    pdf = client.get("/api/v1/reports/export.pdf", headers=operator)
    assert pdf.content.startswith(b"%PDF") and "attachment" in pdf.headers["content-disposition"]

    assert client.get("/api/v1/settings", headers=operator).json()["risk_lookback_days"] == 7
    r = client.put("/api/v1/settings", json={"risk_lookback_days": 14, "retention_check_results_days": 60},
                   headers=admin)
    assert r.json()["risk_lookback_days"] == 14
    assert client.put("/api/v1/settings", json={"risk_lookback_days": 0}, headers=admin).status_code == 422
    assert client.put("/api/v1/settings", json={"unknown": 1}, headers=admin).status_code == 422
    assert client.post("/api/v1/settings/retention/run", headers=admin).status_code == 200
    audit = client.get("/api/v1/audit", headers=admin).json()
    assert any(a["action"] == "settings.updated" for a in audit)
    info = client.get("/api/v1/system/info", headers=operator).json()
    assert info["database"] == "sqlite" and info["demo_mode"] is False


def test_providers_listing_hides_synthetic_outside_demo(client, viewer):
    data = client.get("/api/v1/providers", headers=viewer).json()
    keys = {p["key"] for p in data["providers"]}
    assert {"http", "tcp", "prometheus", "kubernetes", "agent"} <= keys and "synthetic" not in keys
    assert {i["kind"] for i in data["integrations"]} == {"prometheus", "kubernetes"}
    http = next(p for p in data["providers"] if p["key"] == "http")
    assert "url" in http["config_schema"]["properties"]
