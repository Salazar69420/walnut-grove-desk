"""Reports: a catalog and read-only numbers computed from the shared record. Every report has the same shape.

{"key","title","range":{"from","to"},"stats":[{"label","value","hint"}],"sections":[{"title","kind":"table"|"bars","columns","rows"}]}
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CalendarEvent, Campaign, Contact, KnowledgeEntry, Pet, PhoneCall, Task, User, Visit
from app.security.auth import require
from app.services import crm, timeutil
from app.api.phone import RESULT_LABELS

router = APIRouter(prefix="/api/reports", tags=["reports"])

SOURCE_LABELS = {"phone": "Phone call", "walk_in": "Walk-in", "website": "Website", "referral": "Referral", "after_hours": "After hours", "import": "Import", "other": "Other"}
CATALOG = [
    {"key": "agents", "group": "Agents", "title": "Agent reporting", "text": "Calls handled by people and by the assistant, and the tasks each one raised."},
    {"key": "pending_knowledge", "group": "Agents", "title": "Pending knowledge insights", "text": "What the assistant keeps not knowing, and how fast entries are approved."},
    {"key": "conversion", "group": "Clients", "title": "Inquiry to visit", "text": "How many new inquiries became a visit or a booked appointment within 30 days."},
    {"key": "recall", "group": "Clients", "title": "Recall coverage", "text": "Patients with no visit or no vaccine in the last year, by species."},
    {"key": "call_volume", "group": "Phone", "title": "Call volume", "text": "Calls by weekday and hour, to plan who answers the phone."},
    {"key": "campaigns", "group": "Campaigns", "title": "Campaign results", "text": "Who each practice-mode campaign reached and who was left out."},
]


def _range(frm: date | None, to: date | None) -> tuple[date, date]:
    end = to or timeutil.today_local()
    start = frm or end - timedelta(days=29)
    if start > end:
        raise HTTPException(status_code=400, detail="The start date must be before the end date.")
    if (end - start).days > 366:
        raise HTTPException(status_code=400, detail="Pick a range of a year or less.")
    return start, end


def _stat(label: str, value: Any, hint: str = "") -> dict[str, Any]:
    return {"label": label, "value": value, "hint": hint}


def _mmss(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _calls(db: Session, start: date, end: date) -> list[PhoneCall]:
    lo, hi = crm.local_day_start(start), crm.local_day_start(end + timedelta(days=1))
    return list(db.scalars(select(PhoneCall).where(PhoneCall.started_at >= lo, PhoneCall.started_at < hi)).all())


def r_agents(db: Session, start: date, end: date) -> dict[str, Any]:
    calls = _calls(db, start, end)
    lo, hi = crm.local_day_start(start), crm.local_day_start(end + timedelta(days=1))
    tasks = list(db.scalars(select(Task).where(Task.created_at >= lo, Task.created_at < hi)).all())
    rows = []
    for who, label in (("human", "People"), ("ai", "AI assistant")):
        mine = [c for c in calls if c.handled_by == who]
        talk = [c.duration_seconds for c in mine if c.duration_seconds]
        res = Counter(c.result for c in mine)
        rows.append([label, len(mine), _mmss(sum(talk) / len(talk)) if talk else "-", res.get("booked", 0), res.get("callback_task", 0), res.get("emergency_routed", 0)])
    missed = sum(1 for c in calls if c.handled_by == "none" and c.direction == "inbound")
    handled = [c for c in calls if c.handled_by != "none"]
    ai = sum(1 for c in handled if c.handled_by == "ai")
    by_raiser = Counter(t.raised_by for t in tasks)
    done_h = defaultdict(list)
    for t in tasks:
        if t.done_at and t.created_at:
            done_h[t.raised_by].append((crm.aware(t.done_at) - crm.aware(t.created_at)).total_seconds() / 3600)
    esc = sum(1 for t in tasks if t.raised_by == "ai" and t.kind in ("escalation", "unknown_knowledge", "complaint", "emergency_followup"))
    return {
        "stats": [_stat("Calls", len(calls)), _stat("Handled by the assistant", f"{round(100 * ai / len(handled)) if handled else 0}%", f"{ai} of {len(handled)} answered calls"),
                  _stat("Missed inbound", missed), _stat("Escalated by the assistant", esc, "tasks it passed to a person")],
        "sections": [
            {"title": "Calls by who handled them", "kind": "table", "columns": ["Handled by", "Calls", "Average length", "Bookings", "Callback tasks", "Emergencies routed"], "rows": rows},
            {"title": "Tasks raised", "kind": "table", "columns": ["Raised by", "Tasks", "Median hours to finish"],
             "rows": [[lab, by_raiser.get(k, 0), f"{median(done_h[k]):.1f}" if done_h.get(k) else "-"] for k, lab in (("ai", "AI assistant"), ("human", "People"), ("rule", "Rules"))]},
            {"title": "Call results", "kind": "bars", "rows": [[RESULT_LABELS[k], v] for k, v in sorted(Counter(c.result for c in calls).items(), key=lambda kv: -kv[1])]},
        ],
    }


def r_pending(db: Session, start: date, end: date) -> dict[str, Any]:
    lo, hi = crm.local_day_start(start), crm.local_day_start(end + timedelta(days=1))
    now = timeutil.now_utc()
    pend = list(db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.status == "pending")).all())
    decided = list(db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.decided_at >= lo, KnowledgeEntry.decided_at < hi, KnowledgeEntry.status.in_(("approved", "declined", "retired")))).all())
    appr = sum(1 for e in decided if e.status == "approved")
    dec = sum(1 for e in decided if e.status == "declined")
    waits = [(crm.aware(e.decided_at) - crm.aware(e.proposed_at)).total_seconds() / 86400 for e in decided if e.decided_at and e.status in ("approved", "declined")]
    oldest = max(((now - crm.aware(e.proposed_at)).days for e in pend), default=0)
    gaps = db.scalars(select(Task).where(Task.kind == "unknown_knowledge", Task.created_at >= lo, Task.created_at < hi)).all()
    by_cat = Counter(e.category for e in pend)
    from app.services.knowledge import CATEGORY_LABELS
    return {
        "stats": [_stat("Waiting for approval", len(pend)), _stat("Oldest waiting", f"{oldest} days"), _stat("Approval rate", f"{round(100 * appr / (appr + dec))}%" if appr + dec else "-", f"{appr} approved, {dec} declined"),
                  _stat("Median days to decide", f"{median(waits):.1f}" if waits else "-"), _stat("Missing-answer tasks", len(gaps), "times a person was needed for something unknown")],
        "sections": [
            {"title": "Pending by category", "kind": "bars", "rows": [[CATEGORY_LABELS.get(k, k), v] for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])]},
            {"title": "Pending entries that contradict approved knowledge", "kind": "table", "columns": ["Category", "Title", "Waiting (days)"],
             "rows": [[CATEGORY_LABELS.get(e.category, e.category), e.title, (now - crm.aware(e.proposed_at)).days] for e in pend if e.conflicts][:25]},
        ],
    }


def r_conversion(db: Session, start: date, end: date) -> dict[str, Any]:
    lo, hi = crm.local_day_start(start), crm.local_day_start(end + timedelta(days=1))
    cs = list(db.scalars(select(Contact).where(Contact.created_at >= lo, Contact.created_at < hi, Contact.erased_at.is_(None))).all())
    inq = cs
    became = 0
    by_src: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for c in inq:
        created = crm.aware(c.created_at)
        v = db.scalar(select(func.count()).select_from(Visit).where(Visit.contact_id == c.id, Visit.visited_at >= created, Visit.visited_at <= created + timedelta(days=30)))
        e = db.scalar(select(func.count()).select_from(CalendarEvent).where(CalendarEvent.contact_id == c.id, CalendarEvent.status.in_(("booked", "done")),
                                                                            CalendarEvent.kind.in_(("checkup", "vaccine", "surgery", "dental", "consult")), CalendarEvent.starts_at >= created, CalendarEvent.starts_at <= created + timedelta(days=30)))
        won = bool(v or e)
        became += won
        by_src[c.source][0] += 1
        by_src[c.source][1] += won
    return {
        "stats": [_stat("New contacts", len(inq)), _stat("Visit or booking within 30 days", became), _stat("Conversion", f"{round(100 * became / len(inq))}%" if inq else "-")],
        "sections": [{"title": "By where they came from", "kind": "table", "columns": ["Source", "New contacts", "Came in", "Rate"],
                      "rows": [[SOURCE_LABELS.get(k, k), v[0], v[1], f"{round(100 * v[1] / v[0])}%"] for k, v in sorted(by_src.items(), key=lambda kv: -kv[1][0])]}],
    }


def r_recall(db: Session, start: date, end: date) -> dict[str, Any]:
    today = timeutil.today_local()
    cutoff = crm.local_day_start(today - timedelta(days=365))
    patients = list(db.scalars(select(Contact).where(Contact.kind == "patient", Contact.erased_at.is_(None))).all())
    over_visit, over_vax, rows = 0, 0, []
    by_species: Counter = Counter()
    for c in patients:
        last = db.scalar(select(func.max(Visit.visited_at)).where(Visit.contact_id == c.id))
        last_v = db.scalar(select(func.max(Visit.visited_at)).where(Visit.contact_id == c.id, Visit.kind == "vaccine"))
        pets = db.scalars(select(Pet).where(Pet.contact_id == c.id, Pet.status == "active")).all()
        a = last is None or crm.aware(last) < cutoff
        b = last_v is None or crm.aware(last_v) < cutoff
        over_visit += a
        over_vax += b
        if (a or b) and pets:
            for p in pets:
                by_species[p.species.lower()] += 1
            rows.append([c.display_name, ", ".join(p.name for p in pets)[:60], timeutil.local(crm.aware(last)).date().isoformat() if last else "Never", "Overdue" if b else "Up to date"])
    return {
        "stats": [_stat("Patients", len(patients)), _stat("No visit in a year", over_visit), _stat("No vaccine in a year", over_vax)],
        "sections": [
            {"title": "Pets needing recall, by species", "kind": "bars", "rows": [[k.title(), v] for k, v in by_species.most_common()]},
            {"title": "Who to call first", "kind": "table", "columns": ["Client", "Pets", "Last visit", "Vaccines"], "rows": rows[:25]},
        ],
    }


def r_call_volume(db: Session, start: date, end: date) -> dict[str, Any]:
    calls = _calls(db, start, end)
    grid = [[0] * 24 for _ in range(7)]
    for c in calls:
        loc = timeutil.local(c.started_at)
        grid[loc.weekday()][loc.hour] += 1
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    hours = Counter()
    for r in grid:
        for h, v in enumerate(r):
            hours[h] += v
    peak = max(hours.items(), key=lambda kv: kv[1])[0] if calls else None
    after = sum(1 for c in calls if (lambda l: l.hour < 8 or l.hour >= 18 or l.weekday() >= 5)(timeutil.local(c.started_at)))
    return {
        "stats": [_stat("Calls", len(calls)), _stat("Busiest hour", f"{peak}:00" if peak is not None else "-"), _stat("After hours or weekend", f"{round(100 * after / len(calls))}%" if calls else "-")],
        "sections": [
            {"title": "Calls by hour", "kind": "bars", "rows": [[f"{h}:00", hours[h]] for h in range(24) if hours[h]]},
            {"title": "Calls by weekday", "kind": "bars", "rows": [[days[i], sum(grid[i])] for i in range(7)]},
        ],
    }


def r_campaigns(db: Session, start: date, end: date) -> dict[str, Any]:
    lo, hi = crm.local_day_start(start), crm.local_day_start(end + timedelta(days=1))
    rows = db.scalars(select(Campaign).where(Campaign.created_at >= lo, Campaign.created_at < hi).order_by(Campaign.created_at.desc())).all()
    out, sent, matched = [], 0, 0
    for c in rows:
        try:
            k = json.loads(c.counts_json or "{}")
        except ValueError:
            k = {}
        sent += k.get("sent", 0)
        matched += k.get("matched", 0)
        out.append([c.name, c.channel.upper() if c.channel == "sms" else "Email", "Promo" if c.purpose == "promo" else "Care", c.status.replace("_", " "), k.get("matched", "-"), k.get("sent", "-"), sum((k.get("skipped") or {}).values()) if k else "-"])
    return {
        "stats": [_stat("Campaigns", len(rows)), _stat("Messages in practice mode", sent), _stat("Left out", matched - sent, "no consent, unsubscribed, no address or duplicates")],
        "sections": [{"title": "Campaigns", "kind": "table", "columns": ["Name", "Channel", "Kind", "Status", "Matched", "Sent (practice)", "Left out"], "rows": out}],
    }


RUNNERS = {"agents": r_agents, "pending_knowledge": r_pending, "conversion": r_conversion, "recall": r_recall, "call_volume": r_call_volume, "campaigns": r_campaigns}


@router.get("")
def catalog(user: User = Depends(require("reports.view"))):
    return {"items": CATALOG}


@router.get("/{key}")
def run_report(key: str, from_: date | None = Query(default=None, alias="from"), to: date | None = None,
               user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    fn = RUNNERS.get(key)
    if fn is None:
        raise HTTPException(status_code=404, detail="No such report")
    start, end = _range(from_, to)
    meta = next(c for c in CATALOG if c["key"] == key)
    out = fn(db, start, end)
    return {"key": key, "title": meta["title"], "text": meta["text"], "range": {"from": start.isoformat(), "to": end.isoformat()}, **out}
