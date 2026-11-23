# Architecture

CloudSentinel is a single deployable service (API + scheduler + web console) backed by a relational
database. It is deliberately small: one container and one database are enough for most teams, and
every moving part can be understood from this page.

```
                ┌──────────────────────── CloudSentinel process ────────────────────────┐
 Browser ──────▶│  FastAPI (REST /api/v1, OpenAPI)   ◀── static React console (SPA)     │
 (React SPA)    │        │                                                              │
                │        ▼                                                              │
 Linux agent ──▶│  Domain services                                                      │
 (push, token)  │   monitoring · incidents · topology · risk · reports · retention      │
                │        ▲                       │                                      │
                │        │                       ▼                                      │
                │  Scheduler (asyncio) ──▶ Provider registry ──▶ http · tcp · agent      │
                │   due checks + periodic jobs        │          prometheus · kubernetes │
                └─────────────────────────────────────┼──────────────────────────────────┘
                         │ SQLAlchemy                 │ httpx / sockets
                         ▼                            ▼
              PostgreSQL (or SQLite)        Targets, Prometheus API, Kubernetes API
```

## Components

| Layer | Location | Responsibility |
|---|---|---|
| API | `backend/cloudsentinel/api/` | REST endpoints, validation (Pydantic), authN/authZ, audit log |
| Domain services | `backend/cloudsentinel/services/` | Monitoring state machine, incidents, topology, risk analysis, reports, retention, demo data |
| Providers | `backend/cloudsentinel/providers/` | One module per check type / integration; self-describing JSON schemas |
| Scheduler | `services/scheduler.py` | Runs due checks with bounded concurrency; periodic risk analysis, retention, integration health |
| Persistence | `models.py`, `migrations/` | SQLAlchemy 2 models, Alembic migrations (SQLite + PostgreSQL) |
| Web console | `frontend/` | React + TypeScript SPA, TanStack Query, React Flow topology, Recharts |
| Agent | `agent/cloudsentinel_agent.py` | Dependency-free Python script pushing host health |

## Data model

* **Component** – a service or infrastructure element (service, server, database, network, storage,
  Kubernetes cluster…), with owner, environment, tags and *criticality*.
* **Dependency** – a directed edge `source → target` meaning *source depends on target*. Cycles are
  rejected so impact analysis is always well defined.
* **Monitor** – a scheduled check of a given *kind* (provider key), optionally attached to a component
  and, for Prometheus/Kubernetes, to an **Integration**.
* **CheckResult** – one execution: `up | degraded | down | unknown`, latency, message, structured details.
* **Incident** + **IncidentEvent** – lifecycle and chronological timeline.
* **RiskFinding** – an evidence-backed risk with fingerprint, severity, recommendation and status.
* **Agent** / **AgentReport** – registered hosts and their pushed metrics.
* **User**, **AuditLog**, **AppSetting** – access control, accountability, runtime settings.

Synthetic (demo) rows carry `is_synthetic = true` on components, monitors, results, incidents and
findings; the UI and reports label them.

## Check execution and status semantics

1. The scheduler wakes every `CS_SCHEDULER_TICK_SECONDS`, *claims* due monitors by advancing
   `next_run_at`, and runs them under a semaphore (`CS_SCHEDULER_MAX_CONCURRENCY`). A monitor never
   runs concurrently with itself. After a restart, first runs are spread across the interval.
2. A provider returns a `CheckOutcome`. Providers never raise: timeouts and connection errors become
   `down`; failures of the *data source itself* (Prometheus unreachable, Kubernetes RBAC denied, invalid
   configuration) become `unknown`. **CloudSentinel never reports a target as down because the
   monitoring path failed.**
3. `record_outcome` stores the result and updates the monitor's state machine:
   * `down` (or `degraded` when *open incident on degraded* is enabled) increments
     `consecutive_failures`;
   * `up`/`degraded` increments `consecutive_successes`;
   * `unknown` changes neither counter.
4. Incident rules (`services/incidents.py`):
   * an incident opens after `failure_threshold` consecutive failures; its start time is the first
     failing check, and the failing results are attached as evidence;
   * status changes while open are appended to the timeline;
   * after `recovery_threshold` consecutive passes a *recovered* event is added and – unless disabled in
     settings – the incident auto-resolves.

## Health propagation and correlation

`services/topology.py` computes, for every component:

* **own status** – the worst status of its enabled monitors (`unmonitored` when it has none);
* **effective status** – `impacted` if any transitive dependency is `down`, otherwise the own status;
* **impacted_by** – the failing upstream components.

When an incident opens on a component, `correlate()` links it to open incidents on its dependencies
(it is a *probable effect*) or re-links open incidents on dependents beneath it (it is a *probable
cause*). Correlation is always labelled *probable*; it is derived from declared dependencies, not
proven causality.

## Risk intelligence

`services/risk.py` runs every `CS_RISK_ANALYSIS_INTERVAL_SECONDS` (and on demand). Each detector reads
recorded data only and emits findings with evidence:

| Category | Signal |
|---|---|
| `recurring_failure` | ≥ N separate down-episodes in the window |
| `availability_below_target` | measured availability below the configured target |
| `latency_regression` | last-24h median latency ≥ ratio × baseline median (min samples enforced) |
| `repeated_restarts` | Kubernetes restart counters increasing, or agent boot-time changes |
| `tls_certificate_expiry` | certificate expiry within the warning window |
| `stale_monitoring_data` | no fresh result for `multiplier × interval` |
| `inconclusive_monitor` | last 5 results were `unknown` |
| `monitoring_coverage_gap` | critical/high or shared components without an enabled monitor |
| `integration_unreachable` | integration connection test failing |

Findings are de-duplicated by fingerprint, refreshed each run, auto-resolved when the condition no
longer holds, and stay dismissed when an operator dismisses them.

## Security model

* Authentication: scrypt password hashes; HS256 session tokens signed with a key derived (HKDF) from
  `CS_SECRET_KEY`. The browser uses an HttpOnly, SameSite=Strict cookie plus a custom CSRF header;
  API clients use `Authorization: Bearer`.
* Authorization: `viewer` (read), `operator` (manage services/monitors/incidents/risks), `admin`
  (users, integrations, settings). The last active administrator cannot be removed.
* Integration secrets are encrypted at rest with Fernet (key derived from `CS_SECRET_KEY`), never
  returned by the API, and masked in the UI.
* Agent tokens are random, shown once and stored as SHA-256 hashes.
* Login rate limiting, security headers (CSP, X-Frame-Options, nosniff), audit log of administrative
  actions, optional SSRF guard (`CS_ALLOW_PRIVATE_TARGETS=false`).

## Scaling notes and trade-offs

* **Single scheduler.** Exactly one scheduler should run per database. For multiple API replicas set
  `CS_SCHEDULER_ENABLED=false` on them and run `cloudsentinel scheduler` once. Leader election is on
  the roadmap.
* **Sync SQLAlchemy + async checks.** Checks are async (httpx/asyncio); database work runs in worker
  threads. This keeps the data layer simple and portable while network I/O stays concurrent.
* **Raw results, no rollups (yet).** Availability is computed from raw results. With the default
  30-day retention this comfortably serves hundreds of monitors at 60-second intervals on PostgreSQL;
  pre-aggregated rollups are on the roadmap for larger installations.
* **Push agent.** The agent pushes over HTTPS, so hosts need no inbound firewall rules and the
  server never holds SSH credentials.
