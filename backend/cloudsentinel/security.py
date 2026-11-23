"""Password hashing, session tokens, API token hashing and at-rest encryption of integration secrets."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from cloudsentinel.config import get_settings

# scrypt work factor N is chosen in _scrypt_n(); r=8, p=1 (~64 MiB per hash).
_SCRYPT_R = 8
_SCRYPT_P = 1
_MAXMEM = 256 * 1024 * 1024
JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n = _scrypt_n()
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32, maxmem=_MAXMEM
    )
    return f"scrypt${n}${_SCRYPT_R}${_SCRYPT_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def _scrypt_n() -> int:
    # Use cheaper hashing in the test environment only.
    return 2**10 if get_settings().environment == "test" else 2**16


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt_b64, digest_b64 = encoded.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=_MAXMEM,
        )
        return hmac.compare_digest(expected, actual)
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str, role: str, ttl_minutes: int | None = None) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    ttl = ttl_minutes or settings.access_token_ttl_minutes
    payload = {"sub": subject, "role": role, "iat": now, "exp": now + timedelta(minutes=ttl), "typ": "access"}
    return jwt.encode(payload, _signing_key(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    payload: dict[str, Any] = jwt.decode(token, _signing_key(), algorithms=[JWT_ALGORITHM])
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload


def _derive(info: bytes) -> bytes:
    secret = get_settings().effective_secret_key().encode()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"cloudsentinel", info=info).derive(secret)


def _signing_key() -> bytes:
    return _derive(b"jwt-signing")


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derive(b"secret-encryption")))


def encrypt_secrets(data: dict[str, Any]) -> str | None:
    if not data:
        return None
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_secrets(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    try:
        result: dict[str, Any] = json.loads(_fernet().decrypt(token.encode()))
        return result
    except InvalidToken as exc:
        raise RuntimeError(
            "Unable to decrypt integration secrets; was CS_SECRET_KEY changed? Re-enter the credentials."
        ) from exc


def generate_agent_token() -> str:
    return "csa_" + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
