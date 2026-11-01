"""The staff assistant with AI: answers questions about the clinic's data with lookup tools, and proposes changes that a person confirms.

* Every tool runs with the signed-in person's own permissions (a tool they may not use is not even offered to the model).
* The model only sees what the tools return. Recordings, secrets and keys are never available to it. A call transcript is
  available only to people who may read transcripts on the Phone page (owners), one call at a time, and each read is in the access log.
* Changes: the model can only call propose_change. The server checks the action and the person's permission, keeps the proposal for
  15 minutes, and shows it as a card. Nothing changes until the person presses Confirm; the permission is checked again then.
* Off unless the owner turns on "AI for staff" in Admin > AI assistant (it sends clinic data to OpenRouter).
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import date, timedelta
from typing import Any, Callable

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.adapters import llm as llm_adapter
from app.assistant import ai, booking, upsell
from app.models import (Automation, CalendarEvent, Campaign, Contact, Creative, Fee, KnowledgeEntry, Message, Pet, PhoneCall, Product, Task, User, Visit)
from app.security import crypto
from app.security.permissions import has_perm
from app.services import crm, settings_store, sms_mode, task_view, timeutil
from app.services import events as ev
from app.services import knowledge as kn

MAX_STEPS = 8
MAX_TOOL_CHARS = 12000
PENDING_SECONDS = 900
_pending: dict[str, dict[str, Any]] = {}


class AgentFailed(Exception):
    pass


def enabled(db: Session) -> bool:
    cfg = ai.get_config(db)
    return bool(cfg.get("staff_enabled") and cfg.get("key_enc"))


# ---------------------------------------------------------------- lookups (each returns small JSON)
def _d(s: str | None, default: date) -> date:
    try:
        return date.fromisoformat(s) if s else default
    except ValueError:
        return default


def _cbrief(c: Contact | None) -> dict | None:
    return None if c is None else {"id": c.id, "name": c.display_name, "kind": c.kind}


def t_overview(db: Session, user: User, **_) -> dict:
    today = timeutil.today_local()
    lo, hi = crm.local_day_start(today), crm.local_day_start(today + timedelta(days=1))
    m = sms_mode.get(db)
    return {
        "today": today.isoformat(), "contacts": db.scalar(select(func.count()).select_from(Contact).where(Contact.erased_at.is_(None))),
        "open_tasks": db.scalar(select(func.count()).select_from(Task).where(Task.status.in_(("open", "in_progress")))),
        "high_priority_open_tasks": db.scalar(select(func.count()).select_from(Task).where(Task.status.in_(("open", "in_progress")), Task.priority == "high")),
        "events_today": db.scalar(select(func.count()).select_from(CalendarEvent).where(CalendarEvent.starts_at >= lo, CalendarEvent.starts_at < hi, CalendarEvent.status != "cancelled")),
        "requested_appointments_waiting": db.scalar(select(func.count()).select_from(CalendarEvent).where(CalendarEvent.status == "requested")),
        "calls_today": db.scalar(select(func.count()).select_from(PhoneCall).where(PhoneCall.started_at >= lo, PhoneCall.started_at < hi)),
        "texts_in_today": db.scalar(select(func.count()).select_from(Message).where(Message.channel == "sms", Message.direction == "in", Message.created_at >= lo)),
        "pending_knowledge": db.scalar(select(func.count()).select_from(KnowledgeEntry).where(KnowledgeEntry.status == "pending")),
        "approved_knowledge": db.scalar(select(func.count()).select_from(KnowledgeEntry).where(KnowledgeEntry.status == "approved")),
        "pending_fees": db.scalar(select(func.count()).select_from(Fee).where(Fee.status == "pending")),
        "settings": {"texting_mode": m["mode"], "test_numbers_count": len(m["test_numbers"]), "automatic_replies": bool(settings_store.get_raw(db, "auto_reply", False)),
                     "booking_by_text": booking.get_hours(db)["enabled"], "product_suggestions": upsell.get(db)["enabled"],
                     "ai_writes_client_replies": bool(ai.get_config(db)["enabled"]), "ai_model": ai.get_config(db)["model"],
                     "client_assistant_name": ai.get_config(db).get("agent_name"), "call_transcripts": bool(ai.get_config(db).get("transcribe_enabled")),
                     "transcript_model": ai.get_config(db).get("transcribe_model")},
        "callback_tasks_open": db.scalar(select(func.count()).select_from(Task).where(Task.kind == "callback", Task.status.in_(("open", "in_progress")))),
    }


def t_search(db: Session, user: User, query: str = "", **_) -> dict:
    q = (query or "").strip()
    if len(q) < 2:
        return {"error": "query too short"}
    like = crm.like_pattern(q)
    digits = re.sub(r"\D", "", q)
    cconds = [Contact.display_name.like(like, escape="\\"), Contact.email.like(like, escape="\\")]
    if len(digits) >= 3:
        cconds.append(Contact.phone.like(f"%{digits}%"))
    contacts = db.scalars(select(Contact).where(Contact.erased_at.is_(None), or_(*cconds)).limit(10)).all()
    pets = db.execute(select(Pet, Contact).join(Contact, Contact.id == Pet.contact_id).where(Pet.name.like(like, escape="\\")).limit(10)).all()
    tasks = db.scalars(select(Task).where(Task.title.like(like, escape="\\")).order_by(Task.id.desc()).limit(8)).all() if has_perm(user, "tasks.view") else []
    msgs = db.execute(select(Message, Contact).join(Contact, Contact.id == Message.contact_id).where(Message.body.like(like, escape="\\"), Message.channel.in_(("sms", "email")))
                      .order_by(Message.id.desc()).limit(8)).all() if has_perm(user, "messages.view") else []
    return {"contacts": [_cbrief(c) for c in contacts],
            "pets": [{"pet": p.name, "species": p.species, "owner": _cbrief(c)} for p, c in pets],
            "tasks": [{"id": t.id, "title": t.title, "status": t.status} for t in tasks],
            "messages": [{"contact": _cbrief(c), "direction": m.direction, "text": m.body[:200], "at": timeutil.iso(m.created_at)} for m, c in msgs]}


def t_contact(db: Session, user: User, contact_id: int = 0, **_) -> dict:
    c = db.get(Contact, int(contact_id or 0))
    if c is None or c.erased_at is not None:
        return {"error": "no such contact"}
    crm.log_access(db, user, c.id, "view", detail="staff assistant", dedupe_seconds=60)
    msgs = db.scalars(select(Message).where(Message.contact_id == c.id).order_by(Message.created_at.desc(), Message.id.desc()).limit(10)).all()
    return {
        **_cbrief(c), "phone": crm.phone_display(c.phone) if c.phone else None, "email": c.email, "text_consent": c.sms_consent, "email_consent": c.email_consent,
        "assistant_on": c.ai_enabled, "notes": (c.preference_notes or "")[:400], "owner": (db.get(User, c.owner_user_id).display_name if c.owner_user_id else None),
        "pets": [{"name": p.name, "species": p.species, "breed": p.breed} for p in db.scalars(select(Pet).where(Pet.contact_id == c.id)).all()],
        "visits": db.scalar(select(func.count()).select_from(Visit).where(Visit.contact_id == c.id)),
        "open_tasks": [{"id": t.id, "title": t.title, "priority": t.priority} for t in db.scalars(select(Task).where(Task.contact_id == c.id, Task.status.in_(("open", "in_progress")))).all()],
        "upcoming": [{"id": e.id, "title": e.title, "at": timeutil.iso(e.starts_at), "status": e.status} for e in db.scalars(select(CalendarEvent).where(CalendarEvent.contact_id == c.id, CalendarEvent.starts_at >= timeutil.now_utc())).all()],
        "calls": db.scalar(select(func.count()).select_from(PhoneCall).where(PhoneCall.contact_id == c.id)),
        "patient_or_inquiry": c.kind, "what_the_client_assistant_remembers": (c.ai_memory or "")[:800],
        "recent_calls": [_call(r, user) for r in db.scalars(select(PhoneCall).where(PhoneCall.contact_id == c.id).order_by(PhoneCall.started_at.desc()).limit(5)).all()],
        "recent_messages": [{"channel": m.channel, "direction": m.direction, "by": m.sender_kind, "text": (m.body or "")[:240], "at": timeutil.iso(m.created_at), "status": m.delivery_state} for m in msgs],
    }


def t_tasks(db: Session, user: User, status: str = "active", priority: str = "", kind: str = "", assigned: str = "", limit: int = 20, **_) -> dict:
    stmt = select(Task)
    if assigned == "me":
        stmt = stmt.where(Task.assigned_to == user.id)
    elif assigned == "unassigned":
        stmt = stmt.where(Task.assigned_to.is_(None))
    elif assigned == "me_or_unassigned":
        stmt = stmt.where(or_(Task.assigned_to == user.id, Task.assigned_to.is_(None)))
    elif assigned:
        u = db.scalar(select(User).where(func.lower(User.display_name) == assigned.lower()))
        stmt = stmt.where(Task.assigned_to == (u.id if u else -1))
    stmt = stmt.where(Task.status.in_(("open", "in_progress"))) if status == "active" else stmt.where(Task.status == status) if status in ("open", "in_progress", "done") else stmt
    if priority in ("high", "normal", "low"):
        stmt = stmt.where(Task.priority == priority)
    if kind:
        stmt = stmt.where(Task.kind == kind)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Task.created_at.desc()).limit(min(int(limit or 25), 60))).all()
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    cids = {t.contact_id for t in rows if t.contact_id}
    people = {c.id: c.display_name for c in db.scalars(select(Contact).where(Contact.id.in_(cids))).all()} if cids else {}
    out, groups = [], {}
    for t in rows:
        g = task_view.glance(t.title, t.reason, t.kind, crm.TASK_KIND_LABELS.get(t.kind, t.kind), people.get(t.contact_id))
        grp = "urgent" if t.priority == "high" else g["group"]
        groups[grp] = groups.get(grp, 0) + 1
        out.append({"id": t.id, "label": g["label"], "who": g["who"], "phone": g["phone"] or None, "summary": g["summary"], "next_step": g["next_step"] or None,
                    "appointment_asked_for": g["appointment"] or None, "when": g["when"] or None, "priority": t.priority, "status": t.status, "raised_by": t.raised_by,
                    "assigned_to": names.get(t.assigned_to), "contact_id": t.contact_id, "created": timeutil.iso(t.created_at)})
    return {"total_matching": total, "shown": len(out), "by_group": groups, "tasks": out}


def t_calendar(db: Session, user: User, from_date: str = "", to_date: str = "", **_) -> dict:
    today = timeutil.today_local()
    a, b = _d(from_date, today), _d(to_date, today + timedelta(days=7))
    rows = db.scalars(select(CalendarEvent).where(CalendarEvent.starts_at >= crm.local_day_start(a), CalendarEvent.starts_at < crm.local_day_start(b + timedelta(days=1)))
                      .order_by(CalendarEvent.starts_at).limit(80)).all()
    return {"from": a.isoformat(), "to": b.isoformat(), "events": [{"id": e.id, "title": e.title, "kind": e.kind, "status": e.status, "at": booking.label(crm.aware(e.starts_at)), "contact_id": e.contact_id} for e in rows],
            "booking_by_text": booking.get_hours(db)["enabled"], "next_open_times": [booking.label(x) for x in booking.free_times(db, 30, n=5)],
            **({"open_30_minute_times_that_day": [booking.label(x) for x in booking.free_times(db, 30, n=40, day=a, staff=True)]} if a == b else {})}


def t_calls(db: Session, user: User, from_date: str = "", to_date: str = "", handled_by: str = "", result: str = "", contact_id: int = 0, limit: int = 20, **_) -> dict:
    today = timeutil.today_local()
    a, b = _d(from_date, today - timedelta(days=7)), _d(to_date, today)
    stmt = select(PhoneCall).where(PhoneCall.started_at >= crm.local_day_start(a), PhoneCall.started_at < crm.local_day_start(b + timedelta(days=1)))
    if handled_by in ("human", "ai", "none"):
        stmt = stmt.where(PhoneCall.handled_by == handled_by)
    if result:
        stmt = stmt.where(PhoneCall.result == result)
    if contact_id:
        stmt = stmt.where(PhoneCall.contact_id == int(contact_id))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    by_result = dict(db.execute(select(PhoneCall.result, func.count()).where(PhoneCall.started_at >= crm.local_day_start(a), PhoneCall.started_at < crm.local_day_start(b + timedelta(days=1))).group_by(PhoneCall.result)).all())
    rows = db.scalars(stmt.order_by(PhoneCall.started_at.desc()).limit(min(int(limit or 20), 40))).all()
    return {"from": a.isoformat(), "to": b.isoformat(), "total": total, "by_result": by_result,
            "by_reason": dict(db.execute(select(PhoneCall.intent, func.count()).where(PhoneCall.started_at >= crm.local_day_start(a), PhoneCall.started_at < crm.local_day_start(b + timedelta(days=1)),
                                                                     PhoneCall.intent.is_not(None)).group_by(PhoneCall.intent)).all()),
            "patients_vs_inquiries": dict(db.execute(select(PhoneCall.call_type, func.count()).where(PhoneCall.started_at >= crm.local_day_start(a), PhoneCall.started_at < crm.local_day_start(b + timedelta(days=1)))
                                                     .group_by(PhoneCall.call_type)).all()),
            "calls": [_call(r, user) for r in rows]}


def _call(r: PhoneCall, user: User) -> dict:
    out = {"id": r.id, "at": timeutil.iso(r.started_at), "direction": r.direction, "handled_by": r.handled_by, "line": r.agent_label, "seconds": r.duration_seconds,
           "result": r.result, "note": r.short_note, "reason": r.intent, "patient_or_inquiry": r.call_type, "contact_id": r.contact_id, "has_recording": bool(r.recording_id),
           "transcribed": bool(r.transcript)}
    if r.summary and has_perm(user, "phone.transcript"):
        out["summary"] = r.summary  # owners only, like on the Phone page; full transcripts stay on the Phone page
    return out


def t_texts(db: Session, user: User, limit: int = 15, **_) -> dict:
    rows = db.execute(select(Message, Contact).join(Contact, Contact.id == Message.contact_id).where(Message.channel == "sms").order_by(Message.created_at.desc(), Message.id.desc())
                      .limit(min(int(limit or 15), 40))).all()
    return {"texts": [{"contact": _cbrief(c), "direction": m.direction, "by": m.sender_kind, "text": (m.body or "")[:240], "at": timeutil.iso(m.created_at), "status": m.delivery_state} for m, c in rows]}


def t_knowledge(db: Session, user: User, status: str = "approved", query: str = "", **_) -> dict:
    stmt = select(KnowledgeEntry)
    if status in ("pending", "approved", "declined", "retired"):
        stmt = stmt.where(KnowledgeEntry.status == status)
    if query:
        like = crm.like_pattern(query)
        stmt = stmt.where(or_(KnowledgeEntry.title.like(like, escape="\\"), KnowledgeEntry.body.like(like, escape="\\")))
    rows = db.scalars(stmt.order_by(KnowledgeEntry.id).limit(60)).all()
    return {"entries": [{"id": e.id, "status": e.status, "category": e.category, "title": e.title, "text": e.body[:500], "source": e.source_ref or e.source_kind} for e in rows]}


def t_fees(db: Session, user: User, status: str = "all", **_) -> dict:
    stmt = select(Fee) if status == "all" else select(Fee).where(Fee.status == status)
    return {"fees": [{"id": f.id, "service": f.service, "dollars": None if f.price_cents is None else f.price_cents / 100, "unit": f.unit, "status": f.status, "note": f.note[:150]}
                     for f in db.scalars(stmt.order_by(Fee.service)).all()],
            "products": [{"id": p.id, "name": p.name, "dollars": None if p.price_cents is None else p.price_cents / 100, "status": p.status, "says": upsell.line(p)}
                         for p in db.scalars(select(Product).order_by(Product.name)).all()]}


def t_campaigns(db: Session, user: User, **_) -> dict:
    return {"campaigns": [{"id": c.id, "name": c.name, "channel": c.channel, "status": c.status, "counts": json.loads(c.counts_json or "{}")}
                          for c in db.scalars(select(Campaign).order_by(Campaign.id.desc()).limit(20)).all()]}


def t_report(db: Session, user: User, key: str = "agents", from_date: str = "", to_date: str = "", **_) -> dict:
    from app.api import reports as R
    fn = R.RUNNERS.get(key)
    if fn is None:
        return {"error": "unknown report", "reports": list(R.RUNNERS)}
    today = timeutil.today_local()
    a, b = _d(from_date, today - timedelta(days=29)), _d(to_date, today)
    out = fn(db, a, b)
    return {"report": key, "from": a.isoformat(), "to": b.isoformat(), **out}


def t_system(db: Session, user: User, **_) -> dict:
    """The server and the helpers, as far as the web app can see them. Nothing secret."""
    import os
    import shutil
    from app import config as app_config
    from app.api import admin_telus
    cfg = app_config.get_settings()
    repo = "/opt/walnut-desk"
    commit = None
    try:
        head = open(os.path.join(repo, ".git", "HEAD")).read().strip()
        commit = open(os.path.join(repo, ".git", head[5:])).read().strip()[:7] if head.startswith("ref: ") else head[:7]
    except OSError:
        pass
    data_dir = os.path.dirname(cfg.db_path)
    du = shutil.disk_usage(data_dir)
    backups = []
    try:
        bdir = os.path.join(data_dir, "backups")
        backups = sorted((f for f in os.listdir(bdir) if f.endswith(".gz")), reverse=True)
    except OSError:
        pass
    st = admin_telus.read_status() or {}
    conn, job = st.get("connection") or {}, st.get("job") or {}
    def count(sub):
        try:
            return len([f for f in os.listdir(os.path.join(admin_telus._bridge(), *sub)) if f.endswith(".json")])
        except OSError:
            return None
    since = timeutil.now_utc() - timedelta(hours=24)
    last_in = db.scalar(select(func.max(Message.created_at)).where(Message.channel == "sms", Message.direction == "in"))
    return {
        "app_version": commit, "database_mb": round(os.path.getsize(cfg.db_path) / 1e6, 1),
        "disk": {"used_percent": round(100 * du.used / du.total), "free_gb": round(du.free / 1e9, 1)},
        "backups": {"count": len(backups), "latest": backups[0] if backups else None, "kept_days": 14},
        "telus_read_helper": {"connection": conn.get("state"), "code": conn.get("code"), "checked_at": conn.get("checked_at"),
                              "last_job": {"mode": job.get("mode"), "state": job.get("state"), "code": job.get("code"), "finished_at": job.get("finished_at")}},
        "texting": {**sms_mode.get(db), "sends_waiting": count(("send", "requests")), "results_kept": count(("send", "results")),
                    "last_text_in": timeutil.iso(last_in)},
        "assistant_last_24h": {"automatic_replies": db.scalar(select(func.count()).select_from(Message).where(Message.sender_kind == "ai", Message.direction == "out", Message.created_at > since))},
        "not_visible_here": "service states, the HTTPS certificate and memory are checked on the server with the walnut-health command",
    }


def t_users(db: Session, user: User, **_) -> dict:
    from app.models import SessionRow
    last = dict(db.execute(select(SessionRow.user_id, func.max(SessionRow.last_seen_at)).group_by(SessionRow.user_id)).all())
    return {"staff": [{"name": u.display_name, "username": u.username, "role": u.role, "admin": u.is_admin, "active": u.active,
                       "last_active": timeutil.iso(last.get(u.id))} for u in db.scalars(select(User).order_by(User.id)).all()]}


def t_audit(db: Session, user: User, limit: int = 25, contains: str = "", **_) -> dict:
    from app.models import Event
    stmt = select(Event)
    if contains:
        stmt = stmt.where(Event.text.like(crm.like_pattern(contains), escape="\\"))
    rows = db.scalars(stmt.order_by(Event.id.desc()).limit(min(int(limit or 25), 60))).all()
    return {"entries": [{"at": timeutil.iso(e.ts), "area": e.tool, "who": e.actor_name, "what": e.text} for e in rows]}


# ---------------------------------------------------------------- changes the model may propose (executed only on Confirm)
def t_website_traffic(db: Session, user: User, days: int = 30, **_) -> dict:
    """Visitors and page views of the clinic website (Vercel Web Analytics), with top pages, sources, countries and devices."""
    from app.services import web_analytics
    d = int(days or 30)
    d = 7 if d <= 7 else 30 if d <= 30 else 90
    try:
        out = web_analytics.dashboard(db, d)
    except web_analytics.NotConnected:
        return {"error": "the website's Vercel project is not connected (SEO agent, Deployment agent)"}
    out.pop("series", None)
    return out


def t_people_waiting(db: Session, user: User, limit: int = 20, **_) -> dict:
    """Contacts whose latest message in is not read yet (the Home page's "people waiting")."""
    from app.api.home import _unread_flag
    unread = _unread_flag()
    base = select(Contact).where(Contact.erased_at.is_(None), unread)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(Contact.last_active_at.desc()).limit(min(int(limit or 20), 40))).all()
    out = []
    for c in rows:
        m = db.scalar(select(Message).where(Message.contact_id == c.id, Message.direction == "in").order_by(Message.created_at.desc()).limit(1))
        out.append({**_cbrief(c), "last_in": (m.body or "")[:160] if m else None, "channel": m.channel if m else None, "at": timeutil.iso(m.created_at) if m else None})
    return {"total_waiting": total, "people": out}


