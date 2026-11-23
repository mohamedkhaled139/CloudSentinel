# Contributing to CloudSentinel

Thanks for your interest! CloudSentinel aims to be a tool infrastructure engineers trust, so we value
correctness, clear evidence and maintainability over feature count.

## Ways to contribute

* **Report bugs** with the bug template: version, deployment method, steps, expected vs actual.
* **Propose integrations** with the integration template: what should be checked, which public API is
  used, how failures should map to `up/degraded/down/unknown`.
* **Pick up an issue** labelled `good first issue` or `help wanted`, or an item from the
  [roadmap](docs/roadmap.md). Comment first so work is not duplicated.
* **Improve documentation** – especially real-world configuration examples.

## Development setup

See [docs/development.md](docs/development.md). In short:

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev,postgres]"
cd frontend && npm ci
```

## Pull request checklist

- [ ] The change is focused and explained in the PR description.
- [ ] Backend: `ruff check .`, `ruff format --check .`, `mypy cloudsentinel`, `pytest` pass.
- [ ] Frontend: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build` pass.
- [ ] New behaviour has tests; bug fixes include a regression test.
- [ ] Database changes include an Alembic migration that works on SQLite and PostgreSQL.
- [ ] User-facing changes update the docs and `CHANGELOG.md` (*Unreleased* section).
- [ ] No credentials, customer data, proprietary SDKs or confidential vendor documentation.

## Design principles

1. **Never present a guess as a fact.** A failing data source is `unknown`, not `down`; correlation is
   *probable*; risks are risks, not incidents.
2. **Evidence first.** Incidents and findings link to the results that produced them.
3. **Synthetic data is always labelled.**
4. **Boring operations.** One container, one database, migrations on start, structured logs.
5. **Accessible UI.** Never rely on colour alone; keyboard-usable controls; useful empty states.

## Commit messages and reviews

Use clear imperative subjects ("Add DNS provider"). Maintainers review for correctness, tests,
security and long-term maintainability; expect questions, not rubber stamps.

By contributing you agree that your contributions are licensed under the MIT License and that you will
follow the [Code of Conduct](CODE_OF_CONDUCT.md).
