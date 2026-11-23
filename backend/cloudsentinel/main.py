"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select

from cloudsentinel import __version__
from cloudsentinel.api import (
    auth,
    components,
    incidents,
    integrations,
    monitors,
    overview,
    reports,
    risks,
    system,
)
from cloudsentinel.config import Settings, get_settings
from cloudsentinel.db import init_engine, session_factory, session_scope
from cloudsentinel.logging_setup import configure_logging
from cloudsentinel.migrate import upgrade_database
from cloudsentinel.models import Role, User
from cloudsentinel.security import hash_password
from cloudsentinel.services.retention import apply_retention
from cloudsentinel.services.risk import run_analysis
from cloudsentinel.services.scheduler import Scheduler, refresh_integration_status

log = logging.getLogger("cloudsentinel")

API_PREFIX = "/api/v1"


def bootstrap_admin(settings: Settings) -> None:
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        return
    with session_scope() as db:
        if (db.scalar(select(func.count(User.id))) or 0) > 0:
            return
        db.add(
            User(
                email=settings.bootstrap_admin_email.strip().lower(),
                name="Administrator",
                password_hash=hash_password(settings.bootstrap_admin_password),
                role=Role.admin,
            )
        )
        log.info("bootstrap administrator created", extra={"email": settings.bootstrap_admin_email})


def build_scheduler(settings: Settings) -> Scheduler:
    factory = session_factory()

    async def risk_job() -> None:
        def _run() -> None:
            with session_scope() as db:
                run_analysis(db, demo_mode=settings.demo_mode)

        await asyncio.to_thread(_run)

    async def retention_job() -> None:
        def _run() -> None:
            with session_scope() as db:
                apply_retention(db)

        await asyncio.to_thread(_run)

    async def integrations_job() -> None:
        await refresh_integration_status(factory)

    return Scheduler(
        factory,
        tick_seconds=settings.scheduler_tick_seconds,
        max_concurrency=settings.scheduler_max_concurrency,
        allow_private_targets=settings.allow_private_targets,
        demo_mode=settings.demo_mode,
        periodic_jobs=[
            ("integrations", 300, integrations_job),
            ("risk_analysis", float(settings.risk_analysis_interval_seconds), risk_job),
            ("retention", float(settings.retention_interval_seconds), retention_job),
        ],
    )


def create_app(settings: Settings | None = None, *, run_migrations: bool = True) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.effective_secret_key()  # fail fast on missing production secret
        init_engine(settings.database_url)
        if run_migrations:
            await asyncio.to_thread(upgrade_database, settings.database_url)
        bootstrap_admin(settings)
        app.state.scheduler = None
        if settings.scheduler_enabled:
            app.state.scheduler = build_scheduler(settings)
            app.state.scheduler.start()
        log.info("CloudSentinel started", extra={"version": __version__, "demo_mode": settings.demo_mode})
        try:
            yield
        finally:
            if app.state.scheduler:
                await app.state.scheduler.stop()

    app = FastAPI(
        title="CloudSentinel API",
        version=__version__,
        description="Self-hosted infrastructure health and availability intelligence.",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"path": request.url.path, "request_id": request_id})
            response = JSONResponse(
                {"detail": "Internal server error", "request_id": request_id}, status_code=500
            )
        duration = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.startswith("/api/docs") and not request.url.path.startswith("/api/redoc"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "frame-ancestors 'none'; base-uri 'self'",
            )
        if request.url.path.startswith(API_PREFIX):
            response.headers.setdefault("Cache-Control", "no-store")
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": round(duration, 1),
                    "request_id": request_id,
                },
            )
        return response

    for module in (auth, overview, components, monitors, incidents, risks, reports, integrations, system):
        app.include_router(module.router, prefix=API_PREFIX)
    app.include_router(system.probes)

    if settings.static_dir and Path(settings.static_dir).is_dir():
        _mount_spa(app, Path(settings.static_dir))
    return app


def _mount_spa(app: FastAPI, root: Path) -> None:
    root = root.resolve()
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Response:
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            headers = (
                {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") else {}
            )
            return FileResponse(candidate, headers=headers)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
