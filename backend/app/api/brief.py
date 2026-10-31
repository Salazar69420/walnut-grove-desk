"""The owner's Brief, and approving or dismissing suggestions."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.adapters import llm
from app.api.overnight import summary as overnight_summary
from app.db import get_db
from app.models import Draft, LabResult, Shift, Suggestion, User
from app.rules.engine import run_rules
from app.security.auth import require
from app.seed import reference_data as ref
from app.services import events as ev
from app.services import metrics, numbercheck, settings_store, timeutil

router = APIRouter(prefix="/api", tags=["brief"])

BRIEF_CACHE_KEY = "brief_cache"


def suggestion_dict(s: Suggestion) -> dict[str, Any]:
    try:
        evidence = json.loads(s.evidence_json or "{}")
    except ValueError:
        evidence = {}
    return {"id": s.id, "rule_key": s.rule_key, "title": s.title, "why": s.why, "status": s.status, "evidence": evidence,
            "decided_by": s.decided_by, "decided_at": timeutil.iso(s.decided_at)}


def greeting(user: User, now) -> str:
    h = timeutil.local(now).hour
    part = "morning" if h < 12 else ("afternoon" if h < 17 else "evening")
    return f"Good {part}, {user.display_name}"


def date_line(now) -> str:
    d = timeutil.local(now)
    return f"{d.strftime('%A, %B')} {d.day}. Here is what the front desk and nurses logged since yesterday."


def kpis(db: Session, now) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cb = metrics.open_callbacks(db)
    waiting = metrics.results_waiting(db, now)
    missed = metrics.missed_last_week(db, now)
    ov = overnight_summary(db)
    origin = metrics.data_origins(db)
    items = [
        {"key": "callbacks_open", "value": cb["open"], "label": "Callbacks still open", "alert": cb["high"] > 0,
         "origin": origin["callbacks_open"]},
        {"key": "results_waiting", "value": len(waiting), "label": "Results waiting 2+ days", "alert": len(waiting) > 0,
         "origin": origin["results_waiting"]},
        {"key": "missed_last_week", "value": missed["value"],
         "label": ("Missed calls last 7 days" if missed.get("live") else "Missed calls last week") + (" (example)" if missed["demo"] else ""),
         "alert": False, "origin": "example" if missed["demo"] else "live"},
        {"key": "overnight_pending", "value": ov["pending"], "label": "Overnight items to review", "alert": ov["pending"] > 0,
         "origin": origin["overnight_pending"]},
    ]
    facts = {"open": cb["open"], "high": cb["high"], "waiting": len(waiting), "missed": missed["value"],
             "pending": ov["pending"], "overnight_total": ov["total"], "emergency_pending": ov["emergency_pending"],
             "age_days": metrics.rules(db)["results_age_days"]}
    if missed.get("live"):
        lv = metrics.Live(db)
        prev = lv.missed("prev7d")
        calls = lv.calls("7d")
        facts.update({
            "live_origin": "live", "days": 7, "hours": 24,
            "live_delta": None if prev is None else abs(missed["value"] - prev),
            "live_delta_sign": None if prev is None else (missed["value"] > prev) - (missed["value"] < prev),
            "live_no_contact": missed.get("never"), "live_calls": calls["all"] if calls else None,
        })
    return items, facts


def template_paragraph(f: dict[str, Any]) -> str:
    def n(v: int, one: str, many: str) -> str:
        return one if v == 1 else many

    if f.get("live_origin") == "live":
        # Real numbers from the phone system (docs/CONTRACT_LIVE_BRIEF.md).
        missed = f"{f['missed']} inbound {n(f['missed'], 'call', 'calls')} went unanswered in the last 7 days"
        sign, d = f.get("live_delta_sign"), f.get("live_delta")
        if sign is not None:
            missed += ", " + ("the same as the week before" if sign == 0 else f"{d} {'more' if sign > 0 else 'fewer'} than the week before")
        nc = f.get("live_no_contact")
        if nc is not None:
            missed += f", and {nc} of those {n(nc, 'caller', 'callers')} had no contact within 24 hours"
        missed += "."
        extra = [f"The phones took {f['live_calls']} {n(f['live_calls'], 'call', 'calls')} in the last 7 days."] if f.get("live_calls") is not None else []
    else:
        missed, extra = f"{f['missed']} {n(f['missed'], 'call', 'calls')} went unanswered last week.", []
    parts = [
        f"{f['open']} {n(f['open'], 'callback is', 'callbacks are')} open and {f['high']} {n(f['high'], 'is', 'are')} high priority.",
        f"{f['waiting']} lab {n(f['waiting'], 'result has', 'results have')} waited {f['age_days']} days or more.",
        missed, *extra,
        f"{f['pending']} overnight {n(f['pending'], 'item still needs', 'items still need')} someone to look at {n(f['pending'], 'it', 'them')}.",
    ]
    return " ".join(parts)


def brief_text(db: Session, facts: dict[str, Any]) -> dict[str, str]:
    base = template_paragraph(facts)
    cfg = llm.get_config(db)
    if not cfg.get("word_brief"):
        return {"source": "template", "paragraph": base}
    key = hashlib.sha256(base.encode()).hexdigest()
    cache = settings_store.get_raw(db, BRIEF_CACHE_KEY, None)
    if isinstance(cache, dict) and cache.get("key") == key:
        return {"source": cache["source"], "paragraph": cache["paragraph"]}
    client = llm.LlmClient.from_settings(db)
    if client is None:
        return {"source": "template", "paragraph": base}
    allowed = [v for v in facts.values() if isinstance(v, (int, float))]
    text = None
    try:
        text = client.chat(
            "You rewrite a short clinic status paragraph in plain, warm words for the clinic owner. Keep it to 3 sentences. "
            "Use only the facts given. Do not add, change or compute any number. Do not mention names.",
            base, max_tokens=250).strip()
    except llm.LlmError:
        text = None
    if text:
        ok, bad = numbercheck.check(text, allowed)
        if ok:
            out = {"source": "model", "paragraph": text}
        else:
            ev.record(db, ev.ADMIN, "brief_wording_rejected", "Brief wording rejected: number not in the record", None,
                      "setting", BRIEF_CACHE_KEY, {"bad_numbers": bad})
            out = {"source": "template", "paragraph": base}
    else:
        out = {"source": "template", "paragraph": base}
    settings_store.put_raw(db, BRIEF_CACHE_KEY, {"key": key, **out}, by="system")
    db.commit()
    return out


@router.get("/brief")
def get_brief(
    chart: Literal["real", "example"] | None = Query(default=None),
    user: User = Depends(require("brief.view")),
    db: Session = Depends(get_db),
):
    now = timeutil.now_utc()
    k, facts = kpis(db, now)
    sugg = db.scalars(select(Suggestion).order_by(Suggestion.id)).all()
    return {
        "greeting": greeting(user, now),
        "date_line": date_line(now),
        "kpis": k,
        "overnight": overnight_summary(db),
        "suggestions": [suggestion_dict(s) for s in sugg],
        "chart": metrics.chart(db, chart),
        "text": brief_text(db, facts),
        "live": metrics.live_brief(db),
    }


# ---------------------------------------------------------------- suggestions
def _get(db: Session, sid: int) -> Suggestion:
    s = db.get(Suggestion, sid)
    if s is None:
        raise HTTPException(status_code=404, detail="No such suggestion")
    return s


def _decide(db: Session, s: Suggestion, new_status: str, user: User) -> None:
    res = db.execute(update(Suggestion).where(Suggestion.id == s.id, Suggestion.status == "open").values(
        status=new_status, decided_by=user.display_name, decided_at=timeutil.now_utc()))
    if res.rowcount == 0:
        db.refresh(s)
        raise HTTPException(status_code=409, detail=f"Already {s.status} by {s.decided_by or 'someone'}")
    db.refresh(s)


def _evidence(s: Suggestion) -> dict[str, Any]:
    try:
        return json.loads(s.evidence_json or "{}")
    except ValueError:
        return {}


def apply_effect(db: Session, s: Suggestion, user: User) -> None:
    name = user.display_name
    if s.rule_key == "desk_shift":
        evd = _evidence(s)
        lo, hi = int(evd.get("start_min", 480)), int(evd.get("end_min", 600))
        for wd in range(5):
            db.add(Shift(weekday=wd, person_label="Open shift", user_id=None, role="open", start_min=lo, end_min=hi,
                         proposed=True, suggestion_id=s.id))
        db.flush()
        ev.record(db, ev.STAFF_ROSTER, "desk_shift_approved",
                  f"{name} approved an open desk shift, {timeutil.hhmm(lo)}-{timeutil.hhmm(hi)}, Monday to Friday",
                  user, "shift", "", {"suggestion_id": s.id})
    elif s.rule_key == "missed_texts":
        settings_store.put(db, "tog_missed", True, by=name)
        ev.record(db, ev.CALLBACK_LIST, "missed_texts_on", f"{name} turned on texts to missed callers", user, "setting", "tog_missed",
                  {"suggestion_id": s.id})
    elif s.rule_key == "recall_batch":
        for d in ref.RECALL_EXTRA_DRAFTS:
            db.add(Draft(kind=d["kind"], to_label=d["to"], body=d["body"], channel="text", status="pending",
                         created_by_tool="Recall sender", suggestion_id=s.id, created_at=timeutil.now_utc()))
        db.flush()
        ev.record(db, ev.RECALL_SENDER, "recall_batch_started",
                  "Recall batch started: first drafts sent to the front desk for approval", user, "draft", "", {"suggestion_id": s.id})
    elif s.rule_key == "old_results":
        age = metrics.rules(db)["results_age_days"]
        now = timeutil.now_utc()
        rows = db.scalars(select(LabResult).where(LabResult.stage == 0)).all()
        for r in rows:
            if timeutil.age_days(r.received_at, now) >= age:
                r.stage = 1
                r.reviewed_by = name
                r.updated_at = now
        ev.record(db, ev.RESULTS_TRACKER, "old_results_reviewed",
                  f"{name} marked older results as reviewed, nurses can call clients", user, "result", "", {"suggestion_id": s.id})
    ev.queue_publish(db, "suggestions", s.id, None)


@router.post("/suggestions/run")
def run(user: User = Depends(require("suggestions.decide")), db: Session = Depends(get_db)):
    created = run_rules(db)
    db.commit()
    return {"created": [suggestion_dict(s) for s in created]}


@router.post("/suggestions/{sid}/approve")
def approve(sid: int, user: User = Depends(require("suggestions.decide")), db: Session = Depends(get_db)):
    s = _get(db, sid)
    _decide(db, s, "approved", user)
    apply_effect(db, s, user)
    db.commit()
    return {"item": suggestion_dict(s)}


@router.post("/suggestions/{sid}/dismiss")
def dismiss(sid: int, user: User = Depends(require("suggestions.decide")), db: Session = Depends(get_db)):
    s = _get(db, sid)
    _decide(db, s, "dismissed", user)
    ev.record(db, ev.ADMIN, "suggestion_dismissed", f"{user.display_name} dismissed a suggestion: {s.title}", user, "suggestion", s.id)
    db.commit()
    return {"item": suggestion_dict(s)}
