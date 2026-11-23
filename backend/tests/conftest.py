from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.update(
    CS_ENVIRONMENT="test",
    CS_SECRET_KEY="test-secret-key-not-for-production-use-0123456789",
    CS_SCHEDULER_ENABLED="false",
    CS_LOG_LEVEL="WARNING",
    CS_LOG_FORMAT="text",
    CS_DEMO_MODE="false",
)

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cloudsentinel.api.auth import limiter
from cloudsentinel.config import get_settings
from cloudsentinel.db import init_engine, session_factory
from cloudsentinel.main import create_app
from cloudsentinel.migrate import upgrade_database
from cloudsentinel.models import Role, User
from cloudsentinel.security import hash_password

PASSWORD = "correct-horse-battery"


TEST_PG_URL = os.environ.get("CS_TEST_DATABASE_URL")


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    """SQLite by default; set CS_TEST_DATABASE_URL to run the suite against PostgreSQL."""
    if TEST_PG_URL:
        from sqlalchemy import create_engine, text

        from cloudsentinel.config import Settings

        url = Settings(database_url=TEST_PG_URL).database_url
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
        engine.dispose()
        return url
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def db(db_url: str) -> Iterator[Session]:
    init_engine(db_url)
    upgrade_database(db_url)
    session = session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def settings(db_url: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CS_DATABASE_URL", db_url)
    get_settings.cache_clear()
    s = get_settings()
    yield s
    get_settings.cache_clear()


@pytest.fixture
def client(settings, db: Session) -> Iterator[TestClient]:
    limiter._failures.clear()
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def make_user(db: Session, email: str, role: Role) -> User:
    user = User(email=email, name=email.split("@")[0], role=role, password_hash=hash_password(PASSWORD))
    db.add(user)
    db.commit()
    return user


def auth_headers(client: TestClient, email: str) -> dict[str, str]:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def admin(client: TestClient, db: Session) -> dict[str, str]:
    make_user(db, "admin@example.com", Role.admin)
    return auth_headers(client, "admin@example.com")


@pytest.fixture
def operator(client: TestClient, db: Session) -> dict[str, str]:
    make_user(db, "ops@example.com", Role.operator)
    return auth_headers(client, "ops@example.com")


@pytest.fixture
def viewer(client: TestClient, db: Session) -> dict[str, str]:
    make_user(db, "viewer@example.com", Role.viewer)
    return auth_headers(client, "viewer@example.com")
