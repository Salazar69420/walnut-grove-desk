"""Texting mode (practice, test numbers only, live) and the hand-off to the TELUS send helper.

The web app never holds TELUS credentials. A real text is a request file in <bridge>/send/requests/; the send helper (its own
credentials, its own allowlist) sends it and leaves <bridge>/send/results/<id>.json. The helper re-checks the mode itself, because the
mode is also written to <bridge>/send_config.json.

Modes: practice (the default; nothing leaves the server), test_numbers_only (only numbers on the list are really texted, everyone else
still gets a practice message), live (any valid Canadian number). Campaigns never send for real.
"""
from __future__ import annotations

import os
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import Message, Outbox
from app.services import crm, settings_store, timeutil

MODES = ("practice", "test_numbers_only", "live")
KEY_MODE, KEY_NUMBERS = "sms_mode", "sms_test_numbers"
WAIT_FOR_SENDER = timedelta(minutes=5)
CODE_TEXT = {
    "practice_mode": "Texting is in practice mode, so nothing was sent.",
    "not_allowed_number": "That number is not on the test list, so nothing was sent.",
    "rate_limited_local": "Too many texts this hour. Try again later.",
    "not_configured": "The sending login is not set up on the server yet.",
    "login_failed": "TELUS did not accept the sending login.",
    "no_permission": "The sending login is not allowed to send texts.",
    "no_sms_number": "The clinic has no text-capable number to send from.",
    "rejected": "TELUS refused this text. Check the number.",
    "rate_limited": "TELUS asked us to slow down. Try again in a minute.",
    "sender_not_running": "The sending helper did not answer. An admin should check it.",
    "error": "The text could not be sent. Try again.",
}


def get(db: Session) -> dict[str, Any]:
    mode = settings_store.get_raw(db, KEY_MODE, "practice")
    nums = settings_store.get_raw(db, KEY_NUMBERS, [])
    return {"mode": mode if mode in MODES else "practice", "test_numbers": [n for n in nums if isinstance(n, str)][:20]}


def _bridge():
    from app.api import admin_telus as b
    return b


def sender_ready() -> bool:
    b = _bridge()
    return b.bridge_ok() and b._real_dir(os.path.join(b._bridge(), "send", "requests"))


def write_bridge_config(mode: str, numbers: list[str], who: str) -> None:
    b = _bridge()
    b._need_bridge()
    b._atomic_write(b._bridge(), os.path.join(b._bridge(), "send_config.json"),
                    {"v": 1, "mode": mode, "test_numbers": numbers, "updated_by": who[:60], "updated_at": b._now_iso()})


def put(db: Session, mode: str, numbers: list[str], who: str) -> dict[str, Any]:
    if mode not in MODES:
        raise crm.CrmError(400, "Unknown texting mode.")
    clean: list[str] = []
    for raw in numbers[:20]:
        n = crm.normalize_phone(raw)
        if not n:
            raise crm.CrmError(400, "One of the test numbers is not a valid Canadian phone number.")
        if n not in clean:
            clean.append(n)
    if mode == "test_numbers_only" and not clean:
        raise crm.CrmError(400, "Add at least one test number first.")
    write_bridge_config(mode, clean, who)  # the helper's copy first: if this fails, nothing changes
    settings_store.put_raw(db, KEY_MODE, mode, by=who)
    settings_store.put_raw(db, KEY_NUMBERS, clean, by=who)
    return {"mode": mode, "test_numbers": clean}


def is_real_target(db: Session, phone: str | None) -> bool:
    m = get(db)
    return bool(phone) and (m["mode"] == "live" or (m["mode"] == "test_numbers_only" and phone in m["test_numbers"]))


def request_send(kind: str, to: str, text: str | None, user, from_number: str | None = None) -> str:
    """Leave one request for the send helper. Raises CrmError(503) when the helper is not set up."""
    b = _bridge()
    if not sender_ready():
        raise crm.CrmError(503, "The sending helper is not set up on this server yet, so nothing was sent.")
    job = uuid.uuid4().hex
    body: dict[str, Any] = {"v": 1, "id": job, "kind": kind, "to": to, "requested_by": b._who(user) if user else "Assistant", "requested_at": b._now_iso()}
    if text is not None:
        body["text"] = text
    if from_number:
        body["from_number"] = from_number
    try:
        b._atomic_write(b._bridge(), os.path.join(b._bridge(), "send", "requests", job + ".json"), body)
    except OSError:
        raise crm.CrmError(503, "Could not hand the text to the sending helper. Nothing was sent.")
    return job


def read_result(job: str) -> dict[str, Any] | None:
    b = _bridge()
    return b.read_json_file(os.path.join(b._bridge(), "send", "results"), f"{job}.json")


def refresh(db: Session, messages: list[Message]) -> int:
    """Fold the helper's results into queued messages. Returns how many changed. The caller commits."""
    changed = 0
    now = timeutil.now_utc()
    for m in messages:
        if m.delivery_state != "queued" or not m.send_job_id:
            continue
        res = read_result(m.send_job_id)
        if res is None:
            if now - crm.aware(m.created_at) > WAIT_FOR_SENDER:
                m.delivery_state, m.delivery_code = "failed", "sender_not_running"
                changed += 1
            else:
                continue
        else:
            state = str(res.get("state"))
            code = str(res.get("code") or "error")[:24]
            if state == "sent":
                m.delivery_state, m.delivery_code = "sent", None
            elif state in ("failed", "blocked"):
                m.delivery_state, m.delivery_code = "failed", code
            else:
                continue
            changed += 1
        o = db.query(Outbox).filter(Outbox.source_type == "message", Outbox.source_id == str(m.id)).first()
        if o is not None:
            o.status = "sent" if m.delivery_state == "sent" else "failed"
    return changed
