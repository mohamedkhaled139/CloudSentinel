# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-23

First public release.

### Added
- Monitoring engine with an asyncio scheduler, bounded concurrency, per-monitor intervals, timeouts,
  failure/recovery thresholds and latency-based degradation.
- Check providers: HTTP/HTTPS (status, body, latency, TLS expiry), TCP (connect, banner), Linux host
  agent (heartbeat, load, memory, disk, reboots), Prometheus (PromQL thresholds) and Kubernetes
  (API readiness, nodes, deployments/statefulsets/daemonsets, pods and restarts).
- Integration adapter architecture with JSON-Schema-driven configuration forms and encrypted secrets.
- Service inventory, dependency graph with cycle protection, health propagation (*impacted* status)
  and blast-radius analysis; interactive topology view.
- Incident lifecycle (detected → investigating → resolved) with timelines, evidence, acknowledgement,
  notes, resolution notes, reopen, auto-resolution and dependency-based probable-cause correlation.
- Risk intelligence: recurring failures, availability below target, latency regression, restarts and
  reboots, TLS expiry, stale data, inconclusive monitors, coverage gaps and unreachable integrations.
- Reports with date ranges, availability, MTTR, incident time and top recurring problems; CSV and PDF export.
- Authentication with roles (viewer, operator, admin), first-run setup, audit log, login rate limiting.
- Configurable retention, health/readiness probes, structured JSON logging, Alembic migrations
  (SQLite and PostgreSQL).
- React console with dark and light themes, dependency-free Linux agent, Docker image, Docker Compose
  deployment, demo stack with clearly labelled synthetic data, Kubernetes manifests.

[Unreleased]: https://github.com/mohamedkhaled139/CloudSentinel/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mohamedkhaled139/CloudSentinel/releases/tag/v0.1.0
