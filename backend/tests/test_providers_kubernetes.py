import asyncio

import httpx
import pytest

from cloudsentinel.models import CheckStatus
from cloudsentinel.providers.base import CheckContext, IntegrationContext
from cloudsentinel.providers.kubernetes import (
    KubernetesAdapter,
    KubernetesCheckConfig,
    KubernetesIntegrationConfig,
    KubernetesProvider,
)

INTEG = IntegrationContext(1, "k8s", "kubernetes", {"api_server_url": "https://k8s.test:6443"},
                           {"bearer_token": "sa-token"})


def node(name, ready=True, pressure=None):
    conds = [{"type": "Ready", "status": "True" if ready else "False"}]
    if pressure:
        conds.append({"type": pressure, "status": "True"})
    return {"metadata": {"name": name}, "status": {"conditions": conds}, "spec": {}}


def pod(name, restarts=0, ready=True, waiting=None, phase="Running"):
    cs = {"name": "app", "restartCount": restarts, "state": {"waiting": {"reason": waiting}} if waiting else {}}
    return {"metadata": {"name": name}, "status": {
        "phase": phase, "containerStatuses": [cs],
        "conditions": [{"type": "Ready", "status": "True" if ready else "False"}]}}


def run(routes, **cfg):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        for path, resp in routes.items():
            if req.url.path == path:
                return resp if isinstance(resp, httpx.Response) else httpx.Response(200, json=resp)
        return httpx.Response(404, json={})

    p = KubernetesProvider(transport=httpx.MockTransport(handler))
    out = asyncio.run(p.check(CheckContext(1, KubernetesCheckConfig(**cfg), 2.0, integration=INTEG)))
    return out, seen


def test_nodes():
    out, seen = run({"/api/v1/nodes": {"items": [node("a"), node("b")]}}, target="nodes")
    assert out.status == CheckStatus.up
    assert seen[0].headers["authorization"] == "Bearer sa-token"
    out, _ = run({"/api/v1/nodes": {"items": [node("a"), node("b", ready=False)]}}, target="nodes")
    assert out.status == CheckStatus.degraded and out.details["not_ready"] == ["b"]
    out, _ = run({"/api/v1/nodes": {"items": [node("a", pressure="DiskPressure")]}}, target="nodes")
    assert out.status == CheckStatus.degraded
    out, _ = run({"/api/v1/nodes": {"items": [node("a", ready=False)]}}, target="nodes")
    assert out.status == CheckStatus.down


def test_deployment_replicas_and_restarts():
    dep = {"spec": {"replicas": 3, "selector": {"matchLabels": {"app": "api"}}}, "status": {"readyReplicas": 2}}
    pods = {"items": [pod("api-1", restarts=4), pod("api-2"), pod("api-3", ready=False)]}
    out, seen = run({"/apis/apps/v1/namespaces/prod/deployments/api": dep, "/api/v1/namespaces/prod/pods": pods},
                    target="deployment", namespace="prod", name="api")
    assert out.status == CheckStatus.degraded
    assert out.details["restarts"]["api-1"] == 4
    assert seen[1].url.params["labelSelector"] == "app=api"
    dep["status"]["readyReplicas"] = 0
    out, _ = run({"/apis/apps/v1/namespaces/prod/deployments/api": dep, "/api/v1/namespaces/prod/pods": pods},
                 target="deployment", namespace="prod", name="api")
    assert out.status == CheckStatus.down


def test_pods_crashloop():
    pods = {"items": [pod("w-1", waiting="CrashLoopBackOff", ready=False, restarts=9), pod("w-2")]}
    out, _ = run({"/api/v1/namespaces/jobs/pods": pods}, target="pods", namespace="jobs")
    assert out.status == CheckStatus.degraded
    assert "CrashLoopBackOff" in out.message


def test_cluster_readyz_and_rbac_error():
    out, _ = run({"/readyz": httpx.Response(200, text="ok")}, target="cluster")
    assert out.status == CheckStatus.up
    out, _ = run({"/readyz": httpx.Response(500, text="[-]etcd failed")}, target="cluster")
    assert out.status == CheckStatus.down and out.details["failed_checks"] == ["[-]etcd failed"]
    out, _ = run({"/api/v1/nodes": httpx.Response(403, json={})}, target="nodes")
    assert out.status == CheckStatus.unknown and "RBAC" in out.message


def test_config_validation():
    with pytest.raises(ValueError):
        KubernetesCheckConfig(target="deployment", namespace="prod")
    with pytest.raises(ValueError):
        KubernetesCheckConfig(target="pods", namespace="../etc")
    with pytest.raises(ValueError):
        KubernetesIntegrationConfig()
    assert KubernetesIntegrationConfig(in_cluster=True).in_cluster


def test_adapter_test_connection():
    def handler(req):
        if req.url.path == "/version":
            return httpx.Response(200, json={"gitVersion": "v1.31.2"})
        return httpx.Response(200, json={"items": [node("a")]})

    res = asyncio.run(KubernetesAdapter(httpx.MockTransport(handler)).test_connection(INTEG, 2))
    assert res.ok and "v1.31.2" in res.message and "1 nodes" in res.message
