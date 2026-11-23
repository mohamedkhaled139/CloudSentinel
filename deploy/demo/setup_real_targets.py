"""Registers REAL (non-synthetic) monitors in the demo stack once CloudSentinel is up.

These monitors check actual containers (nginx and Prometheus) so the demo shows real data next to
the synthetic environment.
"""

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"


def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


for _ in range(60):
    try:
        token = call("POST", "/auth/login", {"email": "demo@example.com", "password": "demo-password-123"})["access_token"]
        break
    except Exception:
        time.sleep(2)
else:
    raise SystemExit("CloudSentinel did not start")

if not any(not m["is_synthetic"] for m in call("GET", "/monitors", token=token)):
    web = call("POST", "/components", {"name": "demo-nginx (real)", "kind": "service", "criticality": "low",
                                       "description": "Real nginx container in the demo stack"}, token)
    prom_c = call("POST", "/components", {"name": "demo-prometheus (real)", "kind": "service",
                                          "criticality": "low"}, token)
    call("POST", "/monitors", {"name": "nginx HTTP", "kind": "http", "component_id": web["id"], "interval_seconds": 30,
                               "config": {"url": "http://web/"}}, token)
    call("POST", "/monitors", {"name": "nginx TCP 80", "kind": "tcp", "component_id": web["id"], "interval_seconds": 30,
                               "config": {"host": "web", "port": 80}}, token)
    integ = call("POST", "/integrations", {"name": "demo-prometheus", "kind": "prometheus",
                                           "config": {"base_url": "http://prometheus:9090"}}, token)
    call("POST", "/monitors", {"name": "Prometheus targets up", "kind": "prometheus", "component_id": prom_c["id"],
                               "integration_id": integ["id"], "interval_seconds": 30,
                               "config": {"query": "up", "comparison": "<", "down_threshold": 1}}, token)
    print("Registered real demo monitors")
