from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api import serializers
from app.api.schemas import SettingsPutBody, UserCreateBody, UserPatchBody
from app.db import get_db
from app.models import Event, User
from app.security.auth import delete_user_sessions, require
from app.security.passwords import generate_password, hash_password
from app.services import events, settings_store, timeutil

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _initials(name: str) -> str:
    parts = [p for p in name.replace(".", " ").split() if p and p.lower() != "dr"]
    return ("".join(p[0] for p in parts[:2]) or name[:1]).upper()[:4]


def _active_admin_count(db: Session, excluding: int | None = None) -> int:
    q = select(func.count()).select_from(User).where(User.is_admin.is_(True), User.active.is_(True))
    if excluding is not None:
        q = q.where(User.id != excluding)
    return db.scalar(q) or 0


# ---------------------------------------------------------------- users
@router.get("/users")
def list_users(user: User = Depends(require("admin.users")), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).order_by(User.id)).all()
    now = timeutil.now_utc()
    return {"items": [serializers.user_admin(u, now) for u in rows]}


@router.post("/users", status_code=201)
def create_user(body: UserCreateBody, actor: User = Depends(require("admin.users")), db: Session = Depends(get_db)):
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(status_code=409, detail="That username is taken")
    password = generate_password()
    u = User(
        username=body.username,
        email=body.email,
        display_name=body.display_name,
        title=body.title,
        initials=body.initials or _initials(body.display_name),
        role=body.role,
        is_admin=body.is_admin,
        password_hash=hash_password(password),
        must_change_password=True,
        active=True,
        created_at=timeutil.now_utc(),
    )
    db.add(u)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="That username is taken")
    events.record(
        db, events.ADMIN, "user_created", f"{actor.display_name} created the account {u.username} ({u.role})",
        actor, "user", u.id, {"role": u.role, "is_admin": u.is_admin},
    )
    db.commit()
    return {"item": serializers.user_admin(u), "password": password}


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatchBody, actor: User = Depends(require("admin.users")), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="No such user")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        return {"item": serializers.user_admin(u)}

    new_active = changes.get("active", u.active)
    new_admin = changes.get("is_admin", u.is_admin)
    if u.id == actor.id and (new_active is False or new_admin is False):
        raise HTTPException(status_code=409, detail="You cannot remove your own access")
    if u.is_admin and u.active and (not new_admin or not new_active) and _active_admin_count(db, excluding=u.id) == 0:
        raise HTTPException(status_code=409, detail="There must be at least one active admin")

    before = {k: getattr(u, k) for k in changes}
    for k, v in changes.items():
        setattr(u, k, v)
    if changes.get("active") is False or "role" in changes or "is_admin" in changes:
        # Re-sign-in picks up the new access right away.
        if changes.get("active") is False:
            delete_user_sessions(db, u.id)
    parts = []
    for k, v in changes.items():
        parts.append(f"{k} {before[k]} to {v}")
    action = "user_deactivated" if changes.get("active") is False else ("user_activated" if changes.get("active") is True else "user_updated")
    events.record(
        db, events.ADMIN, action, f"{actor.display_name} changed {u.username}: " + ", ".join(parts),
        actor, "user", u.id, {"before": before, "after": changes},
    )
    db.commit()
    return {"item": serializers.user_admin(u)}


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, actor: User = Depends(require("admin.users")), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="No such user")
    password = generate_password()
    u.password_hash = hash_password(password)
    u.must_change_password = True
    u.failed_count = 0
    u.locked_until = None
    delete_user_sessions(db, u.id)
    events.record(db, events.ADMIN, "password_reset", f"{actor.display_name} reset the password for {u.username}", actor, "user", u.id)
    db.commit()
    return {"item": serializers.user_admin(u), "password": password}


# ---------------------------------------------------------------- audit
def audit_query(
    tool: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    q: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
):
    stmt = select(Event)
    if tool:
        stmt = stmt.where(Event.tool == tool)
    if actor:
        stmt = stmt.where(Event.actor_name == actor)
    if action:
        stmt = stmt.where(Event.action == action)
    if entity_type:
        stmt = stmt.where(Event.entity_type == entity_type)
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(or_(Event.text.like(like, escape="\\"), Event.actor_name.like(like, escape="\\")))
    if since:
        stmt = stmt.where(Event.ts >= since if since.tzinfo else Event.ts >= since.replace(tzinfo=timezone.utc))
    if until:
        stmt = stmt.where(Event.ts < until if until.tzinfo else Event.ts < until.replace(tzinfo=timezone.utc))
    return stmt


@router.get("/audit")
def audit(
    user: User = Depends(require("admin.audit")),
    db: Session = Depends(get_db),
    tool: str | None = Query(default=None, max_length=40),
    actor: str | None = Query(default=None, max_length=120),
    action: str | None = Query(default=None, max_length=60),
    entity_type: str | None = Query(default=None, max_length=40),
    q: str | None = Query(default=None, max_length=200),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=1_000_000),
):
    stmt = audit_query(tool, actor, action, entity_type, q, since, until)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Event.id.desc()).limit(limit).offset(offset)).all()
    return {"items": [serializers.event_audit(e) for e in rows], "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------- settings
@router.get("/settings")
def get_settings(user: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    return {"settings": settings_store.get_all(db)}


@router.put("/settings")
def put_settings(body: SettingsPutBody, actor: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    if not body.settings:
        raise HTTPException(status_code=400, detail="Nothing to save")
    try:
        for k, v in body.settings.items():
            settings_store.put(db, k, v, by=actor.display_name)
    except settings_store.SettingError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    keys = ", ".join(sorted(body.settings))
    events.record(db, events.ADMIN, "settings_updated", f"{actor.display_name} changed settings: {keys}", actor, "setting", "", {"keys": sorted(body.settings)})
    db.commit()
    return {"settings": settings_store.get_all(db)}


# ---------------------------------------------------------------- reset demo
@router.post("/reset-demo")
def reset_demo(actor: User = Depends(require("admin.reset_demo")), db: Session = Depends(get_db)):
    from app.seed import reset_demo as do_reset

    counts = do_reset(db, actor)
    db.commit()
    return {"ok": True, "counts": counts}
