from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serializers
from app.api.schemas import ChangePasswordBody, LoginBody
from app.db import get_db
from app.models import SessionRow, User
from app.security import ratelimit
from app.security.auth import (
    clear_session_cookie,
    create_session,
    current_session,
    delete_user_sessions,
    lookup_session,
    set_session_cookie,
)
from app.security.passwords import DUMMY_HASH, check_new_password, hash_password, needs_rehash, verify_password
from app.security.permissions import perms_for
from app.services import events, settings_store, timeutil

router = APIRouter(prefix="/api/auth", tags=["auth"])

WRONG = "Wrong username or password"
LOCKED = "Too many tries. Wait 15 minutes."
IP_BLOCKED = "Too many tries from this device. Wait a few minutes."


def _me_payload(db: Session, user: User, session: SessionRow) -> dict:
    return {
        "user": serializers.user_public(user),
        "perms": perms_for(user),
        "csrf_token": session.csrf_token,
        "settings": settings_store.public(db),
    }


def _fail(db: Session, status: int, detail: str) -> HTTPException:
    db.commit()  # keep the attempt record and the failure event
    return HTTPException(status_code=status, detail=detail)


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = events.current_ip.get() or (request.client.host if request.client else "")
    username = body.username.strip().lower()
    now = timeutil.now_utc()

    if ratelimit.ip_limited(db, ip):
        raise HTTPException(status_code=429, detail=IP_BLOCKED)
    ratelimit.purge_old(db)

    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        verify_password(DUMMY_HASH, body.password)
        ratelimit.record_attempt(db, ip, username, False)
        if ratelimit.unknown_user_locked(db, username):
            raise _fail(db, 423, LOCKED)
        raise _fail(db, 401, WRONG)

    if user.locked_until and user.locked_until > now:
        ratelimit.record_attempt(db, ip, username, False)
        raise _fail(db, 423, LOCKED)

    ok = verify_password(user.password_hash, body.password)
    if not ok or not user.active:
        ratelimit.record_attempt(db, ip, username, False)
        if user.active:
            user.failed_count += 1
            events.record(db, events.SIGN_IN, "login_failed", f"Failed sign-in for {user.username}", user, "user", user.id, {"failed_count": user.failed_count})
            if user.failed_count >= ratelimit.USER_FAIL_LIMIT:
                user.locked_until = now + ratelimit.LOCK_FOR
                user.failed_count = 0
                events.record(db, events.SIGN_IN, "account_locked", f"{user.username} locked for 15 minutes after too many tries", user, "user", user.id)
                raise _fail(db, 423, LOCKED)
        raise _fail(db, 401, WRONG)

    ratelimit.record_attempt(db, ip, username, True)
    user.failed_count = 0
    user.locked_until = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    token, session = create_session(db, user, ip, request.headers.get("user-agent", ""))
    events.record(db, events.SIGN_IN, "login", f"{user.display_name} signed in", user, "user", user.id)
    db.commit()
    set_session_cookie(response, token)
    return _me_payload(db, user, session)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    found = lookup_session(db, request)
    if found is not None:
        user, session = found
        db.delete(session)
        events.record(db, events.SIGN_IN, "logout", f"{user.display_name} signed out", user, "user", user.id)
        db.commit()
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(ctx=Depends(current_session), db: Session = Depends(get_db)):
    user, session = ctx
    return _me_payload(db, user, session)


@router.post("/change-password")
def change_password(body: ChangePasswordBody, ctx=Depends(current_session), db: Session = Depends(get_db)):
    user, session = ctx
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status_code=400, detail="Your current password is not right.")
    problem = check_new_password(body.new_password, body.current_password, user.username)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.failed_count = 0
    user.locked_until = None
    delete_user_sessions(db, user.id, except_id=session.id)
    events.record(db, events.SIGN_IN, "password_changed", f"{user.display_name} changed their password", user, "user", user.id)
    db.commit()
    return _me_payload(db, user, session)