def t_contacts_summary(db: Session, user: User, days: int = 7, **_) -> dict:
    """How many patients and inquiries, how many are new, and where they came from."""
    since = timeutil.now_utc() - timedelta(days=max(1, min(int(days or 7), 365)))
    live = select(Contact).where(Contact.erased_at.is_(None))
    by_kind = dict(db.execute(select(Contact.kind, func.count()).where(Contact.erased_at.is_(None)).group_by(Contact.kind)).all())
    new = db.scalars(live.where(Contact.created_at >= since).order_by(Contact.created_at.desc()).limit(25)).all()
    return {"by_kind": by_kind, "total": sum(by_kind.values()), "days": int(days or 7),
            "new_in_period": db.scalar(select(func.count()).select_from(live.where(Contact.created_at >= since).subquery())),
            "new_by_kind": dict(db.execute(select(Contact.kind, func.count()).where(Contact.erased_at.is_(None), Contact.created_at >= since).group_by(Contact.kind)).all()),
            "new_by_source": dict(db.execute(select(Contact.source, func.count()).where(Contact.erased_at.is_(None), Contact.created_at >= since).group_by(Contact.source)).all()),
            "newest": [{**_cbrief(c), "created": timeutil.iso(c.created_at)} for c in new]}


def t_call_transcript(db: Session, user: User, call_id: int = 0, **_) -> dict:
    """What was said on one call. Owners only (phone.transcript), like the Phone page; the read is in the access log."""
    if not has_perm(user, "phone.transcript"):
        return {"error": "transcripts are for owners only"}
    r = db.get(PhoneCall, int(call_id or 0))
    if r is None:
        return {"error": "no such call"}
    if not r.transcript:
        return {"error": "this call has no transcript", "call": _call(r, user)}
    crm.log_access(db, user, r.contact_id, "view", detail="call transcript (staff assistant)", dedupe_seconds=60)
    return {"call": _call(r, user), "transcript": r.transcript[:6000]}


