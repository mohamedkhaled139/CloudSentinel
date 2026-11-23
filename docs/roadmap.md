# Roadmap

This roadmap describes intended direction, not commitments or dates. Discussion and contributions are
welcome in GitHub issues; items marked **good first issue** are well scoped for new contributors.

## 0.2 – Notifications and operations
- Notification channels: email (SMTP), generic webhook, Slack-compatible webhook, Microsoft Teams
- Maintenance windows (suppress incidents, exclude from availability)
- Incident ownership/assignee and post-incident review template
- Per-component availability targets (SLOs) instead of one global target
- API tokens for automation (separate from user sessions)

## 0.3 – Scale and resilience
- Scheduler leader election so several replicas can run safely
- Hourly/daily rollups for long-range reports and faster dashboards
- Prometheus `/metrics` endpoint exposing CloudSentinel's own health
- Import/export of components, dependencies and monitors as YAML ("monitoring as code")

## Integrations
- DNS resolution and record checks — **good first issue**
- ICMP ping (with an unprivileged-socket fallback) — **good first issue**
- SNMP (network devices, storage arrays)
- OpenStack (Nova/Cinder/Neutron service health) and Proxmox VE
- VMware vSphere via its public REST API
- Alertmanager webhook receiver (turn existing alerts into correlated incidents)
- Kubernetes: auto-discovery of workloads as components

## UX
- Saved views and filters
- Topology grouping by environment/owner and large-graph performance
- Keyboard navigation and a full accessibility audit
- Internationalisation

## Not planned
- Log aggregation or full-text log search (use a dedicated log platform)
- Replacing Prometheus as a time-series database: CloudSentinel consumes metrics, it does not store them
