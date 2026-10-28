"""Login throttling: per IP and per username, backed by the login_attempts table."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import LoginAttempt
from app.services import timeutil

IP_LIMIT = 20
IP_WINDOW = timedelta(minutes=10)
USER_FAIL_LIMIT = 5
LOCK_FOR = timedelta(minutes=15)


def record_attempt(db: Session, ip: str, username: str, success: bool) -> None:
    db.add(LoginAttempt(ts=timeutil.now_utc(), ip=ip[:64], username=username[:120], success=success))
    db.flush()


def ip_limited(db: Session, ip: str) -> bool:
    since = timeutil.now_utc() - IP_WINDOW
    n = db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.ip == ip, LoginAttempt.ts >= since))
    return (n or 0) >= IP_LIMIT


def unknown_user_locked(db: Session, username: str) -> bool:
    """Unknown names lock exactly like real ones so responses do not reveal accounts."""
    since = timeutil.now_utc() - LOCK_FOR
    last_ok = db.scalar(
        select(func.max(LoginAttempt.ts)).where(LoginAttempt.username == username, LoginAttempt.success.is_(True))
    )
    q = select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.username == username, LoginAttempt.success.is_(False), LoginAttempt.ts >= since
    )
    if last_ok is not None:
        q = q.where(LoginAttempt.ts > last_ok)
    return (db.scalar(q) or 0) >= USER_FAIL_LIMIT


def purge_old(db: Session) -> None:
    db.execute(delete(LoginAttempt).where(LoginAttempt.ts < timeutil.now_utc() - timedelta(days=2)))
