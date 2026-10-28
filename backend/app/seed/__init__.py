"""Demo seed. Everything here is invented example data (see reference_data.py).

reset_demo() rebuilds the operational tables and never touches users, settings,
call_analytics, imported_calls, pims_* or events (it appends events only).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    AppointmentRequest,
    Callback,
    CallLogDemo,
    Conversation,
    Draft,
    EmergencyRouting,
    Event,
    Hospital,
    LabResult,
    OpenSlot,
    Outbox,
    Shift,
    SimSession,
    Suggestion,
    User,
    VisitType,
)
from app.security.passwords import generate_password, hash_password
from app.seed import reference_data as ref
from app.services import events as ev
from app.services import timeutil

# Operational tables, in delete order (children first).
RESET_TABLES = [
    Outbox, EmergencyRouting, AppointmentRequest, SimSession, Callback, Draft, Shift,
    Conversation, LabResult, Suggestion, OpenSlot, Hospital, VisitType, CallLogDemo,
]


def _num(ref_id: str) -> int:
    """'c5' -> 5."""
    return int(re.sub(r"\D", "", ref_id))


# (weekday 0=Mon, hour, count) of unanswered calls, last week (41) and the week before (32).
_MISSED_LAST = [(1, 8, 4), (1, 9, 5), (3, 8, 4), (3, 9, 5), (5, 9, 6), (0, 9, 3), (2, 9, 3), (4, 9, 2),
                (0, 12, 2), (2, 12, 1), (4, 12, 1), (1, 15, 1), (3, 15, 1), (0, 16, 1), (2, 16, 1), (4, 16, 1)]
_MISSED_PRIOR = [(1, 8, 3), (1, 9, 3), (3, 8, 4), (3, 9, 5), (5, 9, 3), (0, 9, 2), (2, 9, 2), (4, 9, 1),
                 (0, 12, 2), (2, 12, 1), (4, 12, 1), (1, 15, 1), (3, 15, 1), (0, 16, 1), (2, 16, 1), (4, 16, 1)]


def _call_log_rows(now: datetime) -> list[CallLogDemo]:
    today = timeutil.today_local(now)
    this_monday = today - timedelta(days=today.weekday())
    rows: list[CallLogDemo] = []
    for week_offset, missed, rang_again in ((1, _MISSED_LAST, 28), (2, _MISSED_PRIOR, 22)):
        monday = this_monday - timedelta(days=7 * week_offset)
        # answered calls: the example hourly volume on weekdays
        for wd in range(5):
            for idx, n in enumerate(ref.EXAMPLE_CALLS):
                hour = 8 + idx
                for k in range(n):
                    rows.append(CallLogDemo(ts=timeutil.at_local(monday + timedelta(days=wd), hour, (k * 7) % 60),
                                            weekday=wd, hour=hour, answered=True, rang_again_within_hour=False))
        i = 0
        for wd, hour, count in missed:
            for k in range(count):
                rows.append(CallLogDemo(ts=timeutil.at_local(monday + timedelta(days=wd), hour, (k * 11 + 3) % 60),
                                        weekday=wd, hour=hour, answered=False, rang_again_within_hour=i < rang_again))
                i += 1
    return rows


def _next_weekday(today, weekday: int):
    delta = (weekday - today.weekday()) % 7 or 7
    return today + timedelta(days=delta)


def reset_demo(db: Session, actor=None, now: datetime | None = None) -> dict[str, int]:
    """Rebuild demo data. Caller commits. Returns row counts per table."""
    now = now or timeutil.now_utc()
    today = timeutil.today_local(now)

    for model in RESET_TABLES:
        db.execute(delete(model))
    db.flush()

    users_by_name = {u.display_name: u.id for u in db.scalars(select(User)).all()}

    # visit types and hospitals
    vt_by_name: dict[str, VisitType] = {}
    for i, v in enumerate(ref.VISIT_TYPES):
        row = VisitType(name=v["name"], minutes=v["minutes"], bookable=v["bookable"], sort=i)
        db.add(row)
        vt_by_name[v["name"]] = row
    hospitals: list[Hospital] = []
    for i, h in enumerate(ref.HOSPITALS):
        row = Hospital(name=h["name"], place=h["place"], phone="", address="", hours_claim=h["hours_claim"],
                       confirmed=h["confirmed"], is_placeholder=h["is_placeholder"], sort=i)
        db.add(row)
        hospitals.append(row)
    db.flush()

    # open slots: the next Thursday 9:30, Friday 2:00 pm and Monday 3:30 pm (as in the reference)
    slots: dict[str, OpenSlot] = {}
    for wd, h, m in ((3, 9, 30), (4, 14, 0), (0, 15, 30)):
        start = timeutil.at_local(_next_weekday(today, wd), h, m)
        slot = OpenSlot(start=start, provider="Dr. Garg", taken=False)
        db.add(slot)
        slots[timeutil.slot_display(start)] = slot

    # suggestions, drafts, results
    suggestion_rows = {}
    for s in ref.SUGGESTIONS:
        row = Suggestion(id=_num(s["id"]), rule_key=s["rule_key"], title=s["title"], why=s["why"],
                         status=s["status"], created_at=timeutil.at_local(today, 7, 30), evidence_json="{}")
        db.add(row)
        suggestion_rows[s["id"]] = row
    for d in ref.DRAFTS:
        db.add(Draft(id=_num(d["id"]), kind=d["kind"], to_label=d["to"], body=d["body"], channel="text",
                     status=d["status"], created_by_tool="Recall sender", created_at=timeutil.at_local(today, 9, 12)))
    for r in ref.RESULTS:
        received = timeutil.at_local(today - timedelta(days=r["age"]), 8, 47)
        db.add(LabResult(id=_num(r["id"]), pet_label=r["pet"], owner_label=r["owner"], test=r["test"],
                         received_at=received, stage=r["stage"], updated_at=received,
                         reviewed_by="Dr. Garg" if r["stage"] >= 1 else None,
                         told_by="Anita" if r["stage"] >= 2 else None))

    # shifts
    for s in ref.ROSTER:
        db.add(Shift(weekday=s["weekday"], person_label=s["who"], user_id=users_by_name.get(s["who"]),
                     role=s["role"], start_min=s["start_min"], end_min=s["end_min"], proposed=False))
    db.flush()

    # overnight conversations (pm times are the evening before)
    conv_rows: dict[str, Conversation] = {}
    for o in ref.OVERNIGHT:
        created = timeutil.anchor(o["at"], now, pm_is_yesterday=True)
        conv = Conversation(
            id=_num(o["id"]), channel=o["channel"].lower(), client_label=o["who"], pet_label=o["pet"],
            kind=o["kind"], status=o["status"], preferred_slot=o.get("pref"), summary=o["summary"],
            messages_json=json.dumps([{"who": w, "text": t} for w, t in o["conv"]]),
            source="seed", is_test=False, created_at=created,
        )
        db.add(conv)
        conv_rows[o["id"]] = conv
    db.flush()
    for o in ref.OVERNIGHT:
        conv = conv_rows[o["id"]]
        if o["kind"] == "booking":
            vt = vt_by_name.get("Vaccines" if "vaccine" in o["summary"].lower() else "Yearly check-up")
            slot = slots.get(o.get("pref") or "")
            db.add(AppointmentRequest(conversation_id=conv.id, visit_type_id=vt.id if vt else None,
                                      slot_start=slot.start if slot else None, status="requested", created_at=conv.created_at))
        elif o["kind"] == "emergency":
            hosp = hospitals[0] if hospitals else None
            db.add(EmergencyRouting(conversation_id=conv.id, hospital_id=hosp.id if hosp else None,
                                    matched_terms="[]", layer="1", created_at=conv.created_at))

    # callbacks; a callback that the overnight assistant took links to its conversation
    cb_conv = {(o["who"], o["pet"]): conv_rows[o["id"]] for o in ref.OVERNIGHT if o["kind"] == "callback"}
    for c in ref.CALLBACKS:
        conv = cb_conv.get((c["who"], c["pet"]))
        owner = c["owner"]
        db.add(Callback(
            id=_num(c["id"]), client_label=c["who"], pet_label=c["pet"], what=c["what"], kind=c["kind"], via=c["via"],
            received_at=timeutil.anchor(c["at"], now), priority=c["prio"], red_flag=bool(c.get("flag")),
            owner_id=users_by_name.get(owner) if owner else None, owner_name=owner, status=c["status"],
            source="overnight" if conv else "seed", conversation_id=conv.id if conv else None,
            closed_at=timeutil.anchor(c["at"], now) + timedelta(minutes=30) if c["status"] == "done" else None,
        ))

    for row in _call_log_rows(now):
        db.add(row)
    db.flush()

    # events: the reference feed once (first seeding only), plus one reset line every time
    if not db.scalar(select(Event.id).where(Event.action == "seed_log").limit(1)):
        for line in reversed(ref.EVENT_LOG):
            ev.record(db, line["tool"], "seed_log", line["text"], None, "seed", "", ts=timeutil.anchor(line["t"], now), publish=False)
    reset_event = ev.record(db, ev.ADMIN, "demo_reset", "Demo data reset", actor, "demo", "")
    for entity in sorted(ev.ENTITIES - {"events", "users", "settings", "analytics"}):
        ev.queue_publish(db, entity, None, reset_event.id)

    counts = {}
    for model in RESET_TABLES:
        counts[model.__tablename__] = db.scalar(select(func.count()).select_from(model)) or 0
    return counts


def create_users(db: Session) -> list[tuple[str, str]]:
    """Create the 6 accounts with random 20-char passwords. Refuses to overwrite anyone."""
    existing = {u for (u,) in db.execute(select(User.username))}
    clash = sorted(existing & {p["username"] for p in ref.PEOPLE})
    if clash:
        raise RuntimeError("users already exist, refusing to overwrite: " + ", ".join(clash))
    out: list[tuple[str, str]] = []
    now = timeutil.now_utc()
    for p in ref.PEOPLE:
        password = generate_password(20)
        db.add(User(
            username=p["username"], display_name=p["display_name"], title=p["title"], initials=p["initials"],
            role=p["role"], is_admin=p["is_admin"], password_hash=hash_password(password),
            must_change_password=True, active=True, created_at=now,
        ))
        out.append((p["username"], password))
    db.flush()
    # point demo shifts and claimed callbacks at the new accounts
    ids = {u.display_name: u.id for u in db.scalars(select(User)).all()}
    for s in db.scalars(select(Shift)).all():
        if s.user_id is None:
            s.user_id = ids.get(s.person_label)
    for c in db.scalars(select(Callback)).all():
        if c.owner_id is None and c.owner_name:
            c.owner_id = ids.get(c.owner_name)
    return out
