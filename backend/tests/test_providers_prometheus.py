import asyncio

import httpx

from cloudsentinel.models import CheckStatus
from cloudsentinel.providers.base import CheckContext, IntegrationContext
from cloudsentinel.providers.prometheus import PrometheusAdapter, PrometheusProvider, PrometheusQueryConfig

INTEGRATION = IntegrationContext(1, "prom", "prometheus", {"base_url": "http://prom.test:9090"},
                                 {"bearer_token": "tok"})


def vector(*values, name="up"):
    return {"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": {"__name__": name, "instance": f"node{i}"}, "value": [0, str(v)]} for i, v in enumerate(values)
    ]}}


def provider(payload, seen=None, status=200):
    def handler(req: httpx.Request):
        if seen is not None:
            seen.append(req)
        return httpx.Response(status, json=payload)

    return PrometheusProvider(transport=httpx.MockTransport(handler))


def check(p, **cfg):
    ctx = CheckContext(1, PrometheusQueryConfig(**cfg), 2.0, integration=INTEGRATION)
    return asyncio.run(p.check(ctx))


def test_threshold_down_and_auth_header():
    seen = []
    out = check(provider(vector(1, 0, 1), seen), query="up", comparison="<", down_threshold=1)
    assert out.status == CheckStatus.down
    assert "1/3 series" in out.message and 'instance="node1"' in out.message
    assert seen[0].headers["authorization"] == "Bearer tok"
    assert seen[0].url.params["query"] == "up"


def test_degraded_and_up():
    out = check(provider(vector(0.85)), query="q", comparison=">", down_threshold=0.95, degraded_threshold=0.8)
    assert out.status == CheckStatus.degraded
    out = check(provider(vector(0.5)), query="q", comparison=">", down_threshold=0.95, degraded_threshold=0.8)
    assert out.status == CheckStatus.up
    assert out.latency_ms is None  # query time is not target latency


def test_empty_result_policy():
    empty = {"status": "success", "data": {"resultType": "vector", "result": []}}
    assert check(provider(empty), query="q").status == CheckStatus.unknown
    assert check(provider(empty), query="q", empty_result="down").status == CheckStatus.down


def test_prometheus_error_is_unknown_not_down():
    out = check(provider({"status": "error", "error": "parse error"}, status=400), query="bad(")
    assert out.status == CheckStatus.unknown
    assert "parse error" in out.message


def test_missing_integration():
    ctx = CheckContext(1, PrometheusQueryConfig(query="up"), 2.0)
    assert asyncio.run(PrometheusProvider().check(ctx)).status == CheckStatus.unknown


def test_adapter_connection_test():
    def handler(req):
        if req.url.path.endswith("buildinfo"):
            return httpx.Response(200, json={"status": "success", "data": {"version": "2.53.0"}})
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "scalar", "result": [0, "1"]}})

    res = asyncio.run(PrometheusAdapter(httpx.MockTransport(handler)).test_connection(INTEGRATION, 2))
    assert res.ok and "2.53.0" in res.message

    res = asyncio.run(PrometheusAdapter(httpx.MockTransport(lambda r: httpx.Response(502))).test_connection(
        INTEGRATION, 2))
    assert not res.ok
