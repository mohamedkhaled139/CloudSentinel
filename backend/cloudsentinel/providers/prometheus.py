"""Prometheus integration: evaluate PromQL instant queries against thresholds."""

from __future__ import annotations

import math
import operator
import time
from collections.abc import Callable
from typing import Any, Literal

import httpx
from pydantic import Field, HttpUrl

from cloudsentinel.models import CheckStatus
from cloudsentinel.providers.base import (
    CheckContext,
    CheckOutcome,
    ConnectionTestResult,
    IntegrationAdapter,
    IntegrationContext,
    Provider,
    ProviderConfig,
)

_OPS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}
Comparison = Literal[">", ">=", "<", "<=", "==", "!="]


class PrometheusIntegrationConfig(ProviderConfig):
    base_url: HttpUrl = Field(description="Prometheus base URL, e.g. http://prometheus:9090")
    verify_tls: bool = True
    extra_headers: dict[str, str] = Field(
        default_factory=dict, description="Non-secret headers (e.g. tenant id)"
    )


class PrometheusSecrets(ProviderConfig):
    bearer_token: str | None = Field(default=None, description="Sent as Authorization: Bearer <token>")
    basic_auth_username: str | None = None
    basic_auth_password: str | None = None


class PrometheusClient:
    def __init__(
        self, integration: IntegrationContext, timeout: float, transport: httpx.AsyncBaseTransport | None
    ):
        cfg = PrometheusIntegrationConfig.model_validate(integration.config)
        sec = PrometheusSecrets.model_validate(integration.secrets)
        headers = dict(cfg.extra_headers)
        auth: httpx.BasicAuth | None = None
        if sec.bearer_token:
            headers["Authorization"] = f"Bearer {sec.bearer_token}"
        elif sec.basic_auth_username:
            auth = httpx.BasicAuth(sec.basic_auth_username, sec.basic_auth_password or "")
        self._client = httpx.AsyncClient(
            base_url=str(cfg.base_url).rstrip("/"),
            headers=headers,
            auth=auth,
            verify=cfg.verify_tls,
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> PrometheusClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def query(self, promql: str) -> dict[str, Any]:
        resp = await self._client.get("/api/v1/query", params={"query": promql})
        if resp.status_code >= 400 and resp.headers.get("content-type", "").startswith("application/json"):
            body = resp.json()
            raise PrometheusError(body.get("error") or f"HTTP {resp.status_code}")
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            raise PrometheusError(body.get("error", "query failed"))
        data: dict[str, Any] = body["data"]
        return data

    async def buildinfo(self) -> dict[str, Any]:
        resp = await self._client.get("/api/v1/status/buildinfo")
        resp.raise_for_status()
        data: dict[str, Any] = resp.json().get("data", {})
        return data


class PrometheusError(Exception):
    pass


def _series_values(data: dict[str, Any]) -> list[tuple[dict[str, str], float]]:
    result_type = data.get("resultType")
    result: Any = data.get("result")
    if result_type == "vector":
        return [(s.get("metric", {}), float(s["value"][1])) for s in result]
    if result_type == "scalar":
        return [({}, float(result[1]))]
    raise PrometheusError(f"unsupported result type {result_type!r}; use an instant vector or scalar")


def _label(metric: dict[str, str]) -> str:
    if not metric:
        return "{}"
    name = metric.get("__name__", "")
    inner = ",".join(f'{k}="{v}"' for k, v in sorted(metric.items()) if k != "__name__")
    return f"{name}{{{inner}}}"


class PrometheusAdapter(IntegrationAdapter):
    kind = "prometheus"
    label = "Prometheus"
    description = (
        "Use any Prometheus-compatible API (Prometheus, Thanos, Mimir, VictoriaMetrics) as a metrics source."
    )
    config_model = PrometheusIntegrationConfig
    secret_model = PrometheusSecrets

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def test_connection(self, integration: IntegrationContext, timeout: float) -> ConnectionTestResult:
        try:
            async with PrometheusClient(integration, timeout, self._transport) as client:
                data = await client.query("vector(1)")
                info: dict[str, Any] = {}
                try:
                    info = await client.buildinfo()
                except httpx.HTTPError:
                    info = {}
            ok = bool(_series_values(data))
            version = info.get("version")
            return ConnectionTestResult(ok, f"Connected{f' (Prometheus {version})' if version else ''}", info)
        except (httpx.HTTPError, PrometheusError, ValueError, KeyError) as exc:
            return ConnectionTestResult(False, f"Connection failed: {exc}")


class PrometheusQueryConfig(ProviderConfig):
    query: str = Field(min_length=1, max_length=4000, description="PromQL instant query")
    comparison: Comparison = Field(default=">", description="How each series value is compared to thresholds")
    down_threshold: float | None = Field(default=None, description="Series matching this are DOWN")
    degraded_threshold: float | None = Field(default=None, description="Series matching this are DEGRADED")
    empty_result: Literal["up", "down", "unknown"] = Field(
        default="unknown", description="Status when the query returns no series"
    )


class PrometheusProvider(Provider):
    key = "prometheus"
    label = "Prometheus query"
    description = "Evaluates a PromQL instant query and compares each series against thresholds."
    config_model = PrometheusQueryConfig
    integration_kind = "prometheus"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def check(self, ctx: CheckContext) -> CheckOutcome:
        cfg: PrometheusQueryConfig = ctx.config
        if ctx.integration is None:
            return CheckOutcome.unknown("No Prometheus integration selected")
        started = time.perf_counter()
        try:
            async with PrometheusClient(ctx.integration, ctx.timeout_seconds, self._transport) as client:
                data = await client.query(cfg.query)
            series = _series_values(data)
        except httpx.TimeoutException:
            return CheckOutcome.unknown(f"Prometheus query timed out after {ctx.timeout_seconds:g}s")
        except (httpx.HTTPError, PrometheusError) as exc:
            # The *data source* failed, not necessarily the monitored target: report unknown, never down.
            return CheckOutcome.unknown(f"Prometheus query failed: {exc}")
        # Query time measures Prometheus, not the target, so it is kept out of response-time statistics.
        latency_ms = None
        details: dict[str, Any] = {
            "query_ms": round((time.perf_counter() - started) * 1000, 2),
            "query": cfg.query,
            "series_count": len(series),
            "series": [{"labels": _label(m), "value": v} for m, v in series[:50]],
        }
        if not series:
            return CheckOutcome(
                CheckStatus(cfg.empty_result), "Query returned no series", latency_ms, details
            )

        op = _OPS[cfg.comparison]

        def matches(threshold: float | None) -> list[str]:
            if threshold is None:
                return []
            return [_label(m) for m, v in series if not math.isnan(v) and op(v, threshold)]

        down = matches(cfg.down_threshold)
        if down:
            msg = f"{len(down)}/{len(series)} series {cfg.comparison} {cfg.down_threshold:g}: {', '.join(down[:3])}"
            details["matching_series"] = down[:50]
            return CheckOutcome(CheckStatus.down, msg, latency_ms, details)
        degraded = matches(cfg.degraded_threshold)
        if degraded:
            msg = (
                f"{len(degraded)}/{len(series)} series {cfg.comparison} {cfg.degraded_threshold:g}: "
                f"{', '.join(degraded[:3])}"
            )
            details["matching_series"] = degraded[:50]
            return CheckOutcome(CheckStatus.degraded, msg, latency_ms, details)
        if len(series) == 1:
            return CheckOutcome(
                CheckStatus.up, f"Value {series[0][1]:g} within thresholds", latency_ms, details
            )
        return CheckOutcome(
            CheckStatus.up, f"All {len(series)} series within thresholds", latency_ms, details
        )
