# Integrations

CloudSentinel separates **check providers** (how a health check is executed) from **integration
adapters** (how to connect to an external system such as a Prometheus server or Kubernetes cluster).
Both are small Python classes registered in `backend/cloudsentinel/providers/registry.py`.

| Provider key | Needs integration | What it checks |
|---|---|---|
| `http` | – | Status code, body content, latency, TLS certificate expiry |
| `tcp` | – | TCP connect, optional banner match |
| `agent` | – (agent token) | Heartbeat freshness, load per core, memory, fullest filesystem |
| `prometheus` | `prometheus` | A PromQL instant query compared to thresholds |
| `kubernetes` | `kubernetes` | API server readiness, node conditions, workload replicas, pod restarts |
| `synthetic` | – | Simulated results; **demo mode only** |

## HTTP / HTTPS

| Field | Default | Notes |
|---|---|---|
| `url` | – | `http://` or `https://` |
| `method` | `GET` | `GET`, `HEAD`, `POST` |
| `headers` | `{}` | Extra request headers (do not put long-lived secrets here; they are stored in plain configuration) |
| `body` | – | Request body for POST |
| `expected_status_codes` | `[]` | Empty means any 2xx/3xx |
| `body_contains` | – | Response must contain this text |
| `follow_redirects` | `true` | |
| `verify_tls` | `true` | |
| `tls_expiry_warning_days` | 14 | Result becomes *degraded* when the certificate expires sooner; *down* once expired |

## TCP

`host`, `port`, optional `expect_banner` (e.g. `SSH-2.0` or `220` for SMTP).

## Linux host agent

1. **Integrations → Linux host agents → Register** and copy the token (shown once).
2. Copy `agent/cloudsentinel_agent.py` to the host (Python 3.8+, no dependencies) and run:
   ```bash
   sudo CLOUDSENTINEL_URL=https://sentinel.example.com CLOUDSENTINEL_TOKEN=csa_... \
     python3 cloudsentinel_agent.py --install     # installs a hardened systemd unit
   # or try it first:
   python3 cloudsentinel_agent.py --print         # show what would be sent
   ```
3. Create a monitor of type **Linux host (agent)** with the agent id.

Thresholds: `max_staleness_seconds` (180), `load_per_core_degraded` (2.0), `memory_degraded_pct` (90),
`disk_degraded_pct` (85), `disk_down_pct` (97). Boot-time changes feed the *repeated restarts* risk
detector.

## Prometheus

Works with any Prometheus-compatible query API (Prometheus, Thanos, Mimir, VictoriaMetrics…).

Integration: `base_url`, `verify_tls`, `extra_headers` (e.g. `X-Scope-OrgID`), and secrets
`bearer_token` **or** `basic_auth_username`/`basic_auth_password` (encrypted at rest).

Monitor fields: `query`, `comparison` (`>`, `>=`, `<`, `<=`, `==`, `!=`), `down_threshold`,
`degraded_threshold`, `empty_result` (`unknown` by default). Every series is compared; the result
message lists the offending series.

Examples:

| Goal | Query | Rule |
|---|---|---|
| Scrape targets down | `up{job="node"}` | `<` down 1 |
| Disk almost full | `1 - node_filesystem_avail_bytes{fstype!~"tmpfs"} / node_filesystem_size_bytes` | `>` degraded 0.85, down 0.95 |
| 5xx ratio | `sum(rate(http_requests_total{code=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))` | `>` degraded 0.01, down 0.05 |

If Prometheus itself cannot be queried the result is `unknown`, never `down`.

## Kubernetes

Integration options:

* **External cluster** – `api_server_url`, optional `ca_cert_pem`, and a read-only service-account
  `bearer_token`.
* **In-cluster** – `in_cluster: true`; the pod's service-account token and CA are used.

Minimal read-only RBAC (also in [`deploy/kubernetes/rbac.yaml`](../deploy/kubernetes/rbac.yaml)):

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: { name: cloudsentinel-reader }
rules:
  - apiGroups: [""]
    resources: ["nodes", "pods"]
    verbs: ["get", "list"]
  - apiGroups: ["apps"]
    resources: ["deployments", "statefulsets", "daemonsets"]
    verbs: ["get", "list"]
  - nonResourceURLs: ["/readyz", "/version"]
    verbs: ["get"]
```

Monitor targets:

| `target` | Requires | Down when | Degraded when |
|---|---|---|---|
| `cluster` | – | `/readyz` not 200 | – |
| `nodes` | – | no node Ready | some NotReady, Memory/Disk/PID pressure, cordoned |
| `deployment` / `statefulset` / `daemonset` | `namespace`, `name` | 0 ready replicas | fewer ready than desired |
| `pods` | `namespace`, optional `label_selector` | all pods failing | CrashLoopBackOff, image pull errors, not Ready |

Pod restart counters are recorded in result details and feed the *repeated restarts* risk detector.

---

# Writing a new integration

New integrations need **no frontend changes**: configuration forms are generated from the Pydantic
models' JSON Schema.

### 1. A check provider

```python
# backend/cloudsentinel/providers/ntp.py
from pydantic import Field
from cloudsentinel.models import CheckStatus
from cloudsentinel.providers.base import CheckContext, CheckOutcome, Provider, ProviderConfig


class NtpConfig(ProviderConfig):
    host: str = Field(description="NTP server")
    max_offset_ms: float = Field(default=100, gt=0)


class NtpProvider(Provider):
    key = "ntp"
    label = "NTP offset"
    description = "Queries an NTP server and checks clock offset."
    config_model = NtpConfig

    async def check(self, ctx: CheckContext) -> CheckOutcome:
        cfg: NtpConfig = ctx.config
        ...  # perform the check with ctx.timeout_seconds
        return CheckOutcome(CheckStatus.up, "offset 3 ms", latency_ms=12.0, details={"offset_ms": 3})
```

Rules of thumb:

* **Never raise.** Return `down` when the *target* is unhealthy and `CheckOutcome.unknown(...)` when the
  check itself cannot reach a verdict (bad credentials, data source unreachable).
* Respect `ctx.timeout_seconds`; the engine also enforces a hard deadline.
* Put structured evidence in `details` – it is shown in the UI and used by risk detectors.
* Use `ensure_target_allowed()` for user-supplied hosts so the SSRF guard applies.
* Only set `latency_ms` when it measures the target's response time.

### 2. (Optional) an integration adapter

Use one when several monitors share a connection and credentials:

```python
class VSphereAdapter(IntegrationAdapter):
    kind = "vsphere"
    label = "VMware vSphere"
    description = "..."
    config_model = VSphereConfig      # non-secret connection settings
    secret_model = VSphereSecrets     # encrypted at rest, never returned by the API

    async def test_connection(self, integration, timeout) -> ConnectionTestResult: ...
```

Set `integration_kind = "vsphere"` on the provider; the engine passes the decrypted
`IntegrationContext` as `ctx.integration`.

### 3. Register and test

Register both in `providers/registry.py`. Add tests using `httpx.MockTransport` (see
`tests/test_providers_prometheus.py`) or local sockets (see `tests/test_providers_http_tcp.py`).
Do not use proprietary SDKs that cannot be redistributed, real customer data, or confidential vendor
documentation.

Integration ideas the project would welcome are listed in the [roadmap](roadmap.md).
