"""Kubernetes integration using the plain Kubernetes REST API (no client library required).

Supports bearer-token authentication against an API server URL, or in-cluster service-account
credentials when CloudSentinel itself runs inside the cluster. Only read (get/list) permissions
are needed; see docs/integrations.md for a minimal RBAC ClusterRole.
"""

from __future__ import annotations

import ssl
import time
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import Field, model_validator

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

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")


class KubernetesIntegrationConfig(ProviderConfig):
    in_cluster: bool = Field(
        default=False, description="Use the pod's service account (CloudSentinel runs in-cluster)"
    )
    api_server_url: str | None = Field(default=None, description="e.g. https://k8s.example.internal:6443")
    verify_tls: bool = True
    ca_cert_pem: str | None = Field(default=None, description="PEM CA bundle for the API server (optional)")

    @model_validator(mode="after")
    def _need_url(self) -> KubernetesIntegrationConfig:
        if not self.in_cluster and not self.api_server_url:
            raise ValueError("api_server_url is required unless in_cluster is true")
        if self.api_server_url and not self.api_server_url.startswith(("https://", "http://")):
            raise ValueError("api_server_url must start with https:// or http://")
        return self


class KubernetesSecrets(ProviderConfig):
    bearer_token: str | None = Field(default=None, description="Service account token with read-only access")


class KubernetesError(Exception):
    pass


class KubernetesClient:
    def __init__(
        self, integration: IntegrationContext, timeout: float, transport: httpx.AsyncBaseTransport | None
    ):
        cfg = KubernetesIntegrationConfig.model_validate(integration.config)
        token = KubernetesSecrets.model_validate(integration.secrets).bearer_token
        verify: bool | ssl.SSLContext = cfg.verify_tls
        base_url = cfg.api_server_url or ""
        if cfg.in_cluster:
            base_url = base_url or "https://kubernetes.default.svc"
            if not token and (SA_DIR / "token").exists():
                token = (SA_DIR / "token").read_text().strip()
            if cfg.verify_tls and (SA_DIR / "ca.crt").exists():
                verify = ssl.create_default_context(cafile=str(SA_DIR / "ca.crt"))
        if cfg.verify_tls and cfg.ca_cert_pem:
            verify = ssl.create_default_context(cadata=cfg.ca_cert_pem)
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            verify=verify,
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> KubernetesClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        resp = await self._client.get(path, params=params)
        if resp.status_code in (401, 403):
            raise KubernetesError(f"access denied ({resp.status_code}) for {path}; check token and RBAC")
        if resp.status_code == 404:
            raise KubernetesError(f"not found: {path}")
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    async def get_text(self, path: str) -> tuple[int, str]:
        resp = await self._client.get(path)
        return resp.status_code, resp.text


class KubernetesAdapter(IntegrationAdapter):
    kind = "kubernetes"
    label = "Kubernetes"
    description = "Read cluster, node and workload health from the Kubernetes API (read-only access)."
    config_model = KubernetesIntegrationConfig
    secret_model = KubernetesSecrets

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def test_connection(self, integration: IntegrationContext, timeout: float) -> ConnectionTestResult:
        try:
            async with KubernetesClient(integration, timeout, self._transport) as client:
                version = await client.get("/version")
                nodes = await client.get("/api/v1/nodes")
            return ConnectionTestResult(
                True,
                f"Connected to Kubernetes {version.get('gitVersion', '?')} ({len(nodes.get('items', []))} nodes)",
                {"version": version.get("gitVersion"), "nodes": len(nodes.get("items", []))},
            )
        except (httpx.HTTPError, KubernetesError, ValueError, ssl.SSLError) as exc:
            return ConnectionTestResult(False, f"Connection failed: {exc}")


_DNS_NAME = r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$"
Target = Literal["cluster", "nodes", "deployment", "statefulset", "daemonset", "pods"]


