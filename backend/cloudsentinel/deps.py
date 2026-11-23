"""Authentication and authorization dependencies.

Clients authenticate with either:
* ``Authorization: Bearer <token>`` (API clients, CLI, automation), or
* the ``cs_session`` HttpOnly, SameSite=Strict cookie set by ``/auth/login`` (the web UI).

Cookie-authenticated state-changing requests must also send ``X-CloudSentinel-Client: web`` as a
CSRF defence-in-depth measure (a cross-site form cannot set custom headers).
"""

from __future__ import annotations

from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from cloudsentinel.db import get_db
from cloudsentinel.models import AuditLog, Role, User
from cloudsentinel.security import decode_access_token

COOKIE_NAME = "cs_session"
CSRF_HEADER = "x-cloudsentinel-client"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_ROLE_RANK = {Role.viewer: 0, Role.operator: 1, Role.admin: 2}


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token: str | None = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    else:
        token = request.cookies.get(COOKIE_NAME)
        if token and request.method not in _SAFE_METHODS and request.headers.get(CSRF_HEADER) != "web":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF header")
    if not token:
        raise _unauthorized()
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Session expired") from None
    except jwt.InvalidTokenError:
        raise _unauthorized("Invalid token") from None
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError):
        raise _unauthorized("Invalid token") from None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("User disabled or deleted")
    return user


def require_role(minimum: Role) -> Callable[[User], User]:
    def checker(user: User = Depends(get_current_user)) -> User:
        if _ROLE_RANK[Role(user.role)] < _ROLE_RANK[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {minimum.value} role")
        return user

    return checker


require_viewer = require_role(Role.viewer)
require_operator = require_role(Role.operator)
require_admin = require_role(Role.admin)


def audit(db: Session, actor: User | str, action: str, target: str = "", **data: object) -> None:
    db.add(
        AuditLog(
            actor=actor.email if isinstance(actor, User) else actor,
            action=action,
            target=target,
            data=dict(data),
        )
    )
