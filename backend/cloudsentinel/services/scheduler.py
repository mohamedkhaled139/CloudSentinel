"""Lightweight asyncio scheduler for checks and periodic maintenance jobs.

Design notes:
* One scheduler instance per deployment. It runs inside the API process by default; for larger
  deployments run ``cloudsentinel scheduler`` as a separate process and set
  ``CS_SCHEDULER_ENABLED=false`` on API replicas.
* Due monitors are claimed by advancing ``next_run_at`` before execution, so a slow check never
  runs twice concurrently. A semaphore bounds concurrent checks.
* Initial runs are spread over each monitor's interval to avoid a thundering herd after restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from cloudsentinel.models import Integration, Monitor, utcnow
from cloudsentinel.providers.registry import Registry, registry
from cloudsentinel.services.monitoring import integration_context, run_monitor_once

log = logging.getLogger(__name__)


def _initial_offset(monitor_id: int, interval: int) -> float:
    digest = hashlib.sha256(str(monitor_id).encode()).digest()
    return (int.from_bytes(digest[:4], "big") % max(1, interval * 1000)) / 1000.0


class Scheduler:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        tick_seconds: float = 1.0,
        max_concurrency: int = 50,
        allow_private_targets: bool = True,
        demo_mode: bool = False,
        periodic_jobs: list[tuple[str, float, Callable[[], Awaitable[None]]]] | None = None,
        reg: Registry = registry,
    ) -> None:
        self.session_factory = session_factory
        self.tick_seconds = tick_seconds
        self.allow_private_targets = allow_private_targets
        self.demo_mode = demo_mode
        self.registry = reg
        self._sem = asyncio.Semaphore(max_concurrency)
        self._running: set[int] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._stop = asyncio.Event()
        self._main: asyncio.Task[None] | None = None
        self._periodic = periodic_jobs or []
        self._last_run: dict[str, float] = {}
        self.started_at: float | None = None
        self.last_tick_at: float | None = None

    def start(self) -> None:
        self._stop.clear()
        self.started_at = time.time()
        self._spread_initial_runs()
        self._main = asyncio.create_task(self._loop(), name="cloudsentinel-scheduler")
        log.info("scheduler started")

    async def stop(self) -> None:
        self._stop.set()
        if self._main:
            self._main.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._main
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        log.info("scheduler stopped")

    @property
    def healthy(self) -> bool:
        return self.last_tick_at is not None and time.time() - self.last_tick_at < max(
            30, self.tick_seconds * 10
        )

    def _spread_initial_runs(self) -> None:
        now = utcnow()
        with self.session_factory() as db:
            for m in db.scalars(
                select(Monitor).where(Monitor.enabled.is_(True), Monitor.next_run_at.is_(None))
            ):
                m.next_run_at = now + timedelta(seconds=_initial_offset(m.id, min(m.interval_seconds, 60)))
            db.commit()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            self.last_tick_at = time.time()
            try:
                for monitor_id in await asyncio.to_thread(self._claim_due):
                    self._running.add(monitor_id)
                    self._spawn(monitor_id)
                self._run_periodic()
            except Exception:
                log.exception("scheduler tick failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)

    def _claim_due(self) -> list[int]:
        now = utcnow()
        allowed_kinds = [k for k, p in self.registry.providers.items() if self.demo_mode or not p.synthetic]
        with self.session_factory() as db:
            due = db.scalars(
                select(Monitor)
                .where(
                    Monitor.enabled.is_(True),
                    Monitor.kind.in_(allowed_kinds),
                    or_(Monitor.next_run_at.is_(None), Monitor.next_run_at <= now),
                )
                .order_by(Monitor.next_run_at)
                .limit(500)
            ).all()
            claimed: list[int] = []
            for m in due:
                if m.id in self._running:
                    continue
                m.next_run_at = now + timedelta(seconds=m.interval_seconds)
                claimed.append(m.id)
            db.commit()
        return claimed

    def _spawn(self, monitor_id: int) -> None:
        task = asyncio.create_task(self._run(monitor_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, monitor_id: int) -> None:
        try:
            async with self._sem:
                await run_monitor_once(
                    monitor_id, self.session_factory, self.allow_private_targets, self.registry
                )
        except Exception:
            log.exception("check run failed", extra={"monitor_id": monitor_id})
        finally:
            self._running.discard(monitor_id)

    def _run_periodic(self) -> None:
        now = time.monotonic()
        for name, interval, job in self._periodic:
            last = self._last_run.get(name)
            if last is not None and now - last < interval:
                continue
            self._last_run[name] = now
            if any(t.get_name() == f"job:{name}" for t in self._tasks):
                continue
            task = asyncio.create_task(self._guard(name, job), name=f"job:{name}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _guard(name: str, job: Callable[[], Awaitable[None]]) -> None:
        try:
            await job()
        except Exception:
            log.exception("periodic job failed", extra={"job": name})


async def refresh_integration_status(
    session_factory: sessionmaker[Session], reg: Registry = registry
) -> None:
    """Periodically test every enabled integration and record its connection status."""

    def _load() -> list[tuple[int, str]]:
        with session_factory() as db:
            return [
                (i.id, i.kind) for i in db.scalars(select(Integration).where(Integration.enabled.is_(True)))
            ]

    for integration_id, _kind in await asyncio.to_thread(_load):
        await test_integration(session_factory, integration_id, reg)


async def test_integration(
    session_factory: sessionmaker[Session], integration_id: int, reg: Registry = registry
) -> tuple[bool, str, dict[str, object]]:
    def _ctx() -> tuple[str, object] | None:
        with session_factory() as db:
            integ = db.get(Integration, integration_id)
            if integ is None:
                return None
            try:
                return integ.kind, integration_context(integ)
            except RuntimeError as exc:
                return integ.kind, exc

    loaded = await asyncio.to_thread(_ctx)
    if loaded is None:
        return False, "not found", {}
    kind, ctx = loaded
    if isinstance(ctx, RuntimeError):
        ok, message, details = False, str(ctx), {}
    else:
        try:
            res = await reg.adapter(kind).test_connection(ctx, timeout=10)  # type: ignore[arg-type]
            ok, message, details = res.ok, res.message, res.details
        except Exception as exc:
            ok, message, details = False, f"Connection test error: {exc}", {}

    def _save() -> None:
        with session_factory() as db:
            integ = db.get(Integration, integration_id)
            if integ is None:
                return
            integ.status = "ok" if ok else "error"
            integ.last_error = None if ok else message
            integ.last_checked_at = utcnow()
            db.commit()

    await asyncio.to_thread(_save)
    return ok, message, details