class KubernetesCheckConfig(ProviderConfig):
    target: Target = Field(description="What to evaluate")
    namespace: str | None = Field(
        default=None, max_length=63, pattern=_DNS_NAME, description="Required for workloads and pods"
    )
    name: str | None = Field(
        default=None,
        max_length=253,
        pattern=_DNS_NAME,
        description="Workload name (deployment/statefulset/daemonset)",
    )
    label_selector: str | None = Field(default=None, description="For target=pods, e.g. app=api")

    @model_validator(mode="after")
    def _validate(self) -> KubernetesCheckConfig:
        if self.target in ("deployment", "statefulset", "daemonset") and (
            not self.namespace or not self.name
        ):
            raise ValueError(f"namespace and name are required for target={self.target}")
        if self.target == "pods" and not self.namespace:
            raise ValueError("namespace is required for target=pods")
        return self


def _pod_summary(pods: list[dict[str, Any]]) -> dict[str, Any]:
    restarts: dict[str, int] = {}
    crashloop: list[str] = []
    not_ready: list[str] = []
    phases: dict[str, int] = {}
    for pod in pods:
        name = pod["metadata"]["name"]
        status = pod.get("status", {})
        phase = status.get("phase", "Unknown")
        phases[phase] = phases.get(phase, 0) + 1
        total = 0
        for cs in status.get("containerStatuses", []) or []:
            total += int(cs.get("restartCount", 0))
            waiting = (cs.get("state") or {}).get("waiting") or {}
            if waiting.get("reason") in ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull"):
                crashloop.append(f"{name}/{cs.get('name')}: {waiting['reason']}")
        restarts[name] = total
        ready = any(
            c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions", []) or []
        )
        if phase not in ("Succeeded",) and not ready:
            not_ready.append(name)
    return {
        "pod_count": len(pods),
        "phases": phases,
        "restarts": restarts,
        "total_restarts": sum(restarts.values()),
        "waiting_errors": crashloop[:20],
        "not_ready": not_ready[:50],
    }


