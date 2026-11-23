"""Monitoring engine: executes checks through providers and records their outcomes."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from cloudsentinel.models import CheckResult, CheckStatus, Integration, Monitor, utcnow
from cloudsentinel.providers.base import CheckContext, CheckOutcome, IntegrationContext
from cloudsentinel.providers.registry import Registry, registry
from cloudsentinel.security import decrypt_secrets
from cloudsentinel.services import incidents

log = logging.getLogger(__name__)


def integration_context(integration: Integration) -> IntegrationContext:
    return IntegrationContext(
        id=integration.id,
        name=integration.name,
        kind=integration.kind,
        config=dict(integration.config or {}),
        secrets=decrypt_secrets(integration.secret_encrypted),
    )


def build_context(
    db: Session,
    monitor: Monitor,
    session_factory: sessionmaker[Session] | None,
    allow_private_targets: bool,
    reg: Registry = registry,
) -> CheckContext:
    provider = reg.provider(monitor.kind)
    cfg = provider.validate_config(monitor.config or {})
    integration_ctx = None
    if provider.integration_kind:
        integration = db.get(Integration, monitor.integration_id) if monitor.integration_id else None
        if integration is not None and integration.kind == provider.integration_kind and integration.enabled:
            integration_ctx = integration_context(integration)
    return CheckContext(
        monitor_id=monitor.id,
        config=cfg,
        timeout_seconds=float(monitor.timeout_seconds),
        degraded_latency_ms=monitor.degraded_latency_ms,
        integration=integration_ctx,
        session_factory=session_factory,
        allow_private_targets=allow_private_targets,
    )


async def execute_check(ctx: CheckContext, kind: str, reg: Registry = registry) -> CheckOutcome:
    """Run a provider check with a hard deadline. Never raises."""
    provider = reg.provider(kind)
    try:
        return await asyncio.wait_for(provider.check(ctx), timeout=ctx.timeout_seconds + 5)
    except TimeoutError:
        return CheckOutcome(CheckStatus.down, f"Check exceeded deadline ({ctx.timeout_seconds:g}s)")
    except Exception as exc:  # a provider bug must never stop the scheduler
        log.exception("provider raised", extra={"monitor_id": ctx.monitor_id, "kind": kind})
        return CheckOutcome.unknown(f"Internal check error: {exc.__class__.__name__}: {exc}")


def record_outcome(
    db: Session, monitor: Monitor, outcome: CheckOutcome, at: datetime | None = None
) -> CheckResult:
    """Persist a result, update the monitor's state machine and drive incident detection."""
    at = at or utcnow()
    result = CheckResult(
        monitor_id=monitor.id,
        checked_at=at,
        status=outcome.status,
        latency_ms=outcome.latency_ms,
        message=outcome.message[:2000],
        details=outcome.details,
        is_synthetic=monitor.is_synthetic,
    )
    db.add(result)

    previous = monitor.last_status
    if outcome.status != CheckStatus.unknown:
        if incidents.is_failing(monitor, outcome.status):
            monitor.consecutive_failures += 1
            monitor.consecutive_successes = 0
        else:
            monitor.consecutive_successes += 1
            monitor.consecutive_failures = 0
    monitor.last_status = outcome.status
    monitor.last_message = result.message
    monitor.last_checked_at = at
    monitor.next_run_at = at + timedelta(seconds=monitor.interval_seconds)
    incidents.handle_check_result(db, monitor, result, previous)
    return result


async def run_monitor_once(
    monitor_id: int,
    session_factory: sessionmaker[Session],
    allow_private_targets: bool = True,
    reg: Registry = registry,
) -> CheckResult | None:
    """Load a monitor, execute its check, and record the outcome. Used by the scheduler and the API."""

    def _prepare() -> tuple[CheckContext | None, CheckOutcome | None, str]:
        with session_factory() as db:
            monitor = db.get(Monitor, monitor_id)
            if monitor is None:
                return None, None, ""
            try:
                return (
                    build_context(db, monitor, session_factory, allow_private_targets, reg),
                    None,
                    monitor.kind,
                )
            except (ValidationError, KeyError, RuntimeError) as exc:
                return None, CheckOutcome.unknown(f"Invalid monitor configuration: {exc}"), monitor.kind

    ctx, early, kind = await asyncio.to_thread(_prepare)
    if ctx is None and early is None:
        return None
    outcome = early if early is not None else await execute_check(ctx, kind, reg)  # type: ignore[arg-type]

    def _save() -> CheckResult | None:
        with session_factory() as db:
            monitor = db.get(Monitor, monitor_id)
            if monitor is None:
                return None
            result = record_outcome(db, monitor, outcome)
            db.commit()
            return result

    return await asyncio.to_thread(_save)
