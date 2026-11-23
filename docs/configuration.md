# Configuration reference

CloudSentinel has two kinds of configuration:

1. **Process settings** – environment variables prefixed with `CS_` (or a `.env` file in the working
   directory). They require a restart.
2. **Runtime settings** – retention, incident and risk-analysis parameters edited by administrators in
   **Settings** (or `PUT /api/v1/settings`) and stored in the database.

## Process settings (environment variables)

| Variable | Default | Description |
|---|---|---|
| `CS_ENVIRONMENT` | `development` | `development`, `production` or `test`. Production requires a secret key. |
| `CS_COOKIE_SECURE` | `auto` | `auto` marks the session cookie `Secure` when the request arrived over HTTPS (including via a trusted proxy); `true`/`false` force it. |
| `CS_DATABASE_URL` | `sqlite:///./cloudsentinel.db` | SQLAlchemy URL. `postgresql://user:pass@host:5432/db` is normalised to the psycopg driver. |
| `CS_SECRET_KEY` | – | **Required in production.** Signs sessions and encrypts integration secrets. Use ≥ 32 random bytes. Changing it logs everyone out and makes stored integration credentials unreadable (re-enter them). |
| `CS_SECRET_KEY_FILE` | – | Read the secret key from a file (Docker/Kubernetes secrets). |
| `CS_ACCESS_TOKEN_TTL_MINUTES` | `720` | Session lifetime. |
| `CS_BOOTSTRAP_ADMIN_EMAIL` / `CS_BOOTSTRAP_ADMIN_PASSWORD` | – | Create this administrator on startup if no users exist. Otherwise the first visitor sees a setup screen. |
| `CS_SCHEDULER_ENABLED` | `true` | Run the check scheduler inside the API process. Set `false` on extra API replicas. |
| `CS_SCHEDULER_TICK_SECONDS` | `1.0` | How often due checks are dispatched. |
| `CS_SCHEDULER_MAX_CONCURRENCY` | `50` | Maximum checks running at once. |
| `CS_RISK_ANALYSIS_INTERVAL_SECONDS` | `300` | Risk analysis period. |
| `CS_RETENTION_INTERVAL_SECONDS` | `3600` | How often the retention policy is applied. |
| `CS_DEMO_MODE` | `false` | Enables the synthetic provider and `seed-demo`. Never enable on a real installation. |
| `CS_ALLOW_PRIVATE_TARGETS` | `true` | Allow checks against private/loopback addresses. Infrastructure monitoring normally needs this; disable if untrusted users can create monitors. |
| `CS_LOG_LEVEL` | `INFO` | Python log level. |
| `CS_LOG_FORMAT` | `json` | `json` (one object per line, for log shippers) or `text`. |
| `CS_CORS_ORIGINS` | `[]` | JSON list of allowed origins when the UI is served from another origin, e.g. `["https://ops.example.com"]`. |
| `CS_STATIC_DIR` | – | Directory of the built web console to serve (set in the Docker image). |

## Runtime settings

| Setting | Default | Meaning |
|---|---|---|
| `retention_check_results_days` | 30 | Raw check results kept |
| `retention_agent_reports_days` | 7 | Agent reports kept |
| `retention_resolved_incidents_days` | 365 | Resolved incidents (and timelines) kept |
| `retention_resolved_risks_days` | 90 | Resolved risk findings kept |
| `auto_resolve_incidents` | true | Resolve incidents automatically when their monitor recovers |
| `risk_lookback_days` | 7 | History window used by risk detection |
| `recurring_failure_min_episodes` | 3 | Down-episodes needed for a recurring-failure finding |
| `latency_regression_ratio` | 1.5 | Last-24h median / baseline median that triggers a finding |
| `latency_min_samples` | 20 | Minimum samples in each period before latency or availability statistics are used |
| `restart_min_count` | 3 | Restarts/reboots in the window that trigger a finding |
| `stale_interval_multiplier` | 3 | A monitor is stale after `multiplier × interval + 60s` without a result |
| `availability_target_pct` | 99.9 | Availability below this produces a finding |
| `tls_expiry_risk_days` | 21 | Certificates expiring sooner produce a finding |

## Per-monitor settings

| Field | Default | Meaning |
|---|---|---|
| `interval_seconds` | 60 | Check period (10 s – 24 h) |
| `timeout_seconds` | 10 | Per-check timeout |
| `failure_threshold` | 3 | Consecutive failing checks before an incident opens |
| `recovery_threshold` | 2 | Consecutive passing checks before recovery |
| `degraded_latency_ms` | – | Mark results slower than this as degraded |
| `open_incident_on_degraded` | false | Treat degraded results as failures for incident purposes |

Provider-specific fields are documented in [integrations.md](integrations.md) and exposed as JSON
Schema at `GET /api/v1/providers`.
