"""One-off owner-approved import: real calls from a transcripts CSV into the app's Phone log.

    cd /opt/walnut-desk
    sudo -u walnut-app bash -c 'set -a; . /etc/walnut-desk/app.env; set +a; \
        .venv/bin/python -m pipeline.app_import --csv /path/to/all_transcripts.csv [--dry-run]'

What it does, and why it is built this way (docs/SECURITY.md, docs/CALL_ANALYSIS.md):
* It labels each call with the same rules the private pipeline uses (pipeline/rules.py) and imports only calls judged to be
  clinic work. Calls flagged private, staff-to-staff, personal or "when in doubt" are skipped and only counted.
* The transcript text is stored in the app database (owners only, every open is written to the access log).
* The CSV's phone numbers and file names are never stored: the export has no usable caller numbers, and file names contain them.
  A keyed hash of the file name is kept so running the import again adds nothing.
* Notes are built from fixed vocabularies only (intent, outcome, red-flag names), never from call text.
* Nothing is printed from the transcripts. The summary shows counts only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import re
import sys
from datetime import date, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backend"))

from pipeline.rules import label_call, load_rules  # noqa: E402

INTENT_NOTE = {
    "booking": "Booking", "reschedule_or_cancel": "Reschedule or cancel", "refill": "Refill", "food_or_supply_order": "Food or supply order",
    "pickup_ready": "Pickup", "results_question": "Asked about results", "results_call_out": "Results call", "recall_or_reminder": "Reminder",
    "check_in": "Check-in on a pet", "records_request": "Records request", "courier_or_lab_logistics": "Lab or courier", "pricing_or_policy_question": "Price or policy question",
    "triage_symptom": "Symptom question", "billing_or_insurance": "Billing or insurance", "complaint": "Complaint", "vendor_support": "Vendor", "other": "Call",
    "internal_logistics": "Internal",
}
OUTCOME_RESULT = {
    "resolved_on_call": "answered", "callback_promised": "callback_task", "voicemail_left": "voicemail", "no_answer": "missed",
    "transferred": "answered", "hold_then_dropped": "missed", "unclear": "answered",
}
SKIP_CALLER = {"staff_internal", "private_personal"}
EXT_RX = re.compile(r"^\d{8}-\d{6}_(\d+)_")


def ref_for(key: bytes, name: str) -> str:
    return "imp-" + hmac.new(key, name.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def convert(row: dict, rules, key: bytes):
    """Returns (fields dict, None) for a call to import, or (None, reason) when it is skipped."""
    text = row.get("plain_text") or row.get("transcript") or ""
    seconds = float(row.get("audio_seconds") or 0)
    m = EXT_RX.match(row.get("file", ""))
    ext = m.group(1) if m else None
    lab = label_call(rules, text, seconds, row.get("language"), has_number=bool(row.get("number")), ext=ext)
    if lab.contains_private_content:
        return None, "private_or_uncertain"
    if lab.caller_type in SKIP_CALLER or lab.real_direction == "internal":
        return None, "staff_or_personal"
    outcome = lab.outcome
    result = OUTCOME_RESULT.get(outcome, "answered")
    if result == "answered" and lab.intent == "booking" and outcome == "resolved_on_call":
        result = "booked"
    note_parts = [INTENT_NOTE.get(lab.intent, "Call")]
    if lab.promised_followup:
        note_parts.append("follow-up promised")
    if lab.urgency == "emergency_signs":
        note_parts.append("emergency signs mentioned")
    if lab.red_flags:
        note_parts.append(", ".join(f.replace("_", " ") for f in lab.red_flags[:3]))
    d = date.fromisoformat(row["date"])
    hh, mm, *_ = (int(x) for x in row["time"].split(":"))
    return {
        "local_date": d, "local_time": time(hh, mm), "direction": lab.real_direction if lab.real_direction in ("inbound", "outbound") else "unknown",
        "handled_by": "none" if outcome in ("no_answer", "hold_then_dropped") else "human",
        "agent_label": f"Line {ext}" if ext else "", "duration_seconds": int(seconds), "result": result,
        "short_note": "; ".join(note_parts)[:240], "transcript": row.get("transcript") or text, "external_ref": ref_for(key, row.get("file", "")),
    }, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv", required=True)
    ap.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    ap.add_argument("--db", help="app database path (default: from the app environment)")
    a = ap.parse_args(argv)

    from sqlalchemy import select

    from app import config, db
    from app.models import PhoneCall
    from app.services import events as ev
    from app.services import timeutil

    cfg = config.get_settings()
    key = cfg.secret_key.encode("utf-8")
    db.init_engine(a.db or cfg.db_path)
    rules = load_rules()
    csv.field_size_limit(10**9)
    with open(a.csv, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    stats = {"rows": len(rows), "imported": 0, "already_there": 0, "skipped_private_or_uncertain": 0, "skipped_staff_or_personal": 0, "bad_rows": 0}
    with db.get_sessionmaker()() as s:
        have = set(s.scalars(select(PhoneCall.external_ref).where(PhoneCall.external_ref.is_not(None))).all())
        new: list[PhoneCall] = []
        for r in rows:
            try:
                f, why = convert(r, rules, key)
            except Exception:  # a malformed row must not stop the import; it is only counted
                stats["bad_rows"] += 1
                continue
            if f is None:
                stats["skipped_" + why] += 1
                continue
            if f["external_ref"] in have:
                stats["already_there"] += 1
                continue
            have.add(f["external_ref"])
            new.append(PhoneCall(
                started_at=timeutil.at_local(f["local_date"], f["local_time"].hour, f["local_time"].minute), direction=f["direction"],
                handled_by=f["handled_by"], agent_label=f["agent_label"], contact_id=None, phone=None, duration_seconds=f["duration_seconds"],
                result=f["result"], call_type="unknown", short_note=f["short_note"], transcript=f["transcript"], source="import",
                external_ref=f["external_ref"], is_demo=False))
        stats["imported"] = len(new)
        if not a.dry_run and new:
            s.add_all(new)
            # the invented example calls would only confuse the log now
            s.query(PhoneCall).filter(PhoneCall.is_demo.is_(True)).delete()
            ev.record(s, ev.PHONE, "calls_imported", f"Imported {len(new)} real calls into the call log", "Admin import", "phone_call", None, {"count": len(new)})
            s.commit()
    print(("DRY RUN, nothing written. " if a.dry_run else "Done. ") + ", ".join(f"{k}={v}" for k, v in stats.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