def t_creatives(db: Session, user: User, **_) -> dict:
    from app.services import creatives as cs
    rows = db.scalars(select(Creative).order_by(Creative.id.desc()).limit(30)).all()
    return {"settings": cs.public_cfg(db), "items": [{"id": c.id, "title": c.title, "kind": c.kind, "format": c.format, "status": c.status, "caption": (c.caption or "")[:200],
                                                       "cost_usd": round((c.cost_usd or 0) + (c.llm_cost_usd or 0), 2), "share_link": cs.share_url(c), "error": c.error or None,
                                                       "created": timeutil.iso(c.created_at)} for c in rows]}


def t_automations(db: Session, user: User, **_) -> dict:
    rows = db.scalars(select(Automation).order_by(Automation.id)).all()
    return {"automations": [{"id": a.id, "name": a.name, "on": a.enabled, "when": a.trigger_type, "trigger": json.loads(a.trigger_json or "{}"), "action": a.action,
                             "runs": a.run_count, "last_run": timeutil.iso(a.last_run_at) if a.last_run_at else None, "last_ok": a.last_ok, "last_result": a.last_result} for a in rows]}


def _a_add_knowledge(db, user, p):
    e = kn.propose(db, category=p.get("category") or "clinic_info", title=str(p["title"]), body=str(p["text"]), proposed_by="human", user=user,
                   source_kind="manual", source_ref="Added through the staff assistant")
    if p.get("approve") and has_perm(user, "knowledge.decide"):
        kn.approve(db, e, user)
    return f"Knowledge '{e.title}' saved ({e.status})."


