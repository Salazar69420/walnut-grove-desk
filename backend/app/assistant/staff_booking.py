"""A time a staff member agrees with a client by text goes into the Calendar as a request, so nobody has to type it in.

After a staff text that mentions a day or a time, the model reads the last texts with that client and says whether a specific
day and time were agreed. If so (a future time inside clinic hours, not already in the Calendar), a REQUESTED appointment and a
"Confirm text booking" task are added; any staff member approves or declines it in the Calendar. Runs after the request, with
no database transaction open while the model thinks; never raises.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import date, timedelta

from sqlalchemy import select

log = logging.getLogger("walnut.staff_booking")
TIME_HINT = re.compile(r"\b(\d{1,2}(:\d{2})?\s*([ap]\.?\s?m\b\.?)|\d{1,2}:\d{2}|noon|monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today|"
                       r"tonight|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b", re.I)
SCHEMA = {"type": "object", "properties": {
    "agreed": {"type": "boolean"}, "date": {"type": "string"}, "time": {"type": "string"}, "visit": {"type": "string"}},
    "required": ["agreed", "date", "time", "visit"], "additionalProperties": False}
PROMPT = ("You read text messages between a vet clinic and a client. Did the clinic and the client agree on a specific appointment day AND time "
          "(the clinic proposed or confirmed it and the client did not refuse)? Work out words like tomorrow or Monday from today's date. "
          "Reply with JSON only: agreed, date (YYYY-MM-DD), time (HH:MM 24-hour), visit (what it is for, or empty).")


def maybe_after_staff_text(contact_id: int, body: str) -> None:
    import os
    if os.environ.get("WALNUT_ENV") == "test":
        return  # tests call check_now() directly
    if TIME_HINT.search(body or ""):
        threading.Thread(target=_run, args=(contact_id,), daemon=True).start()


def _run(contact_id: int) -> None:
    time.sleep(3)  # let the request that sent the text commit first
    check_now(contact_id)


def check_now(contact_id: int) -> None:
    from app import db as app_db
    from app.assistant import ai, booking
    from app.models import Contact, Message
    from app.services import timeutil
    from app.services.transcribe import _post
    S = app_db.get_sessionmaker()
    try:
        with S() as s:
            cfg = ai.get_config(s)
            msgs = list(reversed(s.scalars(select(Message).where(Message.contact_id == contact_id, Message.channel == "sms")
                                           .order_by(Message.created_at.desc(), Message.id.desc()).limit(12)).all()))
            convo = "\n".join(f"{'Client' if m.direction == 'in' else 'Clinic'}: {(m.body or '').strip()[:400]}" for m in msgs)
            today = timeutil.local(timeutil.now_utc()).strftime("%A %Y-%m-%d")
            s.commit()
        if not cfg.get("key_enc") or not convo:
            return
        content = _post(cfg, {"model": cfg["model"], "temperature": 0, "max_tokens": 300,
                              "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": f"Today: {today}\n\n{convo}"}],
                              "response_format": {"type": "json_schema", "json_schema": {"name": "agreed_time", "strict": True, "schema": SCHEMA}}})
        d = json.loads(re.search(r"\{.*\}", content, re.S).group(0))
        if not d.get("agreed"):
            return
        hh, mm = (int(x) for x in str(d["time"]).split(":")[:2])
        with S() as s:
            start = timeutil.at_local(date.fromisoformat(str(d["date"])), hh, mm)
            c = s.get(Contact, contact_id)
            if c is None or c.erased_at is not None or start < timeutil.now_utc() + timedelta(minutes=10) or not booking.within_hours(s, start):
                return
            if booking.request_from_conversation(s, c, start, str(d.get("visit") or ""), how="in a staff text") is not None:
                s.commit()
    except Exception as e:  # noqa: BLE001 - a bonus; nothing from the texts is logged
        log.warning("staff booking check skipped (%s)", type(e).__name__)
