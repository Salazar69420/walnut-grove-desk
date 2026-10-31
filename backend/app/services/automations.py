"""Automations: "at this time" or "when this happens", then "do that". Built for the people who use the app every day.

Triggers
* schedule: days of the week and a local time (checked every minute; each automation runs at most once per day it is due).
* event: something new was saved anywhere in the app (a missed call or voicemail, a booking request, a new contact, an urgent
  task, a text coming in). Events are picked up after the save commits and handled in a background thread with their own
  database session, so they never slow down or break the thing that caused them. Automations never trigger other automations.

Actions
* report: a short summary (morning, end of day, waiting for approval, weekly) sent to team Chats, by text or by email.
* chat: post a message in team Chats (to a person or Everyone), with the event's details.
* task: create a task (title, who it is for, priority) about the client involved.
* text_client: text the client involved (only people who opted in; the texting test list still applies until texting is live).
* seo_audit: run the website audit.

Delivery rules: texts to staff numbers go through the same sender and safety net as everything else (only numbers on the test
list until the owner switches texting to live). Email needs the clinic's SMTP settings; without them the run says so.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import threading
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.models import (Automation, AutomationRun, CalendarEvent, ChatMessage, Contact, KnowledgeEntry, Message, PhoneCall, Task, User)
from app.security import crypto
from app.services import crm, settings_store, timeutil

log = logging.getLogger("walnut.automations")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
EVENTS = {
    "missed_call": "A call was missed or went to voicemail",
    "booking_requested": "A booking request is waiting for approval",
    "new_contact": "A new contact was added",
    "urgent_task": "An urgent (high priority) task was raised",
    "text_received": "A client sent a text",
}
REPORTS = {
    "owner_brief": "Dr. Garg's daily briefing: approvals, missed calls, open tasks and today's appointments, item by item",
    "morning": "Morning report: yesterday's calls, today's appointments, what is waiting",
    "end_of_day": "End of day: calls, bookings and tasks today, what is still open",
    "approvals": "Waiting for approval: booking requests, knowledge, website changes",
    "weekly": "Weekly summary: calls by reason, bookings, new contacts, website score",
}
EMAIL_KEY = "email_smtp"

# ---------------------------------------------------------------- the ideas shown to start with
TEMPLATES: list[dict[str, Any]] = [
    {"key": "owner_brief_sms", "name": "Daily briefing to Dr. Garg by text", "why": "Every morning, one text with what needs you: approvals, yesterday's missed calls, open tasks and today's appointments.",
     "trigger_type": "schedule", "trigger": {"days": DAYS, "time": "07:30"}, "action": "report", "config": {"report": "owner_brief", "channel": "sms", "to": ""}},
    {"key": "morning_sms", "name": "Morning report by text", "why": "Know how the day looks before you walk in.",
     "trigger_type": "schedule", "trigger": {"days": DAYS[:6], "time": "07:45"}, "action": "report", "config": {"report": "morning", "channel": "sms", "to": ""}},
    {"key": "missed_call_text_back", "name": "Missed call: text the caller back", "why": "Most callers who reach voicemail do not leave one. A quick text keeps them.",
     "trigger_type": "event", "trigger": {"event": "missed_call"}, "action": "text_client",
     "config": {"text": "Hi {first_name}, sorry we missed your call at Walnut Grove Animal Hospital. Reply here and we will help, or we will call you back shortly."}},
    {"key": "booking_to_desk", "name": "New booking request: tell the team in Chats", "why": "Requests get approved while the client is still waiting.",
     "trigger_type": "event", "trigger": {"event": "booking_requested"}, "action": "chat", "config": {"to": "everyone", "text": "New booking request: {details}. Approve it in the Calendar."}},
    {"key": "urgent_alert", "name": "Urgent task: alert the owner", "why": "Emergencies and high-priority callbacks never sit unseen.",
     "trigger_type": "event", "trigger": {"event": "urgent_task"}, "action": "chat", "config": {"to": "owner", "text": "Urgent task: {details}"}},
    {"key": "end_of_day", "name": "End-of-day summary in Chats", "why": "Close the day knowing what is still open for tomorrow.",
     "trigger_type": "schedule", "trigger": {"days": DAYS[:5], "time": "18:30"}, "action": "report", "config": {"report": "end_of_day", "channel": "chat", "to": "everyone"}},
    {"key": "approvals_noon", "name": "Midday: what is waiting for approval", "why": "Booking requests and knowledge do not wait until tomorrow.",
     "trigger_type": "schedule", "trigger": {"days": DAYS[:6], "time": "12:15"}, "action": "report", "config": {"report": "approvals", "channel": "chat", "to": "owner"}},
    {"key": "weekly_email", "name": "Weekly summary by email (Monday)", "why": "Calls by reason, bookings, new clients and the website score, once a week.",
     "trigger_type": "schedule", "trigger": {"days": ["mon"], "time": "08:00"}, "action": "report", "config": {"report": "weekly", "channel": "email", "to": ""}},
    {"key": "weekly_seo", "name": "Weekly website check", "why": "See right away if a website change broke something for Google.",
     "trigger_type": "schedule", "trigger": {"days": ["sun"], "time": "21:00"}, "action": "seo_audit", "config": {}},
    {"key": "new_contact_task", "name": "New prospect: follow-up task", "why": "Every new caller or texter gets a person following up.",
     "trigger_type": "event", "trigger": {"event": "new_contact"}, "action": "task", "config": {"title": "Follow up with {name}", "priority": "normal", "assign_to": ""}},
]


# ---------------------------------------------------------------- reports
def _count(db: Session, stmt) -> int:
    return db.scalar(select(func.count()).select_from(stmt.subquery())) or 0


def build_report(db: Session, kind: str) -> tuple[str, str]:
    """(title, text). Short plain lines, so it reads well in a text message as well as in Chats or email."""
    from app.assistant import booking
    now = timeutil.now_utc()
    today = timeutil.today_local()
    t0, t1 = crm.local_day_start(today), crm.local_day_start(today + timedelta(days=1))
    y0 = crm.local_day_start(today - timedelta(days=1))
    open_tasks = select(Task).where(Task.status.in_(("open", "in_progress")))
    requests = db.scalars(select(CalendarEvent).where(CalendarEvent.status == "requested").order_by(CalendarEvent.starts_at)).all()
    lines: list[str] = []
    if kind == "owner_brief":
        owner = db.scalar(select(User).where(User.role == "owner", User.active.is_(True)).order_by(User.id))
        title = f"Good morning{(' ' + owner.display_name) if owner else ''}. {today.strftime('%a %b %d')}"
        hm = lambda d: timeutil.local(crm.aware(d)).strftime("%I:%M %p").lstrip("0").lower().replace(":00 ", " ")  # noqa: E731
        lines.append(f"NEEDS YOUR APPROVAL ({len(requests)})")
        for e in requests[:8]:
            c = db.get(Contact, e.contact_id) if e.contact_id else None
            how = "call" if "agreed on a call" in e.title else "staff text" if "staff text" in e.title else "text"
            lines.append(f"- {booking.label(crm.aware(e.starts_at))}, {c.display_name if c else e.title.split(':')[0]}, {e.title.split(':')[0].lower()} (by {how})")
        if len(requests) > 8:
            lines.append(f"- and {len(requests) - 8} more")
        missed = db.scalars(select(PhoneCall).where(PhoneCall.started_at >= y0, PhoneCall.started_at < t0, PhoneCall.direction == "inbound",
                                                    PhoneCall.result.in_(("missed", "voicemail"))).order_by(PhoneCall.started_at)).all()
        lines.append(f"MISSED CALLS YESTERDAY ({len(missed)})")
        for m in missed[:10]:
            c = db.get(Contact, m.contact_id) if m.contact_id else None
            who = c.display_name if c else (crm.phone_display(m.phone) if m.phone else "unknown number")
            lines.append(f"- {hm(m.started_at)} {who}{' (voicemail)' if m.result == 'voicemail' else ''}")
        if len(missed) > 10:
            lines.append(f"- and {len(missed) - 10} more")
        tasks = db.scalars(open_tasks.order_by((Task.priority == "high").desc(), Task.created_at)).all()
        lines.append(f"OPEN TASKS ({len(tasks)}, {sum(1 for t in tasks if t.priority == 'high')} urgent)")
        for t in tasks[:10]:
            name = t.title[8:] if t.title.lower().startswith("urgent, ") else t.title
            lines.append(f"- {'URGENT: ' if t.priority == 'high' else ''}{name[:80]}")
        if len(tasks) > 10:
            lines.append(f"- and {len(tasks) - 10} more")
        appts = db.scalars(select(CalendarEvent).where(CalendarEvent.starts_at >= t0, CalendarEvent.starts_at < t1, CalendarEvent.status == "booked").order_by(CalendarEvent.starts_at)).all()
        lines.append(f"TODAY ({len(appts)} appointments)")
        for e in appts[:10]:
            lines.append(f"- {hm(e.starts_at)} {e.title[:70]}")
        extra = [f"{_count(db, select(KnowledgeEntry).where(KnowledgeEntry.status == 'pending'))} knowledge entries to approve"]
        try:
            from app.models import WebsiteChange
            ready = _count(db, select(WebsiteChange).where(WebsiteChange.status == "ready"))
            if ready:
                extra.append(f"{ready} website change{'s' if ready != 1 else ''} ready to deploy")
        except Exception:  # noqa: BLE001
            pass
        lines.append("ALSO: " + ", ".join(extra) + ".")
    elif kind == "morning":
        title = f"Morning report, {today.strftime('%a %b %d')}"
        calls = select(PhoneCall).where(PhoneCall.started_at >= y0, PhoneCall.started_at < t0)
        missed = _count(db, calls.where(PhoneCall.direction == "inbound", PhoneCall.result.in_(("missed", "voicemail"))))
        appts = db.scalars(select(CalendarEvent).where(CalendarEvent.starts_at >= t0, CalendarEvent.starts_at < t1, CalendarEvent.status == "booked").order_by(CalendarEvent.starts_at)).all()
        lines += [f"Yesterday: {_count(db, calls)} calls, {missed} missed or voicemail.",
                  f"Today: {len(appts)} appointments" + (f", first at {timeutil.local(appts[0].starts_at).strftime('%I:%M %p').lstrip('0')}" if appts else "") + ".",
                  f"Waiting for approval: {len(requests)} booking request{'s' if len(requests) != 1 else ''}.",
                  f"Open tasks: {_count(db, open_tasks)} ({_count(db, open_tasks.where(Task.priority == 'high'))} urgent, {_count(db, open_tasks.where(Task.kind == 'callback'))} callbacks).",
                  f"Texts in the last 24 h: {_count(db, select(Message).where(Message.channel == 'sms', Message.direction == 'in', Message.created_at >= now - timedelta(hours=24)))}."]
    elif kind == "end_of_day":
        title = f"End of day, {today.strftime('%a %b %d')}"
        calls = select(PhoneCall).where(PhoneCall.started_at >= t0, PhoneCall.started_at < t1)
        lines += [f"Calls today: {_count(db, calls)} ({_count(db, calls.where(PhoneCall.result == 'answered'))} answered, {_count(db, calls.where(PhoneCall.result.in_(('missed', 'voicemail'))))} missed).",
                  f"Tasks done today: {_count(db, select(Task).where(Task.done_at >= t0))}. Still open: {_count(db, open_tasks)}.",
                  f"Booking requests still waiting: {len(requests)}.",
                  f"Tomorrow: {_count(db, select(CalendarEvent).where(CalendarEvent.starts_at >= t1, CalendarEvent.starts_at < t1 + timedelta(days=1), CalendarEvent.status == 'booked'))} appointments booked."]
    elif kind == "approvals":
        title = "Waiting for approval"
        lines += [f"Booking requests: {len(requests)}."] + [f"- {booking.label(crm.aware(e.starts_at))}: {e.title[:70]}" for e in requests[:5]]
        lines.append(f"Knowledge waiting: {_count(db, select(KnowledgeEntry).where(KnowledgeEntry.status == 'pending'))}.")
        try:
            from app.models import WebsiteChange
            ready = _count(db, select(WebsiteChange).where(WebsiteChange.status == "ready"))
            if ready:
                lines.append(f"Website changes ready to deploy: {ready}.")
        except Exception:  # noqa: BLE001
            pass
    else:
        title = f"Weekly summary to {today.strftime('%b %d')}"
        w0 = crm.local_day_start(today - timedelta(days=7))
        calls = select(PhoneCall).where(PhoneCall.started_at >= w0)
        reasons = db.execute(select(PhoneCall.intent, func.count()).where(PhoneCall.started_at >= w0, PhoneCall.intent.is_not(None)).group_by(PhoneCall.intent)
                             .order_by(func.count().desc()).limit(4)).all()
        from app.services.transcribe import INTENTS
        lines += [f"Calls: {_count(db, calls)} ({_count(db, calls.where(PhoneCall.result.in_(('missed', 'voicemail'))))} missed).",
                  "Top reasons: " + (", ".join(f"{INTENTS.get(k, k).lower()} {n}" for k, n in reasons) or "not enough transcripts yet") + ".",
                  f"Appointments booked: {_count(db, select(CalendarEvent).where(CalendarEvent.status == 'booked', CalendarEvent.starts_at >= w0, CalendarEvent.starts_at < t1))}.",
                  f"New contacts: {_count(db, select(Contact).where(Contact.created_at >= w0, Contact.erased_at.is_(None)))}."]
        try:
            from app.models import WebsiteAudit
            a = db.scalar(select(WebsiteAudit).where(WebsiteAudit.status == "done").order_by(WebsiteAudit.id.desc()).limit(1))
            if a:
                areas = json.loads(a.summary_json or "{}").get("areas") or {}
                lines.append(f"Website SEO: Google {areas.get('google', '?')}, Maps {areas.get('maps', '?')}, AI search {areas.get('ai', '?')}.")
        except Exception:  # noqa: BLE001
            pass
    return title, "\n".join(lines)


# ---------------------------------------------------------------- delivery
def email_settings(db: Session) -> dict[str, Any]:
    cfg = {"host": "", "port": 587, "username": "", "password_enc": "", "from_addr": "", "security": "starttls"}
    stored = settings_store.get_raw(db, EMAIL_KEY, None)
    if isinstance(stored, dict):
        cfg.update({k: v for k, v in stored.items() if k in cfg})
    return cfg


def send_email(db: Session, to: str, subject: str, body: str) -> str:
    cfg = email_settings(db)
    if not (cfg["host"] and cfg["from_addr"]):
        raise RuntimeError("Email is not set up yet (Automations, Email settings).")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["from_addr"], to, subject[:150]
    msg.set_content(body + "\n\nSent by an automation in Walnut Grove Desk.")
    pw = crypto.decrypt(cfg["password_enc"]) if cfg["password_enc"] else ""
    db.commit()  # nothing held while talking to the mail server
    ctx = ssl.create_default_context()
    if cfg["security"] == "ssl":
        with smtplib.SMTP_SSL(cfg["host"], int(cfg["port"]), context=ctx, timeout=20) as s:
            if cfg["username"]:
                s.login(cfg["username"], pw)
            s.send_message(msg)
    else:
        with smtplib.SMTP(cfg["host"], int(cfg["port"]), timeout=20) as s:
            s.starttls(context=ctx)
            if cfg["username"]:
                s.login(cfg["username"], pw)
            s.send_message(msg)
    return f"emailed {to}"


def send_staff_text(db: Session, number: str, body: str) -> str:
    from app.services import sms_mode
    n = crm.normalize_phone(number)
    if not n:
        raise RuntimeError("That phone number does not look right.")
    if not sms_mode.is_real_target(db, n):
        raise RuntimeError("Not sent: until texting is live, texts only go to the numbers on the texting test list.")
    if len(body) > 1500:
        body = body[:1440].rsplit("\n", 1)[0] + "\n(More in Walnut Desk.)"
    sms_mode.request_send("sms", n, body, None)
    return f"texted {crm.phone_display(n)}"


def post_chat(db: Session, to: str | int, body: str) -> str:
    from app.api.chats import _everyone
    from app.models import ChatMember, ChatThread
    from app.services import events as ev
    if to in ("", "everyone", None):
        t = _everyone(db)
        label = "Everyone"
    else:
        target = db.scalar(select(User).where(User.role == "owner", User.active.is_(True)).order_by(User.id)) if to == "owner" else db.get(User, int(to))
        if target is None:
            raise RuntimeError("That person was not found.")
        bot = _bot_user(db)
        key = f"{min(bot.id, target.id)}:{max(bot.id, target.id)}"
        t = db.scalar(select(ChatThread).where(ChatThread.pair_key == key))
        if t is None:
            t = ChatThread(kind="direct", pair_key=key, created_at=timeutil.now_utc())
            db.add(t)
            db.flush()
            db.add_all([ChatMember(thread_id=t.id, user_id=bot.id), ChatMember(thread_id=t.id, user_id=target.id)])
        label = target.display_name
    now = timeutil.now_utc()
    db.add(ChatMessage(thread_id=t.id, user_id=_bot_user(db).id, body=body[:4000], created_at=now))
    t.last_message_at = now
    ev.queue_publish(db, "chats", t.id, None)
    return f"posted in Chats ({label})"


def _bot_user(db: Session) -> User:
    """Messages from automations come from an "Automations" member of the team, so they are easy to tell apart. It cannot sign in."""
    u = db.scalar(select(User).where(User.username == "automations"))
    if u is None:
        u = User(username="automations", display_name="Automations", title="Automatic messages", initials="AU", role="desk", is_admin=False,
                 password_hash="!disabled", must_change_password=False, active=False)
        db.add(u)
        db.flush()
    return u


def deliver(db: Session, channel: str, to: Any, title: str, body: str) -> str:
    if channel == "sms":
        return send_staff_text(db, str(to or ""), f"{title}\n{body}")
    if channel == "email":
        if not to or "@" not in str(to):
            raise RuntimeError("Add an email address to send it to.")
        return send_email(db, str(to), title, body)
    return post_chat(db, to or "everyone", f"{title}\n{body}")


# ---------------------------------------------------------------- running one automation
def _fill(text: str, ctx: dict[str, Any]) -> str:
    out = text
    for k, v in ctx.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _event_context(db: Session, ev_name: str, obj_id: int) -> dict[str, Any]:
    from app.aftercare import templates as T
    from app.assistant import booking
    ctx: dict[str, Any] = {"details": "", "name": "", "first_name": "there", "contact_id": None}
    contact = None
    if ev_name == "missed_call":
        c = db.get(PhoneCall, obj_id)
        if c:
            contact = db.get(Contact, c.contact_id) if c.contact_id else None
            who = contact.display_name if contact else (crm.phone_display(c.phone) if c.phone else "an unknown number")
            ctx["details"] = f"{'Voicemail' if c.result == 'voicemail' else 'Missed call'} from {who} at {timeutil.local(c.started_at).strftime('%I:%M %p').lstrip('0')}"
    elif ev_name == "booking_requested":
        e = db.get(CalendarEvent, obj_id)
        if e:
            contact = db.get(Contact, e.contact_id) if e.contact_id else None
            ctx["details"] = f"{e.title} on {booking.label(crm.aware(e.starts_at))}"
    elif ev_name == "new_contact":
        contact = db.get(Contact, obj_id)
        if contact:
            ctx["details"] = f"{contact.display_name} ({contact.kind})"
    elif ev_name == "urgent_task":
        t = db.get(Task, obj_id)
        if t:
            contact = db.get(Contact, t.contact_id) if t.contact_id else None
            ctx["details"] = t.title
    elif ev_name == "text_received":
        m = db.get(Message, obj_id)
        if m:
            contact = db.get(Contact, m.contact_id)
            ctx["details"] = f"{contact.display_name if contact else 'Someone'}: {(m.body or '')[:120]}"
    if contact is not None:
        ctx.update(name=contact.display_name, first_name=T.first_name(contact.display_name) or "there", contact_id=contact.id)
    return ctx


def run(db: Session, a: Automation, ctx: dict[str, Any] | None = None, *, by: str = "schedule") -> tuple[bool, str]:
    cfg = json.loads(a.action_json or "{}")
    ctx = ctx or {}
    try:
        if a.action == "report":
            title, body = build_report(db, cfg.get("report", "morning"))
            result = deliver(db, cfg.get("channel", "chat"), cfg.get("to"), title, body)
        elif a.action == "chat":
            result = post_chat(db, cfg.get("to") or "everyone", _fill(cfg.get("text") or "{details}", ctx))
        elif a.action == "task":
            uid = None
            if cfg.get("assign_to"):
                u = db.get(User, int(cfg["assign_to"])) if str(cfg["assign_to"]).isdigit() else None
                uid = u.id if u else None
            t = Task(kind="other", classification="inquiry", title=_fill(cfg.get("title") or "Follow up", ctx)[:200], reason=f"Raised by the automation \"{a.name}\". {ctx.get('details', '')}"[:2000],
                     contact_id=ctx.get("contact_id"), priority=cfg.get("priority") if cfg.get("priority") in ("high", "normal", "low") else "normal",
                     status="open", raised_by="rule", assigned_to=uid, created_at=timeutil.now_utc())
            db.add(t)
            result = "task created"
        elif a.action == "text_client":
            from app.services import sender
            if not ctx.get("contact_id"):
                raise RuntimeError("No client to text for this event.")
            c = db.get(Contact, ctx["contact_id"])
            if c is None or c.sms_consent not in crm.CONSENT_CAN_SEND:
                raise RuntimeError("Skipped: the client has not opted in to texts.")
            m = sender.send_to_contact(db, c, None, channel="sms", body=_fill(cfg.get("text") or "", ctx)[:640], sender_kind="ai")
            result = f"texted the client ({m.delivery_state})"
        elif a.action == "seo_audit":
            from app.services import website
            website.start(db, f"Automation: {a.name}")
            result = "website audit started"
        else:
            raise RuntimeError("Unknown action.")
        ok = True
    except crm.CrmError as e:
        ok, result = False, str(e.detail)[:300]
    except Exception as e:  # noqa: BLE001 - the reason is shown on the automation; no secrets in these messages
        ok, result = False, (str(e) if isinstance(e, RuntimeError) else f"Failed ({type(e).__name__}).")[:300]
    a = db.get(Automation, a.id)
    a.last_run_at, a.last_ok, a.last_result, a.run_count = timeutil.now_utc(), ok, result, (a.run_count or 0) + 1
    db.add(AutomationRun(automation_id=a.id, at=timeutil.now_utc(), ok=ok, detail=f"{by}: {result}"[:500]))
    db.commit()
    return ok, result


# ---------------------------------------------------------------- schedule (called every minute)
def due(a: Automation, now_local: datetime) -> bool:
    t = json.loads(a.trigger_json or "{}")
    if DAYS[now_local.weekday()] not in (t.get("days") or DAYS):
        return False
    try:
        hh, mm = (int(x) for x in str(t.get("time") or "08:00").split(":")[:2])
    except ValueError:
        return False
    at = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if not (at <= now_local < at + timedelta(minutes=30)):
        return False  # catch up for half an hour after a restart, not later
    last = timeutil.local(crm.aware(a.last_run_at)) if a.last_run_at else None
    return last is None or last < at


def tick() -> int:
    from app import db as app_db
    n = 0
    with app_db.get_sessionmaker()() as s:
        s.info["automation"] = True
        now_local = timeutil.local(timeutil.now_utc())
        for a in s.scalars(select(Automation).where(Automation.enabled.is_(True), Automation.trigger_type == "schedule")).all():
            if due(a, now_local):
                run(s, a, by="schedule")
                n += 1
        s.commit()
    return n


# ---------------------------------------------------------------- events: seen when anything is saved, handled after commit
def _collect(session: Session, flush_context) -> None:
    if session.info.get("automation"):
        return  # automations never trigger automations
    q = session.info.setdefault("automation_events", [])
    for obj in session.new:
        if isinstance(obj, PhoneCall) and obj.direction == "inbound" and obj.result in ("missed", "voicemail") and obj.source == "telus":
            q.append(("missed_call", obj))
        elif isinstance(obj, CalendarEvent) and obj.status == "requested":
            q.append(("booking_requested", obj))
        elif isinstance(obj, Contact) and not obj.is_demo:
            q.append(("new_contact", obj))
        elif isinstance(obj, Task) and obj.priority == "high":
            q.append(("urgent_task", obj))
        elif isinstance(obj, Message) and obj.direction == "in" and obj.channel == "sms":
            q.append(("text_received", obj))


def _after_commit(session: Session) -> None:
    items = session.info.pop("automation_events", [])
    if not items:
        return
    found = [(name, getattr(obj, "id", None)) for name, obj in items if getattr(obj, "id", None)]
    if not found:
        return
    if os.environ.get("WALNUT_ENV") == "test":
        handle(found)  # tests: right away, so results are predictable
    else:
        threading.Thread(target=handle, args=(found,), daemon=True).start()


def _after_rollback(session: Session) -> None:
    session.info.pop("automation_events", None)


def handle(found: list[tuple[str, int]]) -> None:
    from app import db as app_db
    try:
        with app_db.get_sessionmaker()() as s:
            s.info["automation"] = True
            autos = s.scalars(select(Automation).where(Automation.enabled.is_(True), Automation.trigger_type == "event")).all()
            for name, oid in found:
                for a in autos:
                    if json.loads(a.trigger_json or "{}").get("event") == name:
                        run(s, a, _event_context(s, name, oid), by=name)
    except Exception as e:  # noqa: BLE001
        log.warning("automation event failed (%s)", type(e).__name__)


def install() -> None:
    """Listen on every session (called once at start)."""
    if getattr(install, "_done", False):
        return
    event.listen(Session, "after_flush", _collect)
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_soft_rollback", lambda s, prev: _after_rollback(s))
    install._done = True  # type: ignore[attr-defined]