def _a_knowledge_decide(db, user, p):
    e = db.get(KnowledgeEntry, int(p["id"]))
    if e is None:
        raise crm.CrmError(404, "No such knowledge entry.")
    if p.get("text") and e.status == "pending":
        e.body = str(p["text"])[:4000]
    (kn.approve if p["decision"] == "approve" else kn.decline)(db, e, user)
    return f"'{e.title}' {e.status}."


def _a_task(db, user, p):
    if p.get("id"):
        t = db.get(Task, int(p["id"]))
        if t is None:
            raise crm.CrmError(404, "No such task.")
        if p.get("status") in ("open", "in_progress", "done"):
            t.status = p["status"]
            if t.status == "done":
                t.done_at, t.done_by = timeutil.now_utc(), user.id
                if t.raised_by == "ai":
                    import threading
                    from app.assistant import learn
                    threading.Thread(target=learn.after_done, args=(t.id, user.id), daemon=True).start()
        if p.get("priority") in ("high", "normal", "low"):
            t.priority = p["priority"]
        if p.get("assign_to"):
            u = db.scalar(select(User).where(func.lower(User.display_name) == str(p["assign_to"]).lower(), User.active.is_(True)))
            if u is None:
                raise crm.CrmError(400, "Nobody by that name.")
            t.assigned_to = u.id
        ev.record(db, ev.TASKS, "task_updated", f"{user.display_name} updated a task through the assistant", user, "task", t.id)
        return f"Task '{t.title}' updated."
    t = Task(kind=p.get("kind") or "other", classification="inquiry", title=str(p["title"])[:200], reason=str(p.get("reason") or "")[:2000],
             contact_id=int(p["contact_id"]) if p.get("contact_id") else None, priority=p.get("priority") if p.get("priority") in ("high", "normal", "low") else "normal",
             status="open", raised_by="human", created_at=timeutil.now_utc())
    db.add(t)
    db.flush()
    ev.record(db, ev.TASKS, "task_created", f"{user.display_name} created a task through the assistant", user, "task", t.id)
    return f"Task '{t.title}' created."


