"""Command-line interface: ``cloudsentinel <command>``."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import signal
import sys

from sqlalchemy import select

from cloudsentinel import __version__
from cloudsentinel.config import get_settings
from cloudsentinel.db import init_engine, session_scope
from cloudsentinel.logging_setup import configure_logging
from cloudsentinel.migrate import upgrade_database
from cloudsentinel.models import Role, User


def _prepare(migrate: bool = True) -> None:
    s = get_settings()
    configure_logging(s.log_level, s.log_format)
    init_engine(s.database_url)
    if migrate:
        upgrade_database(s.database_url)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "cloudsentinel.asgi:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        proxy_headers=True,
        forwarded_allow_ips=args.forwarded_allow_ips,
        log_config=None,
    )
    return 0


def cmd_migrate(_: argparse.Namespace) -> int:
    _prepare(migrate=True)
    print("Database is up to date.")
    return 0


def cmd_create_user(args: argparse.Namespace) -> int:
    from cloudsentinel.schemas import UserCreate
    from cloudsentinel.security import hash_password

    _prepare()
    password = args.password or getpass.getpass("Password (min 10 chars): ")
    body = UserCreate(email=args.email, name=args.name or "", password=password, role=Role(args.role))
    with session_scope() as db:
        if db.scalars(select(User).where(User.email == body.email)).first():
            print(f"User {body.email} already exists", file=sys.stderr)
            return 1
        db.add(
            User(email=body.email, name=body.name, role=body.role, password_hash=hash_password(body.password))
        )
    print(f"Created {body.role.value} {body.email}")
    return 0


def cmd_seed_demo(args: argparse.Namespace) -> int:
    from cloudsentinel.services.demo import seed_demo

    _prepare()
    if not get_settings().demo_mode and not args.force:
        print("Refusing to seed synthetic data: set CS_DEMO_MODE=true (or pass --force).", file=sys.stderr)
        return 1
    with session_scope() as db:
        stats = seed_demo(db, days=args.days)
    print("Seeded SYNTHETIC demo data:", json.dumps(stats))
    return 0


def cmd_clear_demo(_: argparse.Namespace) -> int:
    from cloudsentinel.services.demo import clear_demo_data

    _prepare()
    with session_scope() as db:
        clear_demo_data(db)
    print("Removed all synthetic demo data.")
    return 0


def cmd_analyze(_: argparse.Namespace) -> int:
    from cloudsentinel.services.risk import run_analysis

    _prepare()
    with session_scope() as db:
        print(json.dumps(run_analysis(db, demo_mode=get_settings().demo_mode)))
    return 0


def cmd_retention(_: argparse.Namespace) -> int:
    from cloudsentinel.services.retention import apply_retention

    _prepare()
    with session_scope() as db:
        print(json.dumps(apply_retention(db)))
    return 0


def cmd_scheduler(_: argparse.Namespace) -> int:
    """Run only the scheduler (for deployments that separate API and workers)."""
    from cloudsentinel.main import build_scheduler

    _prepare()
    settings = get_settings()

    async def main() -> None:
        scheduler = build_scheduler(settings)
        scheduler.start()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        await scheduler.stop()

    asyncio.run(main())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cloudsentinel", description="CloudSentinel infrastructure health")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="Run the API server (and scheduler unless CS_SCHEDULER_ENABLED=false)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.add_argument("--forwarded-allow-ips", default="127.0.0.1")
    p.set_defaults(func=cmd_serve)

    sub.add_parser("migrate", help="Apply database migrations").set_defaults(func=cmd_migrate)

    p = sub.add_parser("create-user", help="Create a user")
    p.add_argument("email")
    p.add_argument("--name")
    p.add_argument("--role", choices=[r.value for r in Role], default="admin")
    p.add_argument("--password", help="Prompted if omitted (preferred: avoids shell history)")
    p.set_defaults(func=cmd_create_user)

    p = sub.add_parser("seed-demo", help="Load the synthetic demo environment (requires CS_DEMO_MODE=true)")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_seed_demo)
    sub.add_parser("clear-demo", help="Delete all synthetic demo data").set_defaults(func=cmd_clear_demo)
    sub.add_parser("analyze-risks", help="Run risk analysis now").set_defaults(func=cmd_analyze)
    sub.add_parser("apply-retention", help="Apply the retention policy now").set_defaults(func=cmd_retention)
    sub.add_parser("scheduler", help="Run the check scheduler only").set_defaults(func=cmd_scheduler)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
