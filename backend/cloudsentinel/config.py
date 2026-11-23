"""Runtime configuration.

All settings are read from environment variables prefixed with ``CS_`` (or a ``.env`` file).
No credentials are hardcoded: the secret key must be supplied in production.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CS_", env_file=".env", extra="ignore")

    environment: Literal["development", "production", "test"] = "development"
    database_url: str = "sqlite:///./cloudsentinel.db"

    # Used to sign session tokens and derive the encryption key for stored integration secrets.
    # Must be set explicitly in production (see docs/configuration.md).
    secret_key: str = Field(default="", repr=False)
    # Alternatively read the key from a file (e.g. a Docker/Kubernetes secret mount).
    secret_key_file: str | None = None
    access_token_ttl_minutes: int = Field(default=720, ge=5, le=60 * 24 * 30)

    # Optional bootstrap administrator, created on startup if no users exist.
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = Field(default=None, repr=False)

    scheduler_enabled: bool = True
    scheduler_tick_seconds: float = Field(default=1.0, gt=0, le=60)
    scheduler_max_concurrency: int = Field(default=50, ge=1, le=1000)
    risk_analysis_interval_seconds: int = Field(default=300, ge=30)
    retention_interval_seconds: int = Field(default=3600, ge=60)

    # Enables the synthetic provider and demo seeding. Synthetic data is always flagged as such.
    demo_mode: bool = False

    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    cors_origins: list[str] = Field(default_factory=list)
    static_dir: str | None = None  # Built frontend to serve (set in the Docker image).

    # Outbound check safety: allow monitors to target private/loopback addresses.
    # Infrastructure monitoring normally needs this, so it defaults to True; disable for SaaS-like setups.
    allow_private_targets: bool = True

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://") :]
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def effective_secret_key(self) -> str:
        if self.secret_key:
            return self.secret_key
        if self.secret_key_file:
            return Path(self.secret_key_file).read_text().strip()
        if self.environment == "production":
            raise RuntimeError(
                "CS_SECRET_KEY or CS_SECRET_KEY_FILE must be set when CS_ENVIRONMENT=production"
            )
        return _local_dev_key()


@lru_cache(maxsize=1)
def _local_dev_key() -> str:
    """Development convenience: generate a key once and keep it in a local, owner-only file."""
    path = Path(".cloudsentinel_secret")
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_urlsafe(48)
    path.write_text(key)
    os.chmod(path, 0o600)
    return key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
