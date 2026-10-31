"""Team chat (the Chats button in the top bar): talk to colleagues directly. Messages are staff-only and never reach clients.

* "Everyone" is one conversation for the whole team (every active staff member is in it).
* A direct chat is between two people (one per pair). A group chat has a title and chosen members.
* Unread counts come from each member's last_read_at. New messages are pushed live (SSE entity "chats"); the server only says
  which conversation changed, and each browser fetches what it is allowed to see.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import Strict
from app.db import get_db
from app.models import ChatMember, ChatMessage, ChatThread, Contact, User
from app.security.auth import require
from app.services import crm, timeutil
from app.services import events as ev

router = APIRouter(prefix="/api/chats", tags=["chats"])
PERM = "home.view"  # every staff member


def _everyone(db: Session) -> ChatThread:
    t = db.scalar(select(ChatThread).where(ChatThread.kind == "everyone").limit(1))
    if t is None:
        t = ChatThread(kind="everyone", title="Everyone", created_at=timeutil.now_utc())
        db.add(t)
        db.flush()
    have = set(db.scalars(select(ChatMember.user_id).where(ChatMember.thread_id == t.id)).all())
    for uid in db.scalars(select(User.id).where(User.active.is_(True))).all():
        if uid not in have:
            db.add(ChatMember(thread_id=t.id, user_id=uid))
    db.flush()
    return t


def _member(db: Session, thread_id: int, user: User) -> tuple[ChatThread, ChatMember]:
    t = db.get(ChatThread, thread_id)
    m = db.scalar(select(ChatMember).where(ChatMember.thread_id == thread_id, ChatMember.user_id == user.id)) if t else None
    if t is None or m is None:
        raise HTTPException(status_code=404, detail="No such conversation")
    return t, m


def _people(db: Session, ids: set[int]) -> dict[int, User]:
    return {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids))).all()} if ids else {}


def _thread_dicts(db: Session, user: User, threads: list[ChatThread]) -> list[dict[str, Any]]:
    if not threads:
        return []
    tids = [t.id for t in threads]
    members = db.execute(select(ChatMember.thread_id, ChatMember.user_id, ChatMember.last_read_at).where(ChatMember.thread_id.in_(tids))).all()
    people = _people(db, {m.user_id for m in members})
    mine = {m.thread_id: m.last_read_at for m in members if m.user_id == user.id}
    out = []
    for t in threads:
        last = db.scalar(select(ChatMessage).where(ChatMessage.thread_id == t.id).order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(1))
        seen = mine.get(t.id)
        unread_q = select(func.count()).select_from(ChatMessage).where(ChatMessage.thread_id == t.id, ChatMessage.user_id != user.id)
        if seen is not None:
            unread_q = unread_q.where(ChatMessage.created_at > seen)
        others = [people[m.user_id] for m in members if m.thread_id == t.id and m.user_id != user.id and m.user_id in people]
        title = t.title if t.kind != "direct" else (others[0].display_name if others else "Just you")
        out.append({"id": t.id, "kind": t.kind, "title": title, "members": [{"id": p.id, "name": p.display_name, "initials": p.initials, "title": p.title} for p in others],
                    "last": {"body": last.body[:140], "at": timeutil.iso(last.created_at), "mine": last.user_id == user.id,
                             "who": people[last.user_id].display_name if last.user_id in people else ""} if last else None,
                    "unread": db.scalar(unread_q) or 0, "updated_at": timeutil.iso(t.last_message_at or t.created_at)})
    out.sort(key=lambda x: (x["kind"] != "everyone", x["last"] is None, "" if x["last"] is None else x["last"]["at"]), reverse=False)
    out.sort(key=lambda x: x["last"]["at"] if x["last"] else "", reverse=True)
    return out


@router.get("")
def list_threads(user: User = Depends(require(PERM)), db: Session = Depends(get_db)):
    _everyone(db)
    db.commit()
    tids = db.scalars(select(ChatMember.thread_id).where(ChatMember.user_id == user.id)).all()
    threads = list(db.scalars(select(ChatThread).where(ChatThread.id.in_(tids))).all()) if tids else []
    items = _thread_dicts(db, user, threads)
    staff = [{"id": u.id, "name": u.display_name, "initials": u.initials, "title": u.title}
             for u in db.scalars(select(User).where(User.active.is_(True), User.id != user.id).order_by(User.display_name)).all()]
    return {"items": items, "unread": sum(i["unread"] for i in items), "staff": staff}


class NewThread(Strict):
    user_ids: list[int] = Field(min_length=1, max_length=30)
    title: str = Field(default="", max_length=80)


@router.post("", status_code=201)
def new_thread(body: NewThread, user: User = Depends(require(PERM)), db: Session = Depends(get_db)):
    ids = sorted({i for i in body.user_ids if i != user.id})
    found = set(db.scalars(select(User.id).where(User.id.in_(ids), User.active.is_(True))).all())
    if not ids or found != set(ids):
        raise HTTPException(status_code=400, detail="Pick colleagues who are active.")
    if len(ids) == 1:
        key = f"{min(user.id, ids[0])}:{max(user.id, ids[0])}"
        t = db.scalar(select(ChatThread).where(ChatThread.pair_key == key))
        if t is None:
            t = ChatThread(kind="direct", pair_key=key, created_by=user.id, created_at=timeutil.now_utc())
            db.add(t)
            db.flush()
            db.add_all([ChatMember(thread_id=t.id, user_id=user.id), ChatMember(thread_id=t.id, user_id=ids[0])])
    else:
        names = [u.display_name.split()[0] for u in _people(db, set(ids)).values()]
        t = ChatThread(kind="group", title=(body.title.strip() or ", ".join(sorted(names))[:80]), created_by=user.id, created_at=timeutil.now_utc())
        db.add(t)
        db.flush()
        db.add_all([ChatMember(thread_id=t.id, user_id=i) for i in [user.id, *ids]])
    db.flush()
    ev.queue_publish(db, "chats", t.id, None)
    db.commit()
    return {"item": _thread_dicts(db, user, [t])[0]}


@router.get("/{thread_id}/messages")
def messages(thread_id: int, before: datetime | None = None, limit: int = Query(default=50, ge=1, le=200),
             user: User = Depends(require(PERM)), db: Session = Depends(get_db)):
    t, m = _member(db, thread_id, user)
    q = select(ChatMessage).where(ChatMessage.thread_id == t.id)
    if before is not None:
        q = q.where(ChatMessage.created_at < crm.aware(before))
    rows = list(reversed(db.scalars(q.order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(limit + 1)).all()))
    more = len(rows) > limit
    rows = rows[-limit:]
    people = _people(db, {r.user_id for r in rows if r.user_id})
    cids = {r.contact_id for r in rows if r.contact_id}
    contacts = {c.id: c.display_name for c in db.scalars(select(Contact).where(Contact.id.in_(cids), Contact.erased_at.is_(None))).all()} if cids else {}
    m.last_read_at = timeutil.now_utc()  # reading changes only this person's badge: no live push, so open chats never ping-pong
    db.commit()
    return {"thread": _thread_dicts(db, user, [t])[0], "more": more,
            "items": [{"id": r.id, "body": r.body, "at": timeutil.iso(r.created_at), "mine": r.user_id == user.id,
                       "who": {"id": r.user_id, "name": people[r.user_id].display_name, "initials": people[r.user_id].initials} if r.user_id in people else None,
                       "contact": {"id": r.contact_id, "name": contacts[r.contact_id]} if r.contact_id in contacts else None} for r in rows]}


class NewMessage(Strict):
    body: str = Field(min_length=1, max_length=4000)
    contact_id: int | None = None


@router.post("/{thread_id}/messages", status_code=201)
def send(thread_id: int, body: NewMessage, user: User = Depends(require(PERM)), db: Session = Depends(get_db)):
    t, m = _member(db, thread_id, user)
    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Write something first.")
    if body.contact_id is not None:
        c = db.get(Contact, body.contact_id)
        if c is None or c.erased_at is not None:
            raise HTTPException(status_code=400, detail="That contact was not found.")
    now = timeutil.now_utc()
    msg = ChatMessage(thread_id=t.id, user_id=user.id, body=text, contact_id=body.contact_id, created_at=now)
    db.add(msg)
    t.last_message_at = now
    m.last_read_at = now
    db.flush()
    ev.queue_publish(db, "chats", t.id, None)
    db.commit()
    return {"item": {"id": msg.id, "body": msg.body, "at": timeutil.iso(msg.created_at), "mine": True,
                     "who": {"id": user.id, "name": user.display_name, "initials": user.initials}, "contact": None}}


@router.post("/{thread_id}/read")
def mark_read(thread_id: int, user: User = Depends(require(PERM)), db: Session = Depends(get_db)):
    t, m = _member(db, thread_id, user)
    m.last_read_at = timeutil.now_utc()
    db.commit()
    return {"ok": True}
