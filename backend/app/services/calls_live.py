"""Fold the latest TELUS calls (<bridge>/calls/live.json, written by `telus-calls`) into the Phone call log.

Each call is stored once (external_ref "tel-<TELUS id>"); a call already imported from the recordings (same recording id)
only gets its number. A call is linked to a contact when exactly one contact has that number. Read only when the file changed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Contact, Message, PhoneCall, Task
from app.services import crm
from app.services import events as ev

log = logging.getLogger("walnut.calls_live")
MAX_BYTES = 2 * 1024 * 1024
E164 = re.compile(r"^\+1[2-9][0-9]{2}[2-9][0-9]{6}$")
ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
RESULT = {"answered": ("human", "answered"), "missed": ("none", "missed"), "hangup": ("none", "missed"), "voicemail": ("none", "voicemail"),
          "failed": ("none", "missed"), "other": ("human", "answered")}
_seen = {"mtime": 0.0}


def _path() -> str:
    from app.api import admin_telus as b
    return os.path.join(b._bridge(), "calls", "live.json")


def _read(path: str) -> dict | None:
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES or st.st_mtime == _seen["mtime"]:
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            data = json.loads(fh.read(MAX_BYTES + 1))
        _seen["mtime"] = st.st_mtime
        return data if isinstance(data, dict) and data.get("v") == 1 else None
    except (OSError, ValueError):
        return None


def ingest(db: Session, data: dict) -> dict[str, int]:
    counts = {"added": 0, "numbers_added": 0}
    rows = [r for r in (data.get("calls") or []) if isinstance(r, dict) and isinstance(r.get("id"), str) and ID.match(r["id"])][:3000]
    if not rows:
        return counts
    refs = {f"tel-{r['id']}" for r in rows}
    have = set(db.scalars(select(PhoneCall.external_ref).where(PhoneCall.external_ref.in_(refs))).all())
    recs = {r["rec"] for r in rows if isinstance(r.get("rec"), str) and ID.match(r["rec"])}
    by_rec = {c.recording_id: c for c in db.scalars(select(PhoneCall).where(PhoneCall.recording_id.in_(recs))).all()} if recs else {}
    nums = {r["n"] for r in rows if isinstance(r.get("n"), str) and E164.match(r["n"])}
    owners: dict[str, list[Contact]] = {}
    for c in db.scalars(select(Contact).where(Contact.phone.in_(nums), Contact.erased_at.is_(None))).all() if nums else []:
        owners.setdefault(c.phone, []).append(c)
    touched: set[int] = set()
    new_calls: list[PhoneCall] = []
    for r in rows:
        ref = f"tel-{r['id']}"
        n = r.get("n") if isinstance(r.get("n"), str) and E164.match(r["n"]) else None
        rec = r.get("rec") if isinstance(r.get("rec"), str) and ID.match(r["rec"]) else None
        if ref in have:
            continue
        if rec and rec in by_rec:  # already in the log from the recordings import
            old = by_rec[rec]
            if n and not old.phone:
                old.phone = n
                counts["numbers_added"] += 1
            continue
        try:
            at = crm.aware(datetime.fromisoformat(str(r.get("at"))))
        except ValueError:
            continue
        handled, result = RESULT.get(str(r.get("r")), RESULT["other"])
        who = owners.get(n, []) if n else []
        c = who[0] if len(who) == 1 else None
        new_calls.append(PhoneCall(started_at=at, direction=r.get("d") if r.get("d") in ("inbound", "outbound") else "unknown", handled_by=handled,
                         agent_label="", contact_id=c.id if c else None, phone=n, duration_seconds=max(0, int(r.get("s") or 0)), result=result,
                         call_type=("patient" if c.kind == "patient" else "inquiry") if c else "unknown", short_note="", transcript=None,
                         source="telus", external_ref=ref, recording_id=rec, is_demo=False))
        db.add(new_calls[-1])
        have.add(ref)
        counts["added"] += 1
        if c:
            touched.add(c.id)
            crm.touch_contact(db, c, at)
    db.flush()
    counts["linked"] = relink(db, touched)
    counts["callbacks"] = callback_tasks(db, new_calls)
    close_answered_callbacks(db, new_calls)
    ensure_call_messages(db)
    if counts["added"] or counts["numbers_added"] or counts["linked"] or counts["callbacks"]:
        ev.record(db, ev.PHONE, "calls_synced", f"{counts['added']} new calls from TELUS", "TELUS", "phone_call", None, counts)
        for cid in touched:
            ev.queue_publish(db, "contacts", cid, None)
    db.commit()
    return counts


def call_label(c: PhoneCall) -> str:
    if c.short_note:
        return c.short_note
    if c.result == "voicemail":
        return "Left a voicemail"
    if c.result == "missed":
        return "Missed call" if c.direction == "inbound" else "No answer"
    m, sec = divmod(c.duration_seconds or 0, 60)
    return f"Talked for {m} min {sec} s" if m else f"Talked for {sec} s"


def ensure_call_messages(db: Session) -> int:
    """Every call linked to a contact shows in that contact's conversation (All and Calls), with its play and transcript buttons."""
    have = select(Message.phone_call_id).where(Message.phone_call_id.is_not(None))
    rows = db.scalars(select(PhoneCall).where(PhoneCall.contact_id.is_not(None), PhoneCall.id.not_in(have)).limit(5000)).all()
    for c in rows:
        out = c.direction == "outbound"
        db.add(Message(contact_id=c.contact_id, channel="call", direction="out" if out else "in", sender_kind="staff" if out else "client",
                       body=call_label(c), delivery_state="received", created_at=c.started_at, phone_call_id=c.id, is_demo=False))
    return len(rows)