def _a_setting(db, user, p):
    what, on = p["setting"], bool(p["on"])
    if what == "booking_by_text":
        booking.put_hours(db, {"enabled": on}, user.display_name)
    elif what == "product_suggestions":
        cur = upsell.get(db)
        settings_store.put_raw(db, upsell.KEY, {**cur, "enabled": on}, by=user.display_name)
    elif what == "automatic_replies":
        settings_store.put_raw(db, "auto_reply", on, by=user.display_name)
    elif what == "call_transcripts":
        cfg = ai.get_config(db)
        cfg["transcribe_enabled"] = on
        ai.save(db, cfg, user.display_name)
    elif what == "ai_writes_client_replies":
        cfg = ai.get_config(db)
        if on and not cfg["key_enc"]:
            raise crm.CrmError(409, "Add an OpenRouter key first.")
        cfg["enabled"] = on
        ai.save(db, cfg, user.display_name)
    ev.record(db, ev.ADMIN, "setting_changed_by_assistant", f"{user.display_name} turned {what.replace('_', ' ')} {'on' if on else 'off'} through the assistant", user, "setting", what)
    return f"{what.replace('_', ' ').capitalize()} turned {'on' if on else 'off'}."


def _a_contact_assistant(db, user, p):
    c = db.get(Contact, int(p["contact_id"]))
    if c is None:
        raise crm.CrmError(404, "No such contact.")
    c.ai_enabled = bool(p["on"])
    ev.record(db, ev.CONTACTS, "contact_ai_changed", f"{user.display_name} turned the assistant {'on' if c.ai_enabled else 'off'} for a contact", user, "contact", c.id)
    return f"Assistant {'on' if c.ai_enabled else 'off'} for {c.display_name}."


def _a_fee(db, user, p):
    if p.get("id"):
        f = db.get(Fee, int(p["id"]))
        if f is None or f.status != "pending":
            raise crm.CrmError(409, "Only a pending fee can be approved.")
        f.status, f.decided_by, f.decided_at = "approved", user.id, timeutil.now_utc()
        f.effective_from = f.effective_from or timeutil.today_local()
        return f"Fee '{f.service}' approved."
    f = Fee(service=str(p["service"])[:120], price_cents=None if p.get("dollars") is None else int(round(float(p["dollars"]) * 100)), unit=str(p.get("unit") or "")[:40],
            note=str(p.get("note") or "")[:300], status="pending", proposed_by="human")
    db.add(f)
    return f"Fee '{f.service}' added for approval."


def _a_prompt(db, user, p):
    cfg = ai.get_config(db)
    line = str(p["instruction"]).strip()[:400]
    cfg["system_prompt"] = (cfg["system_prompt"].rstrip() + "\n- " + line)[:6000]
    ai.save(db, cfg, user.display_name)
    ev.record(db, ev.ADMIN, "assistant_prompt_changed", f"{user.display_name} added a line to the assistant's base prompt", user, "setting", ai.KEY)
    return "Added to the assistant's base prompt."


def t_booking_requests(db: Session, user: User, **_) -> dict:
    """Appointments waiting for a person to approve or decline (asked by text, agreed on a call, or agreed in a staff text)."""
    rows = db.scalars(select(CalendarEvent).where(CalendarEvent.status == "requested").order_by(CalendarEvent.starts_at).limit(30)).all()
    return {"waiting": [{"event_id": e.id, "at": booking.label(crm.aware(e.starts_at)), "title": e.title,
                         "from": "a call" if "agreed on a call" in e.title else "a staff text" if "staff text" in e.title else "a text",
                         "contact": _cbrief(db.get(Contact, e.contact_id)) if e.contact_id else None} for e in rows]}


def t_website(db: Session, user: User, **_) -> dict:
    """The clinic website: the latest SEO audit (Google, Maps, AI search), what to fix, and the deployment agent's changes."""
    from app.models import WebsiteAudit, WebsiteChange
    from app.services import deploy_agent, website
    a = db.scalar(select(WebsiteAudit).where(WebsiteAudit.status == "done").order_by(WebsiteAudit.id.desc()).limit(1))
    out: dict[str, Any] = {"site": website.get_settings(db)["url"], "audit": None}
    if a is not None:
        sm = json.loads(a.summary_json or "{}")
        out["audit"] = {"finished_at": timeutil.iso(a.finished_at), "score": sm.get("score"), "scores_by_area": sm.get("areas"), "pages": sm.get("pages"),
                        "pagespeed": (sm.get("pagespeed") or {}).get("scores"), "compared_with_previous": sm.get("compare"),
                        "issues": [{k: i.get(k) for k in ("area", "severity", "title", "count", "fix")} for i in json.loads(a.issues_json or "[]")][:20],
                        "has_ai_fix_plan": bool(a.ai_plan)}
    out["deployment_agent"] = {**{k: v for k, v in deploy_agent.public(deploy_agent.get_cfg(db)).items() if k != "token_last4"},
                               "recent_changes": [{k: v for k, v in deploy_agent.view(c).items() if k in ("id", "status", "mode", "summary", "preview_url", "created_at", "error")}
                                                  for c in db.scalars(select(WebsiteChange).order_by(WebsiteChange.id.desc()).limit(5)).all()]}
    return out


