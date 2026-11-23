# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | Yes |

## Reporting a vulnerability

Please **do not** open public issues for security problems. Report them privately through GitHub's
[private vulnerability reporting](https://github.com/mohamedkhaled139/CloudSentinel/security/advisories/new).

Include affected versions, a description, reproduction steps and impact. We aim to acknowledge reports
within 5 working days and to agree on a disclosure timeline with you. Reporters are credited unless
they prefer otherwise.

## Security design summary

* Passwords hashed with scrypt; session tokens signed with a key derived from `CS_SECRET_KEY`.
* Browser sessions use HttpOnly, SameSite=Strict cookies plus a CSRF header; `Secure` when served over HTTPS.
* Role-based access control (viewer, operator, admin) enforced server-side on every endpoint.
* Integration credentials encrypted at rest (Fernet) and never returned by the API.
* Agent tokens are shown once and stored as SHA-256 hashes.
* Login rate limiting, audit log, Content-Security-Policy and other security headers.
* Optional SSRF guard: `CS_ALLOW_PRIVATE_TARGETS=false` blocks checks against private addresses.

## Hardening checklist for operators

* Set a strong `CS_SECRET_KEY` (or `CS_SECRET_KEY_FILE`) and keep it out of version control.
* Serve CloudSentinel over HTTPS behind a reverse proxy; do not expose the database.
* Give the Kubernetes integration the read-only role in `deploy/kubernetes/rbac.yaml`, nothing more.
* Use least-privilege, read-only credentials for Prometheus and other data sources.
* Grant `admin` sparingly; most users need `viewer` or `operator`.
* Never enable `CS_DEMO_MODE` or use the demo credentials on a reachable network.