def callback_tasks(db: Session, calls: list[PhoneCall]) -> int:
    """A missed call or voicemail from a client becomes a callback task, unless we already spoke with that number since."""
    from datetime import timedelta
    from app.services import timeutil
    n = 0
    for c in calls:
        if c.direction != "inbound" or c.result not in ("missed", "voicemail") or not c.phone:
            continue
        if timeutil.now_utc() - crm.aware(c.started_at) > timedelta(days=2):
            continue  # do not raise tasks for old history on the first pull
        later = db.scalar(select(PhoneCall.id).where(PhoneCall.phone == c.phone, PhoneCall.started_at > c.started_at, PhoneCall.result == "answered").limit(1))
        if later:
            continue  # they got through, or we called them back
        open_t = db.scalar(select(Task).where(Task.kind == "callback", Task.status != "done", Task.reason.like(f"%{crm.phone_display(c.phone)}%")).limit(1))
        who = db.get(Contact, c.contact_id) if c.contact_id else None
        name = who.display_name if who else crm.phone_display(c.phone)
        what = "left a voicemail" if c.result == "voicemail" else "called and nobody answered"
        line = f"{name} {what} at {timeutil.local(c.started_at).strftime('%a %b %d, %I:%M %p').replace(' 0', ' ')}. Number: {crm.phone_display(c.phone)}."
        if open_t:
            open_t.reason = (open_t.reason + "\n" + line)[:2000]  # one task per caller: later attempts are added to it
            continue
        t = Task(kind="callback", classification=who.kind if who else "inquiry", title=f"Call back {name}"[:200], reason=line, contact_id=who.id if who else None,
                 priority="high" if c.result == "voicemail" else "normal", status="open", raised_by="rule", created_at=timeutil.now_utc())
        db.add(t)
        db.flush()
        ev.record(db, ev.TASKS, "callback_task_raised", f"Callback task: {name} {what}", "TELUS", "task", t.id)
        n += 1
    return n