def t_blog(db: Session, user: User, **_) -> dict:
    from app.services import blog
    cfg = blog.get_cfg(db)
    if not blog.public(cfg)["ready"]:
        return {"connected": False, "note": "The website CMS is not connected yet (Website content, Blog posts)."}
    try:
        return {"connected": True, "posts": blog.list_posts(db)[:30]}
    except Exception:  # noqa: BLE001
        return {"connected": True, "error": "The CMS could not be reached."}


def t_chats(db: Session, user: User, **_) -> dict:
    """This person's own team chats (only conversations they are in): unread counts and the latest messages."""
    from app.api.chats import _everyone, _thread_dicts
    from app.models import ChatMember, ChatMessage, ChatThread
    _everyone(db)
    tids = db.scalars(select(ChatMember.thread_id).where(ChatMember.user_id == user.id)).all()
    threads = list(db.scalars(select(ChatThread).where(ChatThread.id.in_(tids))).all()) if tids else []
    items = _thread_dicts(db, user, threads)
    for it in items[:6]:
        rows = db.scalars(select(ChatMessage).where(ChatMessage.thread_id == it["id"]).order_by(ChatMessage.created_at.desc()).limit(5)).all()
        people = {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_({r.user_id for r in rows if r.user_id}))).all()}
        it["latest"] = [{"who": people.get(r.user_id, ""), "text": r.body[:300], "at": timeutil.iso(r.created_at)} for r in reversed(rows)]
    return {"conversations": items[:12]}


def _a_booking(db, user, p):
    from app.api import booking as booking_api
    e = db.get(CalendarEvent, int(p["event_id"]))
    if e is None or e.status != "requested":
        raise crm.CrmError(409, "That appointment is not waiting for approval.")
    body = booking_api.ConfirmIn(notify=bool(p.get("text_client")))
    out = booking_api.confirm(e.id, body, user, db) if p.get("decision") == "confirm" else booking_api.decline(e.id, body, user, db)
    return f"Appointment {'confirmed' if p.get('decision') == 'confirm' else 'declined'}" + (f"; text: {out.get('texted')}" if out.get("texted") else "") + "."


def _a_chat(db, user, p):
    from app.api.chats import NewMessage, NewThread, _everyone, new_thread, send
    to = str(p.get("to") or "").strip()
    if to.lower() in ("everyone", "all", "team"):
        tid = _everyone(db).id
    else:
        u = db.scalar(select(User).where(func.lower(User.display_name) == to.lower(), User.active.is_(True))) or \
            db.scalar(select(User).where(func.lower(User.display_name).like(f"{to.lower().split()[0]}%"), User.active.is_(True)))
        if u is None or u.id == user.id:
            raise crm.CrmError(400, "Nobody by that name.")
        tid = new_thread(NewThread(user_ids=[u.id]), user, db)["item"]["id"]
    send(tid, NewMessage(body=str(p["text"])[:4000]), user, db)
    return f"Message sent to {to}."


def _a_website(db, user, p):
    from app.models import WebsiteAudit
    from app.services import deploy_agent, website
    if p.get("do") == "audit":
        website.start(db, user.display_name)
        return "The website audit started; results show in SEO agent in under a minute."
    a = db.scalar(select(WebsiteAudit).where(WebsiteAudit.status == "done").order_by(WebsiteAudit.id.desc()).limit(1))
    if a is None:
        raise crm.CrmError(409, "Run an audit first.")
    try:
        c = deploy_agent.start(db, a, [], user.display_name, p.get("mode") if p.get("mode") in ("auto", "approve") else None)
    except ValueError as e:
        raise crm.CrmError(409, "Connect Vercel in SEO agent first." if str(e) == "no_token" else "The OpenRouter key is not set.")
    return f"The deployment agent is writing the fixes ({c.mode} mode); follow it in SEO agent."


def _a_agent_name(db, user, p):
    name = str(p["name"]).strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z '\-]{1,29}", name):
        raise crm.CrmError(400, "Use a simple first name.")
    cfg = ai.get_config(db)
    cfg["agent_name"] = name
    ai.save(db, cfg, user.display_name)
    ev.record(db, ev.ADMIN, "assistant_name_changed", f"{user.display_name} renamed the client assistant to {name}", user, "setting", ai.KEY)
    return f"The client assistant is now called {name}."


ACTIONS: dict[str, tuple[str, Callable]] = {
    "add_knowledge": ("knowledge.suggest", _a_add_knowledge),
    "decide_knowledge": ("knowledge.decide", _a_knowledge_decide),
    "task": ("tasks.work", _a_task),
    "setting": ("admin.settings", _a_setting),
    "contact_assistant": ("contacts.edit", _a_contact_assistant),
    "fee": ("knowledge.suggest", _a_fee),
    "base_prompt": ("admin.settings", _a_prompt),
    "booking_decision": ("tasks.work", _a_booking),
    "chat_message": ("home.view", _a_chat),
    "website": ("website.run", _a_website),
    "assistant_name": ("admin.settings", _a_agent_name),
}
ACTION_PERM_OVERRIDE = {("setting", "booking_by_text"): "afterhours.edit", ("setting", "product_suggestions"): "knowledge.decide",
                        ("setting", "automatic_replies"): "admin.telus", ("fee", "approve"): "knowledge.decide"}


def _perm_for(action: str, params: dict) -> str:
    if action == "setting":
        return ACTION_PERM_OVERRIDE.get(("setting", params.get("setting")), "admin.settings")
    if action == "fee" and params.get("id"):
        return "knowledge.decide"
    return ACTIONS[action][0]


def propose(db: Session, user: User, action: str, params: dict, summary: str) -> dict:
    if action not in ACTIONS:
        return {"error": f"unknown action; use one of {list(ACTIONS)}"}
    if action == "setting" and params.get("setting") not in ("booking_by_text", "product_suggestions", "automatic_replies", "ai_writes_client_replies", "call_transcripts"):
        return {"error": "unknown setting"}
    perm = _perm_for(action, params)
    if not has_perm(user, perm):
        return {"error": "this person is not allowed to make that change"}
    for k in [k for k, v in _pending.items() if v["expires"] < time.time()]:
        _pending.pop(k, None)
    pid = uuid.uuid4().hex
    _pending[pid] = {"user_id": user.id, "action": action, "params": params, "summary": summary[:300], "expires": time.time() + PENDING_SECONDS}
    return {"proposal_id": pid, "shown_to_user_as": summary[:300], "status": "waiting for the person to press Confirm"}


