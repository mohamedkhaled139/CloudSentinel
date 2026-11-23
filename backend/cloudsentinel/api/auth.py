"""Authentication, first-run setup and user management."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cloudsentinel.config import get_settings
from cloudsentinel.db import get_db
from cloudsentinel.deps import COOKIE_NAME, audit, get_current_user, require_admin
from cloudsentinel.models import Role, User, utcnow
from cloudsentinel.schemas import (
    LoginRequest,
    PasswordChange,
    SetupRequest,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
)
from cloudsentinel.security import create_access_token, hash_password, verify_password

router = APIRouter(tags=["auth"])


class LoginRateLimiter:
    """In-memory limiter: at most ``limit`` failed attempts per key within ``window`` seconds."""

    def __init__(self, limit: int = 5, window: float = 300) -> None:
        self.limit, self.window = limit, window
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._failures[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.monotonic())) >= self.limit

    def fail(self, key: str) -> None:
        with self._lock:
            self._prune(key, time.monotonic()).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


limiter = LoginRateLimiter()
# Verified against when the account does not exist, to keep response timing uniform.
_DUMMY_HASH: str | None = None


def _issue(response: Response, user: User) -> TokenResponse:
    settings = get_settings()
    token = create_access_token(str(user.id), user.role)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.access_token_ttl_minutes * 60,
        httponly=True,
        samesite="strict",
        secure=settings.environment == "production",
        path="/",
    )
    return TokenResponse(
        access_token=token,
        expires_in=settings.access_token_ttl_minutes * 60,
        user=UserOut.model_validate(user),
    )


@router.get("/auth/setup-status")
def setup_status(db: Session = Depends(get_db)) -> dict[str, bool]:
    return {"needs_setup": (db.scalar(select(func.count(User.id))) or 0) == 0}


@router.post("/auth/setup", response_model=TokenResponse, status_code=201)
def setup(body: SetupRequest, response: Response, db: Session = Depends(get_db)) -> TokenResponse:
    """Create the first administrator. Only allowed while no users exist."""
    if (db.scalar(select(func.count(User.id))) or 0) > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup has already been completed")
    user = User(
        email=body.email,
        name=body.name,
        password_hash=hash_password(body.password),
        role=Role.admin,
        last_login_at=utcnow(),
    )
    db.add(user)
    db.flush()
    audit(db, user, "setup.admin_created", user.email)
    db.commit()
    return _issue(response, user)


@router.post("/auth/login", response_model=TokenResponse)
def login(
    body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> TokenResponse:
    global _DUMMY_HASH
    email = body.email.strip().lower()
    key = f"{request.client.host if request.client else '?'}:{email}"
    if limiter.blocked(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts; try again later")
    user = db.scalars(select(User).where(User.email == email)).first()
    if user is None:
        _DUMMY_HASH = _DUMMY_HASH or hash_password("not-a-real-password")
        verify_password(body.password, _DUMMY_HASH)
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        limiter.fail(key)
        audit(db, email, "auth.login_failed")
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    limiter.reset(key)
    user.last_login_at = utcnow()
    audit(db, user, "auth.login")
    db.commit()
    return _issue(response, user)


@router.post("/auth/logout", status_code=204)
def logout(response: Response) -> Response:
    response.delete_cookie(COOKIE_NAME, path="/")
    response.status_code = 204
    return response


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/auth/password", status_code=204)
def change_password(
    body: PasswordChange, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    audit(db, user, "auth.password_changed")
    db.commit()
    return Response(status_code=204)


# ---- user administration --------------------------------------------------------------------------
@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[User]:
    return list(db.scalars(select(User).order_by(User.email)))


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> User:
    if db.scalars(select(User).where(User.email == body.email)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
    user = User(email=body.email, name=body.name, role=body.role, password_hash=hash_password(body.password))
    db.add(user)
    audit(db, admin, "user.created", body.email, role=body.role)
    db.commit()
    return user


def _active_admins(db: Session) -> int:
    return (
        db.scalar(select(func.count(User.id)).where(User.role == Role.admin, User.is_active.is_(True))) or 0
    )


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int, body: UserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    demoting = (body.role is not None and body.role != Role.admin) or body.is_active is False
    if user.role == Role.admin and demoting and _active_admins(db) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot remove the last active administrator")
    if body.name is not None:
        user.name = body.name
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password:
        user.password_hash = hash_password(body.password)
    audit(
        db,
        admin,
        "user.updated",
        user.email,
        fields=sorted(body.model_dump(exclude_none=True, exclude={"password"})),
    )
    db.commit()
    return user


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> Response:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot delete your own account")
    if user.role == Role.admin and _active_admins(db) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot remove the last active administrator")
    db.delete(user)
    audit(db, admin, "user.deleted", user.email)
    db.commit()
    return Response(status_code=204)