def close_answered_callbacks(db: Session, calls: list[PhoneCall]) -> int:
    """When we get through to a number (either way), its open callback task is done."""
    from app.services import timeutil
    n = 0
    for c in calls:
        if c.result != "answered" or not c.phone:
            continue
        from sqlalchemy import func
        last_missed = db.scalar(select(func.max(PhoneCall.started_at)).where(PhoneCall.phone == c.phone, PhoneCall.direction == "inbound",
                                                                          PhoneCall.result.in_(("missed", "voicemail"))))
        if last_missed is None or crm.aware(c.started_at) <= crm.aware(last_missed):
            continue  # this conversation was before their latest missed call
        for t in db.scalars(select(Task).where(Task.kind == "callback", Task.status != "done",
                                              Task.reason.like(f"%{crm.phone_display(c.phone)}%"))).all():
            t.status, t.done_at = "done", timeutil.now_utc()
            t.reason = (t.reason + f"\nClosed automatically: a call with this number was answered at {timeutil.local(c.started_at).strftime('%I:%M %p').lstrip('0')}.")[:2000]
            n += 1
    return n


def relink(db: Session, touched: set[int] | None = None) -> int:
    """Calls with a number but no contact get linked once exactly one contact has that number (a contact added or given a number later)."""
    rows = db.scalars(select(PhoneCall).where(PhoneCall.contact_id.is_(None), PhoneCall.phone.is_not(None)).limit(5000)).all()
    if not rows:
        return 0
    owners: dict[str, list[Contact]] = {}
    for c in db.scalars(select(Contact).where(Contact.phone.in_({r.phone for r in rows}), Contact.erased_at.is_(None))).all():
        owners.setdefault(c.phone, []).append(c)
    n = 0
    for r in rows:
        who = owners.get(r.phone, [])
        if len(who) == 1:
            r.contact_id, r.call_type = who[0].id, "patient" if who[0].kind == "patient" else "inquiry"
            n += 1
            if touched is not None:
                touched.add(who[0].id)
    return n


def run_once() -> dict[str, int]:
    data = _read(_path())
    if data is None:
        return {}
    from app import db as app_db
    with app_db.get_sessionmaker()() as s:
        try:
            return ingest(s, data)
        except Exception as e:  # noqa: BLE001 - never stop the loop; nothing from the data is logged
            s.rollback()
            _seen["mtime"] = 0.0
            log.error("live calls ingest failed (%s)", type(e).__name__)
            return {}


REASONS = {
    "write_scopes": "The TELUS login on the server can also send and change things, so the read-only call sync refuses to use it. "
                    "Create a TELUS app with only Read permissions and run telus-setup --force on the server.",
    "telus_error": "TELUS did not answer the last check. It will try again in a few minutes.",
    "never": "The call sync has not run yet on this server (the walnut-telus-calls timer is not installed).",
}


def status(db: Session) -> dict:
    from sqlalchemy import func
    from app.api import admin_telus as b
    from app.services import timeutil
    st = b.read_json_file(os.path.join(b._bridge(), "calls"), "status.json") or {}
    now = timeutil.now_utc()
    try:
        at = crm.aware(datetime.fromisoformat(str(st.get("at"))))
    except ValueError:
        at = None
    age = (now - at).total_seconds() if at else None
    if not st or age is None:
        state, code = "off", "never"
    elif not st.get("ok"):
        state, code = "off", str(st.get("code") or "telus_error")
    elif age > 600:
        state, code = "delayed", "telus_error"
    else:
        state, code = "live", "ok"
    latest = db.scalar(select(func.max(PhoneCall.started_at)).where(PhoneCall.source == "telus"))
    today = db.scalar(select(func.count()).select_from(PhoneCall).where(PhoneCall.source == "telus", PhoneCall.started_at >= crm.local_day_start(timeutil.today_local()))) or 0
    return {"state": state, "checked_at": timeutil.iso(at) if at else None, "latest_call_at": timeutil.iso(crm.aware(latest)) if latest else None,
            "calls_today": today, "reason": None if state == "live" else REASONS.get(code, REASONS["telus_error"])}