def confirm(db: Session, user: User, pid: str) -> str:
    p = _pending.get(pid)
    if p is None or p["expires"] < time.time():
        _pending.pop(pid, None)
        raise crm.CrmError(404, "That change has expired. Ask again.")
    if p["user_id"] != user.id:
        raise crm.CrmError(403, "Only the person who asked can confirm this.")  # and it stays for them
    _pending.pop(pid, None)
    if not has_perm(user, _perm_for(p["action"], p["params"])):
        raise crm.CrmError(403, "You are not allowed to make that change.")
    try:
        msg = ACTIONS[p["action"]][1](db, user, p["params"])
    except (KeyError, ValueError, TypeError):
        raise crm.CrmError(400, "The change was missing something. Ask again in other words.")
    db.commit()
    return msg


def cancel(user: User, pid: str) -> None:
    p = _pending.get(pid)
    if p and p["user_id"] == user.id:
        _pending.pop(pid, None)


# ---------------------------------------------------------------- the tool list offered to the model
def _fn(name, desc, props=None, req=None):
    return {"type": "function", "function": {"name": name, "description": desc,
                                              "parameters": {"type": "object", "properties": props or {}, "required": req or []}}}


DATE = {"type": "string", "description": "YYYY-MM-DD"}
TOOLS: list[tuple[str, str, Callable, dict]] = [
    ("home.view", "clinic_overview", t_overview, _fn("clinic_overview", "Counts for today: contacts, open tasks, calendar, calls, texts, pending knowledge, and which assistant settings are on.")),
    ("contacts.view", "search", t_search, _fn("search", "Find contacts (by name, email or phone digits), pets, tasks and text messages.", {"query": {"type": "string"}}, ["query"])),
    ("contacts.view", "contact_details", t_contact, _fn("contact_details", "Everything about one contact: details, consent, pets, open tasks, upcoming appointments, recent messages.", {"contact_id": {"type": "integer"}}, ["contact_id"])),
    ("tasks.view", "list_tasks", t_tasks, _fn("list_tasks", "Tasks, each with a label (Missed call, Pickup, Confirm booking...), who, number, one-line summary and next step, plus totals by group. status: active (default), open, in_progress, done or all. assigned: me, unassigned, me_or_unassigned, a staff name, or empty for everyone. kind: callback, booking, refill, results, complaint, escalation, other.",
                                            {"status": {"type": "string"}, "priority": {"type": "string"}, "kind": {"type": "string"}, "assigned": {"type": "string"}, "limit": {"type": "integer"}})),
    ("calendar.view", "calendar", t_calendar, _fn("calendar", "Appointments between two dates (default the next 7 days), whether booking by text is on, and the next open times. Give the same date twice to get every open time that day.", {"from_date": DATE, "to_date": DATE})),
    ("phone.view", "phone_calls", t_calls, _fn("phone_calls", "Phone calls between two dates (default last 7 days) with totals by result. handled_by: human, ai or none. Notes only, never transcripts.",
                                             {"from_date": DATE, "to_date": DATE, "handled_by": {"type": "string"}, "result": {"type": "string"}, "contact_id": {"type": "integer"}, "limit": {"type": "integer"}})),
    ("messages.view", "recent_texts", t_texts, _fn("recent_texts", "The most recent text messages in and out, across everyone.", {"limit": {"type": "integer"}})),
    ("knowledge.view", "knowledge", t_knowledge, _fn("knowledge", "Clinic knowledge entries. status: approved, pending, declined, retired or all.", {"status": {"type": "string"}, "query": {"type": "string"}})),
    ("knowledge.view", "fees_and_products", t_fees, _fn("fees_and_products", "Fees and products with prices and status.", {"status": {"type": "string"}})),
    ("campaigns.view", "campaigns", t_campaigns, _fn("campaigns", "Text and email campaigns and their counts.")),
    ("admin.settings", "system_status", t_system, _fn("system_status", "The server and app: version, database size, disk, backups, the TELUS helper (live calls, reading login), texting state and queue, recent assistant activity.")),
    ("admin.users", "staff_list", t_users, _fn("staff_list", "Staff accounts: name, role, admin, active, last time they used the app.")),
    ("admin.audit", "audit_log", t_audit, _fn("audit_log", "The latest entries of the audit log (who did what), including sign-ins and admin changes.", {"limit": {"type": "integer"}, "contains": {"type": "string"}})),
    ("calendar.view", "booking_requests", t_booking_requests, _fn("booking_requests", "Appointments waiting for approval or decline (asked by text, agreed on a call or in a staff text), with the client.")),
    ("website.view", "website", t_website, _fn("website", "The clinic website: latest SEO audit with scores for Google, Maps and AI search, issues to fix, Google PageSpeed, and the deployment agent's recent changes (Vercel previews and what went live).")),
    ("website.view", "blog_posts", t_blog, _fn("blog_posts", "The blog posts on the clinic website.")),
    ("home.view", "my_chats", t_chats, _fn("my_chats", "This person's own team chats (Everyone, direct, groups): unread counts and latest messages. Only conversations they are in.")),
    ("website.view", "website_traffic", t_website_traffic, _fn("website_traffic", "Website visitors and page views for the last 7, 30 or 90 days compared with the period before, top pages, where visitors came from, countries, devices and browsers, inbound calls in the same days, and the SEO score history.", {"days": {"type": "integer"}})),
    ("home.view", "people_waiting", t_people_waiting, _fn("people_waiting", "People whose latest text or email to the clinic has not been read yet, newest first, with what they wrote.", {"limit": {"type": "integer"}})),
    ("contacts.view", "contacts_summary", t_contacts_summary, _fn("contacts_summary", "How many patients and inquiries there are, how many contacts are new in the last N days (default 7), by kind and by where they came from.", {"days": {"type": "integer"}})),
    ("phone.transcript", "call_transcript", t_call_transcript, _fn("call_transcript", "The transcript of ONE call (get the call id from phone_calls or contact_details first). Only when the question needs what was actually said; quote briefly.", {"call_id": {"type": "integer"}}, ["call_id"])),
    ("creatives.view", "creative_studio", t_creatives, _fn("creative_studio", "Creative studio: images and short videos made for campaigns, their status, cost, share links, and this month's spend and budget.")),
    ("automations.view", "automations", t_automations, _fn("automations", "The automations (scheduled reports, chat posts, tasks, texts): what starts each one, whether it is on, and how its last run went.")),
    ("reports.view", "report", t_report, _fn("report", "Run a report: agents, pending_knowledge, conversion, recall, call_volume or campaigns.", {"key": {"type": "string"}, "from_date": DATE, "to_date": DATE}, ["key"])),
]
PROPOSE = _fn("propose_change", "Propose a change. It is shown to the person with a Confirm button and only happens if they press it. Actions and params: "
              "add_knowledge {category, title, text, approve}; decide_knowledge {id, decision: approve|decline, text (optional edit)}; "
              "task {id (to update) or title (to create), reason, priority, status, assign_to (a staff name), contact_id}; "
              "setting {setting: booking_by_text|product_suggestions|automatic_replies|ai_writes_client_replies, on: true|false}; "
              "contact_assistant {contact_id, on}; fee {id (to approve) or service, dollars, unit, note}; base_prompt {instruction: one line about tone or behaviour}; "
              "booking_decision {event_id, decision: confirm|decline, text_client: true|false}; chat_message {to: a staff name or everyone, text}; "
              "website {do: audit|fix, mode: approve|auto (for fix)}; assistant_name {name}. setting also takes call_transcripts.",
              {"action": {"type": "string"}, "params": {"type": "object"}, "summary": {"type": "string", "description": "one plain sentence describing the change"}}, ["action", "params", "summary"])


