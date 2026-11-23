# CloudSentinel backend

Python/FastAPI service for [CloudSentinel](../README.md): monitoring engine, incident and risk
intelligence, reporting and the REST API (`/api/docs`).

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cloudsentinel serve --reload          # http://localhost:8000/api/docs
pytest                                # tests
ruff check . && mypy cloudsentinel    # lint + type-check
```

See [docs/development.md](../docs/development.md) for details.