class KubernetesProvider(Provider):
    key = "kubernetes"
    label = "Kubernetes health"
    description = (
        "Checks API server readiness, node conditions, workload replica availability and pod restarts."
    )
    config_model = KubernetesCheckConfig
    integration_kind = "kubernetes"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def check(self, ctx: CheckContext) -> CheckOutcome:
        cfg: KubernetesCheckConfig = ctx.config
        if ctx.integration is None:
            return CheckOutcome.unknown("No Kubernetes integration selected")
        started = time.perf_counter()
        try:
            async with KubernetesClient(ctx.integration, ctx.timeout_seconds, self._transport) as client:
                if cfg.target == "cluster":
                    outcome = await self._cluster(client)
                elif cfg.target == "nodes":
                    outcome = await self._nodes(client)
                elif cfg.target == "pods":
                    outcome = await self._pods(client, cfg)
                else:
                    outcome = await self._workload(client, cfg)
        except httpx.TimeoutException:
            return CheckOutcome.unknown(f"Kubernetes API timed out after {ctx.timeout_seconds:g}s")
        except (httpx.HTTPError, KubernetesError, ssl.SSLError) as exc:
            return CheckOutcome.unknown(f"Kubernetes API error: {exc}")
        outcome.details["api_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return outcome

    async def _cluster(self, client: KubernetesClient) -> CheckOutcome:
        code, text = await client.get_text("/readyz?verbose")
        failed = [line for line in text.splitlines() if line.startswith("[-]")]
        details = {"status_code": code, "failed_checks": failed[:20]}
        if code == 200:
            return CheckOutcome(CheckStatus.up, "API server ready", None, details)
        return CheckOutcome(CheckStatus.down, f"API server not ready (HTTP {code})", None, details)

    async def _nodes(self, client: KubernetesClient) -> CheckOutcome:
        items = (await client.get("/api/v1/nodes")).get("items", [])
        not_ready: list[str] = []
        pressure: list[str] = []
        for node in items:
            name = node["metadata"]["name"]
            for cond in node.get("status", {}).get("conditions", []) or []:
                ctype, status = cond.get("type"), cond.get("status")
                if ctype == "Ready" and status != "True":
                    not_ready.append(name)
                elif ctype in ("MemoryPressure", "DiskPressure", "PIDPressure") and status == "True":
                    pressure.append(f"{name}:{ctype}")
            if node.get("spec", {}).get("unschedulable"):
                pressure.append(f"{name}:Cordoned")
        details = {"node_count": len(items), "not_ready": not_ready, "conditions": pressure}
        if not items:
            return CheckOutcome(CheckStatus.unknown, "No nodes visible (check RBAC)", None, details)
        if len(not_ready) == len(items):
            return CheckOutcome(CheckStatus.down, "No nodes are Ready", None, details)
        if not_ready or pressure:
            parts = []
            if not_ready:
                parts.append(f"{len(not_ready)}/{len(items)} nodes NotReady")
            if pressure:
                parts.append(f"conditions: {', '.join(pressure[:5])}")
            return CheckOutcome(CheckStatus.degraded, "; ".join(parts), None, details)
        return CheckOutcome(CheckStatus.up, f"All {len(items)} nodes Ready", None, details)

    async def _pods(self, client: KubernetesClient, cfg: KubernetesCheckConfig) -> CheckOutcome:
        params = {"labelSelector": cfg.label_selector} if cfg.label_selector else None
        pods = (await client.get(f"/api/v1/namespaces/{cfg.namespace}/pods", params)).get("items", [])
        summary = _pod_summary(pods)
        if not pods:
            return CheckOutcome(CheckStatus.unknown, "No matching pods", None, summary)
        failed = summary["phases"].get("Failed", 0)
        if summary["waiting_errors"] or failed:
            ready = summary["pod_count"] - len(summary["not_ready"])
            status = CheckStatus.down if ready == 0 else CheckStatus.degraded
            msg = "; ".join(summary["waiting_errors"][:3]) or f"{failed} pods Failed"
            return CheckOutcome(status, msg, None, summary)
        if summary["not_ready"]:
            return CheckOutcome(
                CheckStatus.degraded, f"{len(summary['not_ready'])}/{len(pods)} pods not Ready", None, summary
            )
        return CheckOutcome(CheckStatus.up, f"{len(pods)} pods Ready", None, summary)

    async def _workload(self, client: KubernetesClient, cfg: KubernetesCheckConfig) -> CheckOutcome:
        plural = {"deployment": "deployments", "statefulset": "statefulsets", "daemonset": "daemonsets"}[
            cfg.target
        ]
        obj = await client.get(f"/apis/apps/v1/namespaces/{cfg.namespace}/{plural}/{cfg.name}")
        spec, status = obj.get("spec", {}), obj.get("status", {})
        if cfg.target == "daemonset":
            desired = int(status.get("desiredNumberScheduled", 0))
            ready = int(status.get("numberReady", 0))
        else:
            desired = int(spec.get("replicas", 1))
            ready = int(status.get("readyReplicas", 0))
        details: dict[str, Any] = {"desired": desired, "ready": ready}

        selector = (spec.get("selector") or {}).get("matchLabels") or {}
        if selector:
            label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
            pods = (
                await client.get(
                    f"/api/v1/namespaces/{cfg.namespace}/pods", {"labelSelector": label_selector}
                )
            ).get("items", [])
            details.update(_pod_summary(pods))

        label = f"{cfg.target} {cfg.namespace}/{cfg.name}"
        if desired == 0:
            return CheckOutcome(CheckStatus.up, f"{label} scaled to zero", None, details)
        if ready == 0:
            return CheckOutcome(CheckStatus.down, f"{label}: 0/{desired} replicas ready", None, details)
        if ready < desired:
            return CheckOutcome(
                CheckStatus.degraded, f"{label}: {ready}/{desired} replicas ready", None, details
            )
        return CheckOutcome(CheckStatus.up, f"{label}: {ready}/{desired} replicas ready", None, details)
