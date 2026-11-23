"""Programmatic Alembic migrations (run automatically on startup and via ``cloudsentinel migrate``)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(database_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg


def upgrade_database(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)
