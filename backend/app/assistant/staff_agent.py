"""The staff assistant with AI: answers questions about the clinic's data with lookup tools, and proposes changes that a person confirms.

* Every tool runs with the signed-in person's own permissions (a tool they may not use is not even offered to the model).
* The model only sees what the tools return. Call transcripts, recordings, secrets and keys are never available to it.
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
from app.models import (CalendarEvent, Campaign, Contact, Fee, KnowledgeEntry, Message, Pet, PhoneCall, Product, Task, User, Visit)
from app.security import crypto
from app.security.permissions import has_perm
from app.services import crm, settings_store, sms_mode, timeutil
from app.services import events as ev
from app.services import knowledge as kn

MAX_STEPS = 6
MAX_TOOL_CHARS = 7000
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
                     "ai_writes_client_replies": bool(ai.get_config(db)["enabled"]), "ai_model": ai.get_config(db)["model"]},
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
        "recent_messages": [{"channel": m.channel, "direction": m.direction, "by": m.sender_kind, "text": (m.body or "")[:240], "at": timeutil.iso(m.created_at), "status": m.delivery_state} for m in msgs],
    }


def t_tasks(db: Session, user: User, status: str = "active", priority: str = "", kind: str = "", limit: int = 20, **_) -> dict:
    stmt = select(Task)
    stmt = stmt.where(Task.status.in_(("open", "in_progress"))) if status == "active" else stmt.where(Task.status == status) if status in ("open", "in_progress", "done") else stmt
    if priority in ("high", "normal", "low"):
        stmt = stmt.where(Task.priority == priority)
    if kind:
        stmt = stmt.where(Task.kind == kind)
    rows = db.scalars(stmt.order_by(Task.created_at.desc()).limit(min(int(limit or 20), 50))).all()
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    return {"tasks": [{"id": t.id, "title": t.title, "kind": t.kind, "priority": t.priority, "status": t.status, "raised_by": t.raised_by,
                       "assigned_to": names.get(t.assigned_to), "contact_id": t.contact_id, "created": timeutil.iso(t.created_at), "reason": (t.reason or "")[:200]} for t in rows]}


def t_calendar(db: Session, user: User, from_date: str = "", to_date: str = "", **_) -> dict:
    today = timeutil.today_local()
    a, b = _d(from_date, today), _d(to_date, today + timedelta(days=7))
    rows = db.scalars(select(CalendarEvent).where(CalendarEvent.starts_at >= crm.local_day_start(a), CalendarEvent.starts_at < crm.local_day_start(b + timedelta(days=1)))
                      .order_by(CalendarEvent.starts_at).limit(80)).all()
    return {"from": a.isoformat(), "to": b.isoformat(), "events": [{"id": e.id, "title": e.title, "kind": e.kind, "status": e.status, "at": booking.label(crm.aware(e.starts_at)), "contact_id": e.contact_id} for e in rows],
            "booking_by_text": booking.get_hours(db)["enabled"], "next_open_times": [booking.label(x) for x in booking.free_times(db, 30, n=5)]}


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
            "calls": [{"id": r.id, "at": timeutil.iso(r.started_at), "direction": r.direction, "handled_by": r.handled_by, "line": r.agent_label, "seconds": r.duration_seconds,
                       "result": r.result, "note": r.short_note, "contact_id": r.contact_id} for r in rows]}


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
    return {"entries": [{"id": e.id, "status": e.status, "category": e.category, "title": e.title, "text": e.body[:500]} for e in rows]}


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


# ---------------------------------------------------------------- changes the model may propose (executed only on Confirm)
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


ACTIONS: dict[str, tuple[str, Callable]] = {
    "add_knowledge": ("knowledge.suggest", _a_add_knowledge),
    "decide_knowledge": ("knowledge.decide", _a_knowledge_decide),
    "task": ("tasks.work", _a_task),
    "setting": ("admin.settings", _a_setting),
    "contact_assistant": ("contacts.edit", _a_contact_assistant),
    "fee": ("knowledge.suggest", _a_fee),
    "base_prompt": ("admin.settings", _a_prompt),
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
    if action == "setting" and params.get("setting") not in ("booking_by_text", "product_suggestions", "automatic_replies", "ai_writes_client_replies"):
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
    ("tasks.view", "list_tasks", t_tasks, _fn("list_tasks", "Tasks. status: active, open, in_progress, done or all.", {"status": {"type": "string"}, "priority": {"type": "string"}, "kind": {"type": "string"}, "limit": {"type": "integer"}})),
    ("calendar.view", "calendar", t_calendar, _fn("calendar", "Appointments between two dates (default the next 7 days), whether booking by text is on, and the next open times.", {"from_date": DATE, "to_date": DATE})),
    ("phone.view", "phone_calls", t_calls, _fn("phone_calls", "Phone calls between two dates (default last 7 days) with totals by result. handled_by: human, ai or none. Notes only, never transcripts.",
                                             {"from_date": DATE, "to_date": DATE, "handled_by": {"type": "string"}, "result": {"type": "string"}, "contact_id": {"type": "integer"}, "limit": {"type": "integer"}})),
    ("messages.view", "recent_texts", t_texts, _fn("recent_texts", "The most recent text messages in and out, across everyone.", {"limit": {"type": "integer"}})),
    ("knowledge.view", "knowledge", t_knowledge, _fn("knowledge", "Clinic knowledge entries. status: approved, pending, declined, retired or all.", {"status": {"type": "string"}, "query": {"type": "string"}})),
    ("knowledge.view", "fees_and_products", t_fees, _fn("fees_and_products", "Fees and products with prices and status.", {"status": {"type": "string"}})),
    ("campaigns.view", "campaigns", t_campaigns, _fn("campaigns", "Text and email campaigns and their counts.")),
    ("reports.view", "report", t_report, _fn("report", "Run a report: agents, pending_knowledge, conversion, recall, call_volume or campaigns.", {"key": {"type": "string"}, "from_date": DATE, "to_date": DATE}, ["key"])),
]
PROPOSE = _fn("propose_change", "Propose a change. It is shown to the person with a Confirm button and only happens if they press it. Actions and params: "
              "add_knowledge {category, title, text, approve}; decide_knowledge {id, decision: approve|decline, text (optional edit)}; "
              "task {id (to update) or title (to create), reason, priority, status, assign_to (a staff name), contact_id}; "
              "setting {setting: booking_by_text|product_suggestions|automatic_replies|ai_writes_client_replies, on: true|false}; "
              "contact_assistant {contact_id, on}; fee {id (to approve) or service, dollars, unit, note}; base_prompt {instruction: one line about tone or behaviour}.",
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


def system(user: User) -> str:
    now = timeutil.local(timeutil.now_utc())
    return f"""You are the office assistant inside Walnut Grove Desk, the staff app of Walnut Grove Animal Hospital (a vet clinic in Langley, BC).
You are talking with {user.display_name} ({user.role}). It is {now.strftime('%A %B %d %Y, %I:%M %p')} in Vancouver.
- Use the tools to look things up. Never guess a number, name, date or setting; if a tool does not have it, say so.
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
    msgs: list[dict[str, Any]] = [{"role": "system", "content": system(user)}, *history[-12:]]
    proposals: list[dict] = []
    used: list[str] = []
    for _ in range(MAX_STEPS):
        payload = {"model": cfg["model"], "messages": msgs, "tools": defs, "tool_choice": "auto", "temperature": 0.1, "max_tokens": 900,
                   "provider": {"require_parameters": True, **({"data_collection": "deny"} if cfg["deny_data_collection"] else {})}}
        try:
            with httpx.Client(timeout=40, transport=llm_adapter.TRANSPORT) as c:
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
            return {"text": (msg.get("content") or "").strip() or "I could not find an answer.", "proposals": proposals, "tools": used}
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
