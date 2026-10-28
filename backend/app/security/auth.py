"""Sessions, cookies, CSRF and the permission dependencies."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import SessionRow, User
from app.security.permissions import ADMIN_PERMS, PERMS, has_perm
from app.services import timeutil

IDLE_TIMEOUT = timedelta(hours=12)
ABSOLUTE_TIMEOUT = timedelta(days=7)
TOUCH_EVERY = timedelta(minutes=1)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_EXEMPT_PATHS = {"/api/auth/login"}
CSRF_EXEMPT_PREFIXES = ("/api/retell/",)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User, ip: str, user_agent: str) -> tuple[str, SessionRow]:
    token = secrets.token_urlsafe(32)
    now = timeutil.now_utc()
    row = SessionRow(
        token_hash=token_hash(token),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        created_at=now,
        last_seen_at=now,
        expires_at=now + ABSOLUTE_TIMEOUT,
        ip=ip[:64],
        user_agent=user_agent[:300],
    )
    db.add(row)
    db.flush()
    return token, row


def set_session_cookie(response: Response, token: str) -> None:
    s = config.get_settings()
    response.set_cookie(
        s.cookie_name,
        token,
        max_age=int(ABSOLUTE_TIMEOUT.total_seconds()),
        path="/",
        secure=s.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    s = config.get_settings()
    response.delete_cookie(s.cookie_name, path="/", secure=s.cookie_secure, httponly=True, samesite="lax")


def delete_user_sessions(db: Session, user_id: int, except_id: int | None = None) -> None:
    stmt = delete(SessionRow).where(SessionRow.user_id == user_id)
    if except_id is not None:
        stmt = stmt.where(SessionRow.id != except_id)
    db.execute(stmt)


def lookup_session(db: Session, request: Request) -> tuple[User, SessionRow] | None:
    """Return the live (user, session) for the request cookie, or None.

    Expired sessions are deleted on sight. last_seen_at is touched at most once a minute.
    """
    token = request.cookies.get(config.get_settings().cookie_name)
    if not token or len(token) > 200:
        return None
    row = db.scalar(select(SessionRow).where(SessionRow.token_hash == token_hash(token)))
    if row is None:
        return None
    now = timeutil.now_utc()
    if now >= row.expires_at or now - row.last_seen_at >= IDLE_TIMEOUT:
        db.delete(row)
        db.commit()
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.active:
        db.delete(row)
        db.commit()
        return None
    if now - row.last_seen_at >= TOUCH_EVERY:
        row.last_seen_at = now
        db.commit()
    return user, row


def _not_signed_in() -> HTTPException:
    return HTTPException(status_code=401, detail="not_authenticated")


def current_session(request: Request, db: Session = Depends(get_db)) -> tuple[User, SessionRow]:
    found = lookup_session(db, request)
    if found is None:
        raise _not_signed_in()
    return found


def current_user_any(ctx: tuple[User, SessionRow] = Depends(current_session)) -> User:
    """Signed in, even if a password change is still pending (me, logout, change-password)."""
    return ctx[0]


def current_user(user: User = Depends(current_user_any)) -> User:
    """Signed in and past the forced password change."""
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="password_change_required")
    return user


def require(perm: str):
    """Dependency factory: `Depends(require("callbacks.work"))`. Tagged for the route walker test."""
    if perm not in PERMS and perm not in ADMIN_PERMS:
        raise KeyError(f"unknown permission {perm!r}")

    def dependency(user: User = Depends(current_user)) -> User:
        if not has_perm(user, perm):
            raise HTTPException(status_code=403, detail="forbidden")
        return user

    dependency.walnut_perm = perm  # type: ignore[attr-defined]
    return dependency


def require_login(user: User = Depends(current_user)) -> User:
    return user


def csrf_guard(request: Request, db: Session = Depends(get_db)) -> None:
    """Global dependency. Non-GET /api requests need X-CSRF-Token equal to the session token."""
    if request.method in SAFE_METHODS:
        return
    path = request.url.path
    if not path.startswith("/api"):
        return
    if path in CSRF_EXEMPT_PATHS or path.startswith(CSRF_EXEMPT_PREFIXES):
        return
    found = lookup_session(db, request)
    if found is None:
        return  # the route itself answers 401
    sent = request.headers.get("x-csrf-token", "")
    if not sent or not hmac.compare_digest(sent.encode(), found[1].csrf_token.encode()):
        raise HTTPException(status_code=403, detail="csrf")
