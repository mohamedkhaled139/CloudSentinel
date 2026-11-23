# Development guide

## Layout

```
backend/    FastAPI service (Python 3.11+): API, monitoring engine, providers, migrations, tests
frontend/   React + TypeScript console (Vite, TanStack Query, React Flow, Recharts), tests
agent/      Dependency-free Linux host agent
deploy/     Demo stack and Kubernetes manifests
docs/       Documentation
```

## Backend

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,postgres]"

# Run with synthetic demo data
export CS_DEMO_MODE=true CS_LOG_FORMAT=text
cloudsentinel seed-demo
cloudsentinel create-user you@example.com --role admin
cloudsentinel serve --reload            # http://127.0.0.1:8000/api/docs
```

Quality gates (all run in CI):

```bash
ruff check . && ruff format --check .
mypy cloudsentinel                      # strict typing
pytest                                  # SQLite
CS_TEST_DATABASE_URL=postgresql://postgres:test@localhost:5432/cs_test pytest   # PostgreSQL
```

A throwaway PostgreSQL for tests: `docker run --rm -d -p 5432:5432 -e POSTGRES_PASSWORD=test -e POSTGRES_DB=cs_test postgres:16-alpine`.

### Database migrations

Models live in `cloudsentinel/models.py`. After changing them:

```bash
alembic revision --autogenerate -m "describe the change"
```

Review the generated file in `cloudsentinel/migrations/versions/` (autogenerate is not perfect),
and make sure it works on both SQLite (batch mode is enabled) and PostgreSQL.

### CLI

| Command | Purpose |
|---|---|
| `cloudsentinel serve` | API + scheduler (`--host`, `--port`, `--reload`) |
| `cloudsentinel scheduler` | Scheduler only |
| `cloudsentinel migrate` | Apply migrations |
| `cloudsentinel create-user EMAIL --role admin` | Create a user (prompts for the password) |
| `cloudsentinel seed-demo` / `clear-demo` | Load / remove synthetic demo data (demo mode only) |
| `cloudsentinel analyze-risks` / `apply-retention` | Run maintenance jobs now |

## Frontend

```bash
cd frontend
npm ci
npm run dev        # http://localhost:5173, proxies /api to 127.0.0.1:8000
npm test           # vitest + Testing Library
npm run lint && npm run typecheck
npm run build      # outputs dist/, served by the backend when CS_STATIC_DIR points to it
```

Design conventions:

* Colours come from CSS tokens in `src/styles.css`, defined for light and dark themes.
* Status is always shown as icon **and** text (`StatusBadge`), never colour alone.
* Synthetic data is always marked with `SyntheticBadge`.
* Every list has a useful empty state that explains what to do next.
* Provider configuration forms are generated from backend JSON Schemas (`SchemaForm`); avoid
  hand-written forms for provider settings.

## Adding a provider

See [integrations.md](integrations.md#writing-a-new-integration).
