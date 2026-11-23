# REST API

The API is versioned under `/api/v1`. Interactive OpenAPI documentation is served by every
installation at **`/api/docs`** (Swagger UI) and **`/api/redoc`**; the raw schema is at
`/api/openapi.json`.

## Authentication

```bash
TOKEN=$(curl -s -X POST https://sentinel.example.com/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"ops@example.com","password":"..."}' | jq -r .access_token)

curl -H "Authorization: Bearer $TOKEN" https://sentinel.example.com/api/v1/overview
```

Tokens expire after `CS_ACCESS_TOKEN_TTL_MINUTES` (12 h by default). Browser sessions use an HttpOnly
cookie instead; cookie-authenticated write requests must send `X-CloudSentinel-Client: web`.

Roles: `viewer` can read everything except users and the audit log; `operator` can also manage
components, dependencies, monitors, incidents and risk findings; `admin` can also manage users,
integrations, agents and settings.

## Endpoints

| Method & path | Role | Description |
|---|---|---|
| `GET /auth/setup-status`, `POST /auth/setup` | – | First-run administrator creation |
| `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/password` | – / any | Sessions |
| `GET/POST /users`, `PATCH/DELETE /users/{id}` | admin | User management |
| `GET /overview` | viewer | Dashboard summary |
| `GET/POST /components`, `GET/PATCH/DELETE /components/{id}` | viewer / operator | Service inventory with live health |
| `GET /components/{id}/impact` | viewer | Upstream dependencies and downstream blast radius |
| `GET /topology` | viewer | Nodes (with health) and dependency edges |
| `POST /dependencies`, `DELETE /dependencies/{id}` | operator | Declare/remove dependencies (cycles rejected) |
| `GET /providers` | viewer | Check providers and integration adapters with JSON Schemas |
| `GET/POST /monitors`, `GET/PATCH/DELETE /monitors/{id}` | viewer / operator | Monitors |
| `POST /monitors/test` | operator | Execute an unsaved configuration once |
| `POST /monitors/{id}/run` | operator | Run a check now and record it |
| `GET /monitors/{id}/results` | viewer | Paginated history (`start`, `end`, `status`, `limit`, `offset`) |
| `GET /monitors/{id}/stats?hours=24` | viewer | Availability, latency and 96-bucket series |
| `GET/POST /incidents`, `GET /incidents/{id}` | viewer / operator | List (filters: `status=open|detected|investigating|resolved`, `severity`, `component_id`, `q`, `start`, `end`), declare, investigate |
| `POST /incidents/{id}/acknowledge` · `/notes` · `/resolve` · `/reopen` | operator | Lifecycle actions |
| `GET /risks`, `POST /risks/analyze`, `POST /risks/{id}/dismiss` · `/reopen` | viewer / operator | Risk intelligence |
| `GET /reports/summary` | viewer | Report for `start`/`end` (default: last 7 days, max 366 days) |
| `GET /reports/export.csv?section=monitors|components|incidents`, `GET /reports/export.pdf` | viewer | Exports |
| `GET/POST /integrations`, `PATCH/DELETE /integrations/{id}`, `POST /integrations/{id}/test` | viewer / admin | Data sources (secrets are write-only) |
| `GET/POST /agents`, `DELETE /agents/{id}` | viewer / admin | Linux agents (token returned once on creation) |
| `POST /agent/heartbeat` | agent token | Agent reports |
| `GET/PUT /settings`, `POST /settings/retention/run` | viewer / admin | Runtime settings |
| `GET /system/info` | viewer | Version and runtime information |
| `GET /audit` | admin | Audit log |
| `GET /healthz`, `GET /readyz` | – | Liveness and readiness probes (outside `/api/v1`) |

## Examples

Create a component, a monitor, and a dependency:

```bash
API=https://sentinel.example.com/api/v1
H="Authorization: Bearer $TOKEN"

curl -s -X POST $API/components -H "$H" -H 'Content-Type: application/json' \
  -d '{"name":"payments-db","kind":"database","criticality":"critical","owner":"dba"}'

curl -s -X POST $API/monitors -H "$H" -H 'Content-Type: application/json' -d '{
  "name": "payments-db TCP", "kind": "tcp", "component_id": 1,
  "interval_seconds": 30, "failure_threshold": 3,
  "config": {"host": "db01.internal", "port": 5432}
}'

curl -s -X POST $API/dependencies -H "$H" -H 'Content-Type: application/json' \
  -d '{"source_id": 2, "target_id": 1}'   # component 2 depends on component 1
```

Validation errors return `422` with `detail.errors` listing the offending fields.