def tools_for(user: User) -> tuple[list[dict], dict[str, Callable]]:
    defs, impl = [], {}
    for perm, name, fn, spec in TOOLS:
        if has_perm(user, perm):
            defs.append(spec)
            impl[name] = fn
    if any(has_perm(user, p) for p, _ in ACTIONS.values()):
        defs.append(PROPOSE)
    return defs, impl


def system(user: User, agent_name: str = ai.AGENT_NAME) -> str:
    now = timeutil.local(timeutil.now_utc())
    owner = ""
    if user.role == "owner":
        owner = (f"\n{user.display_name} is the owner and head of the clinic. Give complete, direct answers with numbers, and look across every area their tools reach "
                 f"(tasks, people, calls, texts, calendar, knowledge, reports, staff, audit log, system status). When they ask about \"my\" tasks, check tasks assigned to them "
                 "AND unassigned tasks (they are responsible for those too) and say which is which.")
    return f"""You are the office assistant inside Walnut Grove Desk, the staff app of Walnut Grove Animal Hospital (a vet clinic in Langley, BC).
You are talking with {user.display_name} ({user.role}). It is {now.strftime('%A %B %d %Y, %I:%M %p')} in Vancouver.{owner}
What the app has (so you can point people to the right place): Home, Tasks (callback tasks from missed calls and voicemails, tasks the
client assistant raises, booking confirmations), Contacts (texts, emails, calls with play and transcript, notes; contacts found on calls
start "not opted in"), Calendar (requested appointments to Confirm or Decline), Knowledge & Fees (pending suggestions from call notes,
the website and corrections), Phone (live TELUS calls, transcripts and summaries, "Why people call"), Campaigns, Reports, Website content
(the site's pages and blog posts), SEO agent (Google, Maps and AI-search audits, AI fix plan, deployment agent that deploys fixes to the site on Vercel as a preview, then live after approval),
Creative studio (clinic images and short videos made through a short interview, attachable to campaigns), Chats (team chat), and Admin. The client text assistant is called
{agent_name} and only texts the numbers on the test list until the owner switches texting to live.
- Use the tools to look things up, as many as the question needs (several in one step is fine). Never guess a number, name, date or setting; if a tool does not have it, say so.
- For "what should I look at", "what needs my decision" or "what happened since yesterday": check urgent and unassigned tasks, booking requests waiting, people waiting for a reply, missed calls, and pending knowledge, then give the few most important items first.
- Times in tool results are UTC (ending in Z or +00:00). Always say times in Vancouver time, never UTC.
- Answer briefly in plain words. Use short bullet lines starting with "- " for lists. Mention contact names, not ids.
- To change anything, call propose_change once per change with a clear one-sentence summary, then tell the person to press Confirm. Never say a change is done before they confirm.
- You may suggest which knowledge to approve, but do not invent clinic facts; knowledge text should come from what the person tells you.
- You are an office assistant, not a vet: never give medical advice about a pet."""


def run(db: Session, user: User, history: list[dict[str, str]]) -> dict[str, Any]:
    cfg = ai.get_config(db)
    key = crypto.decrypt(cfg["key_enc"]) if cfg.get("key_enc") else None
    if not key:
        raise AgentFailed("no_key")
    defs, impl = tools_for(user)
    msgs: list[dict[str, Any]] = [{"role": "system", "content": system(user, cfg.get("agent_name") or ai.AGENT_NAME)}, *history[-12:]]
    proposals: list[dict] = []
    used: list[str] = []
    for _ in range(MAX_STEPS):
        payload = {"model": cfg["model"], "messages": msgs, "tools": defs, "tool_choice": "auto", "temperature": 0.1, "max_tokens": 1400,
                   "provider": {"require_parameters": True, **({"data_collection": "deny"} if cfg["deny_data_collection"] else {})}}
        db.commit()  # never hold the database while waiting for the model (every transaction takes the write lock)
        try:
            with httpx.Client(timeout=60, transport=llm_adapter.TRANSPORT) as c:
                r = c.post(ai.URL, json=payload, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Title": "Walnut Grove Desk"})
        except httpx.HTTPError:
            raise AgentFailed("network") from None
        if r.status_code != 200:
            raise AgentFailed(f"http_{r.status_code}")
        try:
            msg = r.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError):
            raise AgentFailed("bad_answer") from None
        calls = msg.get("tool_calls") or []
        if not calls:
            text = re.sub(r"\s*[\u2014\u2013]\s*", ", ", (msg.get("content") or "").strip())  # house style: no long dashes
            return {"text": text or "I could not find an answer.", "proposals": proposals, "tools": used}
        msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for call in calls[:6]:
            name = (call.get("function") or {}).get("name", "")
            try:
                args = json.loads((call.get("function") or {}).get("arguments") or "{}")
            except ValueError:
                args = {}
            if name == "propose_change":
                res = propose(db, user, str(args.get("action")), args.get("params") or {}, str(args.get("summary") or args.get("action")))
                if "proposal_id" in res:
                    proposals.append({"id": res["proposal_id"], "summary": res["shown_to_user_as"]})
            elif name in impl:
                used.append(name)
                try:
                    res = impl[name](db, user, **{k: v for k, v in args.items() if isinstance(k, str)})
                except Exception:  # a bad argument must not stop the conversation
                    res = {"error": "that lookup failed"}
            else:
                res = {"error": "not available to this person"}
            msgs.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": json.dumps(res, default=str)[:MAX_TOOL_CHARS]})
    return {"text": "That needed too many steps. Please ask in a simpler way.", "proposals": proposals, "tools": used}
