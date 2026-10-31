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

from app.models import Contact, PhoneCall
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
        db.add(PhoneCall(started_at=at, direction=r.get("d") if r.get("d") in ("inbound", "outbound") else "unknown", handled_by=handled,
                         agent_label="", contact_id=c.id if c else None, phone=n, duration_seconds=max(0, int(r.get("s") or 0)), result=result,
                         call_type=("patient" if c.kind == "patient" else "inquiry") if c else "unknown", short_note="", transcript=None,
                         source="telus", external_ref=ref, recording_id=rec, is_demo=False))
        have.add(ref)
        counts["added"] += 1
        if c:
            touched.add(c.id)
            crm.touch_contact(db, c, at)
    if counts["added"] or counts["numbers_added"]:
        ev.record(db, ev.PHONE, "calls_synced", f"{counts['added']} new calls from TELUS", "TELUS", "phone_call", None, counts)
        for cid in touched:
            ev.queue_publish(db, "contacts", cid, None)
    db.commit()
    return counts


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
