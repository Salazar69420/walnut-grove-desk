"""Booking by text. The code finds open times from the clinic's published bookable hours minus the Calendar; a model never
writes a time. The person picks 1, 2 or 3 (or names a time); that creates a REQUESTED appointment and a task for the team,
who confirm it (and enter it in ImproMed). Nothing here confirms an appointment by itself.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aftercare import templates as T
from app.models import CalendarEvent, Contact, Pet, SmsOffer, Task, VisitType
from app.services import crm, settings_store, timeutil
from app.services import events as ev

KEY = "booking_hours"
OFFER_HOURS = 3
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
             "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}
BOOK_RX = re.compile(r"\b(book|books|booking|bookings|appointments?|appts?|schedule|come in|bring (him|her|them|my \w+) in|see (the|a) (vet|doctor)|check-?ups?|"
                     r"availability|availabilities|available|opening|openings|slots?|spots?|free times?|any time|next week|vaccine appointment|"
                     r"when (are|can) (you|we|i)|are you free|fit (us|me|him|her|them) in)\b", re.I)
VISIT_RULES = [
    (re.compile(r"\b(vaccin\w*|shots?|booster\w*|rabies)\b", re.I), "Vaccines"),
    (re.compile(r"\b(sick|not eating|vomit\w*|throwing up|diarrh\w*|limp\w*|itch\w*|ear|eye|lump|cough\w*|sneez\w*|rash)\b", re.I), "Sick visit"),
    (re.compile(r"\b(dental|teeth|tooth)\b", re.I), "Dental consult"),
    (re.compile(r"\b(spay|neuter|surgery|operation)\b", re.I), "Surgery drop-off"),
    (re.compile(r"\b(check-?up|annual|yearly|exam|physical|wellness|new (puppy|kitten|patient))\b", re.I), "Yearly check-up"),
]
OTHER_RX = re.compile(r"\b(other|another|different|none|neither|no good|don'?t work|doesn'?t work|later|earlier)\b", re.I)


def defaults() -> dict[str, Any]:
    # The clinic's hours as the owner gave them: Mon to Fri 8:30 am to 7 pm, Sat and Sun 9 am to 5 pm.
    weekday, weekend = [["08:30", "19:00"]], [["09:00", "17:00"]]
    return {"enabled": True, "slot_minutes": 30, "lead_minutes": 120, "horizon_days": 14, "closed_dates": [],
            "days": {"mon": weekday, "tue": weekday, "wed": weekday, "thu": weekday, "fri": weekday, "sat": weekend, "sun": weekend}}


def get_hours(db: Session) -> dict[str, Any]:
    cfg = defaults()
    stored = settings_store.get_raw(db, KEY, None)
    if isinstance(stored, dict):
        cfg.update({k: v for k, v in stored.items() if k in cfg})
    return cfg


def _hm(s: str) -> time:
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(s).strip())
    if not m:
        raise crm.CrmError(400, f"Times look like 09:00 or 17:30, not {str(s)[:10]}.")
    return time(int(m.group(1)), int(m.group(2)))


def put_hours(db: Session, body: dict[str, Any], by: str) -> dict[str, Any]:
    cfg = get_hours(db)
    for k in ("enabled",):
        if k in body:
            cfg[k] = bool(body[k])
    for k, lo, hi in (("slot_minutes", 10, 120), ("lead_minutes", 0, 2880), ("horizon_days", 1, 60)):
        if k in body:
            v = int(body[k])
            if not lo <= v <= hi:
                raise crm.CrmError(400, f"{k.replace('_', ' ')} must be between {lo} and {hi}.")
            cfg[k] = v
    if "days" in body:
        days = {}
        for d in DAYS:
            ranges = body["days"].get(d) or []
            clean = []
            for r in ranges[:3]:
                a, b = _hm(r[0]), _hm(r[1])
                if b <= a:
                    raise crm.CrmError(400, "Each range must end after it starts.")
                clean.append([a.strftime("%H:%M"), b.strftime("%H:%M")])
            days[d] = clean
        cfg["days"] = days
    if "closed_dates" in body:
        cfg["closed_dates"] = sorted({date.fromisoformat(x).isoformat() for x in body["closed_dates"][:100]})
    settings_store.put_raw(db, KEY, cfg, by=by)
    return cfg


KIND_FOR_VISIT = {"Vaccines": "vaccine", "Dental consult": "dental", "Surgery drop-off": "surgery", "Sick visit": "consult"}


def availability(db: Session, minutes: int = 30, n: int = 6) -> list[datetime]:
    """The next open times the assistant may tell a client about (empty when booking by text is off)."""
    return sorted(spread(free_times(db, minutes, n=80), k=n, gap=timedelta(hours=3))) if get_hours(db)["enabled"] else []


def remember_offer(db: Session, contact: Contact, times: list[datetime], visit_type_id: int | None = None) -> None:
    """Times the assistant just told a client about, so "2" or "the 10 am" in their next text books that time."""
    for old in db.scalars(select(SmsOffer).where(SmsOffer.contact_id == contact.id, SmsOffer.status == "offered")).all():
        old.status = "expired"
    db.add(SmsOffer(contact_id=contact.id, visit_type_id=visit_type_id, slots_json=json.dumps([timeutil.iso(x) for x in times]), status="offered",
                    created_at=timeutil.now_utc()))
    db.flush()


def upcoming(db: Session, contact: Contact, n: int = 3) -> list[CalendarEvent]:
    return list(db.scalars(select(CalendarEvent).where(CalendarEvent.contact_id == contact.id, CalendarEvent.status.in_(("booked", "requested")),
                                                       CalendarEvent.starts_at >= timeutil.now_utc()).order_by(CalendarEvent.starts_at).limit(n)).all())


def label(dt: datetime) -> str:
    loc = timeutil.local(dt)
    h = loc.strftime("%I:%M %p").lstrip("0").lower().replace(":00 ", " ")
    return f"{loc.strftime('%a %b')} {loc.day}, {h}"


def _busy(db: Session, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    rows = db.scalars(select(CalendarEvent).where(CalendarEvent.status.in_(("booked", "requested")), CalendarEvent.starts_at < end,
                                                  CalendarEvent.ends_at > start)).all()
    return [(crm.aware(e.starts_at), crm.aware(e.ends_at)) for e in rows]


def free_times(db: Session, minutes: int, *, n: int = 3, after: datetime | None = None, weekday: int | None = None, day: date | None = None,
               part: str | None = None, staff: bool = False) -> list[datetime]:
    """Open times. staff=True is a person booking in the app: no lead time and no horizon, only the past and the Calendar block a time."""
    cfg = get_hours(db)
    now = timeutil.now_utc()
    earliest = max(now + timedelta(minutes=0 if staff else cfg["lead_minutes"]), after or now)
    today = timeutil.today_local(now)
    horizon = max(day, today) if (staff and day) else today + timedelta(days=cfg["horizon_days"])
    if day and day > horizon:
        return []
    busy = _busy(db, crm.local_day_start(today), crm.local_day_start(horizon + timedelta(days=1)))
    step = cfg["slot_minutes"]
    out: list[datetime] = []
    d = today
    while d <= horizon and len(out) < n:
        if (weekday is None or d.weekday() == weekday) and (day is None or d == day) and d.isoformat() not in cfg["closed_dates"]:
            for a, b in cfg["days"].get(DAYS[d.weekday()], []):
                t, end = _hm(a), _hm(b)
                cur = datetime.combine(d, t)
                stop = datetime.combine(d, end)
                while cur + timedelta(minutes=minutes) <= stop and len(out) < n:
                    s = timeutil.at_local(d, cur.hour, cur.minute)
                    e = s + timedelta(minutes=minutes)
                    hour = cur.hour
                    ok_part = part is None or (part == "morning" and hour < 12) or (part == "afternoon" and 12 <= hour < 17) or (part == "evening" and hour >= 16)
                    if s >= earliest and ok_part and not any(bs < e and be > s for bs, be in busy):
                        out.append(s)
                    cur += timedelta(minutes=step)
        d += timedelta(days=1)
    return out


def _prefs(text: str) -> dict[str, Any]:
    t = text.lower()
    p: dict[str, Any] = {}
    today = timeutil.today_local()
    if re.search(r"\btoday\b", t):
        p["day"] = today
    elif re.search(r"\btomorrow\b", t):
        p["day"] = today + timedelta(days=1)
    else:
        for name, wd in DAY_NAMES.items():
            if re.search(rf"\b{name}\b", t):
                p["weekday"] = wd
                break
    for part in ("morning", "afternoon", "evening"):
        if part in t:
            p["part"] = part
    return p


def _visit(db: Session, text: str) -> VisitType | None:
    name = next((n for rx, n in VISIT_RULES if rx.search(text)), "Yearly check-up")
    return db.scalar(select(VisitType).where(VisitType.name == name))


def _active_offer(db: Session, contact: Contact) -> SmsOffer | None:
    since = timeutil.now_utc() - timedelta(hours=OFFER_HOURS)
    return db.scalar(select(SmsOffer).where(SmsOffer.contact_id == contact.id, SmsOffer.status == "offered", SmsOffer.created_at > since)
                     .order_by(SmsOffer.id.desc()).limit(1))


def _choice(text: str, slots: list[datetime]) -> datetime | None:
    t = text.strip().lower()
    m = re.fullmatch(r"(?:option\s*|number\s*|#\s*)?([1-9])[.!)]?(?:\s*(?:please|pls|thanks|thank you|works|is good|sounds good))?[.!]?", t)
    if m and int(m.group(1)) <= len(slots):
        return slots[int(m.group(1)) - 1]
    words = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2}
    for w, i in words.items():
        if re.search(rf"\b(the )?{w}( one| option)?\b", t) and i < len(slots):
            return slots[i]
    tm = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", t)
    if tm:
        h, mi = int(tm.group(1)), int(tm.group(2) or 0)
        ap = tm.group(3)
        hits = []
        for s in slots:
            loc = timeutil.local(s)
            hh = loc.hour if ap is None else (loc.hour % 12 or 12)
            if (h == hh or (ap is None and h == loc.hour % 12)) and mi == loc.minute and (ap is None or (ap == "pm") == (loc.hour >= 12)):
                hits.append(s)
        if len(hits) == 1:
            return hits[0]
    return None


def _pet(db: Session, contact: Contact) -> str | None:
    return db.scalar(select(Pet.name).where(Pet.contact_id == contact.id, Pet.status == "active").order_by(Pet.id).limit(1))


def _task(db: Session, contact: Contact, title: str, reason: str) -> Task:
    t = Task(kind="booking", classification=contact.kind, title=title[:200], reason=reason[:2000], contact_id=contact.id, priority="normal",
             status="open", raised_by="ai", created_at=timeutil.now_utc())
    db.add(t)
    db.flush()
    ev.record(db, ev.TASKS, "task_raised_by_assistant", f"The assistant raised a task: {title[:80]}", "AI assistant", "task", t.id, {"kind": "booking"})
    return t


def handle(db: Session, contact: Contact, text: str, *, act: bool = True) -> dict[str, Any] | None:
    """A reply when the message is about booking, else None (the normal assistant answers). act=False never creates an appointment."""
    cfg = get_hours(db)
    first = (T.first_name(contact.display_name) or "there")
    offer = _active_offer(db, contact)
    if offer is not None:
        slots = [crm.aware(datetime.fromisoformat(x)) for x in json.loads(offer.slots_json or "[]")]
        vt = db.get(VisitType, offer.visit_type_id) if offer.visit_type_id else None
        pick = _choice(text, slots)
        if pick is not None:
            if not act:
                return {"kind": "booking", "draft": str(T.booking_requested(first, label(pick), _pet(db, contact))), "sources": [], "task": None,
                        "note": "They picked a time. Sending this text does not book it: add it in the Calendar (or let the automatic replies do it)."}
            if _busy(db, pick, pick + timedelta(minutes=vt.minutes if vt else 30)):
                return _offer(db, contact, vt, first, after=pick, prefs={}, note="That time was just taken, so new times were offered.")
            minutes = vt.minutes if vt else 30
            pet = db.scalar(select(Pet).where(Pet.contact_id == contact.id, Pet.status == "active").order_by(Pet.id).limit(1))
            visit = vt.name if vt else "Appointment"
            e = CalendarEvent(kind=KIND_FOR_VISIT.get(visit, "checkup"), classification=contact.kind, title=f"{visit}: {contact.display_name} (asked by text)",
                              contact_id=contact.id, pet_id=pet.id if pet else None, starts_at=pick, ends_at=pick + timedelta(minutes=minutes), status="requested", source="app")
            db.add(e)
            db.flush()
            offer.status, offer.event_id = "requested", e.id
            t = _task(db, contact, f"Confirm text booking: {contact.display_name}, {label(pick)}",
                      f"{visit} requested by text for {label(pick)}. Confirm it in the Calendar (it can text the client) and enter it in ImproMed.")
            ev.record(db, ev.CONTACTS, "booking_requested_by_text", "An appointment was requested by text", "AI assistant", "calendar_event", e.id)
            return {"kind": "booking", "draft": str(T.booking_requested(first, label(pick), pet.name if pet else None)), "sources": [],
                    "task": {"id": t.id, "title": t.title}, "event_id": e.id, "visit": visit,
                    "note": "A requested appointment was added to the Calendar and a task was raised to confirm it."}
        prefs = _prefs(text)
        if OTHER_RX.search(text) or prefs:
            return _offer(db, contact, vt, first, after=max(slots) if slots and not prefs else None, prefs=prefs, act=act)
    if not BOOK_RX.search(text):
        return None
    if not cfg["enabled"]:
        return None  # booking by text is off: the normal assistant answers (it never promises times)
    vt = _visit(db, text)
    if vt is None or not vt.bookable:
        visit = vt.name if vt else "visit"
        t = _task(db, contact, f"Book by phone: {contact.display_name} ({visit})", f"Asked by text to book a {visit.lower()}, which is not bookable by text.") if act else None
        return {"kind": "booking", "draft": str(T.booking_by_team(first, visit)), "sources": [], "task": {"id": t.id, "title": t.title} if t else None,
                "note": "This visit type is not bookable by text, so the team books it."}
    return _offer(db, contact, vt, first, after=None, prefs=_prefs(text), act=act, text=text)


def spread(times: list[datetime], k: int = 3, gap: timedelta = timedelta(hours=2)) -> list[datetime]:
    """Three choices that differ (the earliest, then times at least two hours apart), not 9, 9:30 and 10."""
    out: list[datetime] = []
    for t in times:
        if all(abs(t - x) >= gap for x in out):
            out.append(t)
        if len(out) == k:
            return out
    for t in times:
        if t not in out and len(out) < k:
            out.append(t)
    return sorted(out)


def _offer(db: Session, contact: Contact, vt: VisitType | None, first: str, *, after: datetime | None, prefs: dict[str, Any], act: bool = True, text: str = "",
           note: str = "Open times were offered from the bookable hours and the Calendar.") -> dict[str, Any]:
    minutes = vt.minutes if vt else 30
    visit = vt.name if vt else "Appointment"
    times = spread(free_times(db, minutes, n=40, after=after + timedelta(minutes=1) if after else None, **prefs))
    if not times and prefs:
        times = spread(free_times(db, minutes, n=40, after=after + timedelta(minutes=1) if after else None))
    for old in db.scalars(select(SmsOffer).where(SmsOffer.contact_id == contact.id, SmsOffer.status == "offered")).all():
        old.status = "expired"
    if not times:
        t = _task(db, contact, f"Book by phone: {contact.display_name} ({visit})", "No open time was found online.") if act else None
        return {"kind": "booking", "draft": str(T.booking_none(first, visit)), "sources": [], "task": {"id": t.id, "title": t.title} if t else None,
                "note": "No open time was found in the bookable hours."}
    db.add(SmsOffer(contact_id=contact.id, visit_type_id=vt.id if vt else None, slots_json=json.dumps([timeutil.iso(x) for x in times]), status="offered",
                    created_at=timeutil.now_utc()))
    db.flush()
    named = visit if vt is not None and any(n == visit for rx, n in VISIT_RULES if rx.search(text)) else None
    return {"kind": "booking", "draft": str(T.booking_offer(first, named, [label(x) for x in times])), "sources": [], "task": None, "note": note}
