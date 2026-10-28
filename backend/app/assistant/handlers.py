"""One handler per chip key. Numbers and names come from queries, never from the text of this file."""
from __future__ import annotations

import json
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Callback, Conversation, Draft, LabResult, PimsPatient, Shift, Suggestion, User
from app.seed import reference_data as ref
from app.services import events as ev
from app.services import metrics, settings_store, timeutil

Answer = dict[str, Any]


def ans(paragraphs=None, bullets=None, after=None, link=None, source="record") -> Answer:
    return {"paragraphs": paragraphs or [], "bullets": bullets or [], "after": after or [], "link": link, "source": source}


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _num_word(n: int) -> str:
    words = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
    return words[n] if 0 <= n < len(words) else str(n)


def _day(n: int) -> str:
    return f"{n} {_plural(n, 'day', 'days')}"


def _conv_msgs(c: Conversation) -> list[dict]:
    try:
        return json.loads(c.messages_json or "[]")
    except ValueError:
        return []


def night(db: Session, user: User) -> Answer:
    rows = db.scalars(select(Conversation).order_by(Conversation.id)).all()
    c = metrics.overnight_counts(db)
    bk = ", ".join(f"{r.client_label} for {r.pet_label}" for r in rows if r.kind == "booking")
    bullets = [f"{c['booking']} appointment {_plural(c['booking'], 'request', 'requests')}" + (f" ({bk})" if bk else ""),
               f"{c['callback']} added to callbacks",
               f"{c['emergency']} sent to an emergency hospital"]
    return ans([f"Since 6 pm the after-hours assistant handled {c['total']} {_plural(c['total'], 'conversation', 'conversations')}:"],
               bullets, [f"{c['pending']} still need someone to look at them."] if c["pending"] != 1 else ["1 still needs someone to look at it."],
               {"label": "Open overnight", "view": "overnight"})


def emerg(db: Session, user: User) -> Answer:
    rows = db.scalars(select(Conversation).where(Conversation.kind == "emergency").order_by(Conversation.id)).all()
    now = timeutil.now_utc()
    if not rows:
        return ans(["No conversations were sent to an emergency hospital overnight."], link={"label": "Open overnight", "view": "overnight"})
    bullets = [
        f"{r.client_label}, for {r.pet_label}, {timeutil.clock_text(r.created_at)}. {r.summary}" +
        (" Nobody has followed up yet." if r.status == "new" else " Followed up.")
        for r in rows
    ]
    who = "the client" if len(rows) == 1 else ("both clients" if len(rows) == 2 else "all of these clients")
    return ans([f"{len(rows)} {_plural(len(rows), 'conversation was', 'conversations were')} sent to an emergency hospital overnight:"],
               bullets, [f"The clinic should check in with {who} this morning."], {"label": "Open overnight", "view": "overnight"})


def _open_callbacks(db: Session) -> list[Callback]:
    rows = db.scalars(select(Callback).where(Callback.status != "done")).all()
    rows.sort(key=lambda c: (c.priority != "high", not c.red_flag, -c.received_at.timestamp()))
    return rows


def first(db: Session, user: User) -> Answer:
    rows = _open_callbacks(db)
    hi = [c for c in rows if c.priority == "high"]
    old = metrics.results_waiting(db)
    flag = next((c for c in rows if c.red_flag), None)
    b1 = f"{len(rows)} {_plural(len(rows), 'callback is', 'callbacks are')} open and {len(hi)} {_plural(len(hi), 'is', 'are')} high priority."
    if flag:
        b1 += f" One, {flag.client_label} for {flag.pet_label} ({flag.what.rstrip('.')}), may be urgent."
    bullets = [b1, f"{len(old)} lab {_plural(len(old), 'result has', 'results have')} waited {metrics.rules(db)['results_age_days']} days or more without a client call."]
    ch = metrics.chart(db, None)
    i = max(range(len(ch["calls"])), key=lambda k: ch["per_person"][k])
    hr, cv = ch["hours"][i], ch["cover"][i]
    h12 = hr[:-1] + (" am" if hr.endswith("a") else " pm")
    who = "only one person is" if cv == 1 else (f"{cv} people are" if cv > 1 else "nobody is")
    bullets.append(f"At {h12} {who} on the phones, and about {round(ch['calls'][i])} calls arrive in that hour ({ch['calls_label'].lower()}).")
    n_open = len(db.scalars(select(Suggestion).where(Suggestion.status == "open")).all())
    return ans([f"{_num_word(len(bullets)).capitalize()} things for this morning:"], bullets,
               [f"I put {n_open} {_plural(n_open, 'suggestion', 'suggestions')} in your brief for approval."], {"label": "Open brief", "view": "brief"})


def missed(db: Session, user: User) -> Answer:
    last, prior = metrics.missed_last_week(db), metrics.missed_prior_week(db)
    delta = last["value"] - prior["value"]
    cmp = f"{abs(delta)} {'more' if delta > 0 else 'fewer'} than the week before" if delta else "the same as the week before"
    p1 = f"{last['value']} calls went unanswered last week, {cmp}."
    if last.get("again") is not None:
        p1 += f" {last['again']} of those callers rang again within the hour. {last['never']} never did."
    if last["demo"]:
        p1 += " These are example numbers."
    paras = [p1]
    gaps = metrics.missed_gaps(db)
    if gaps and last["demo"]:
        paras.append(f"The gaps were {gaps}. A text to missed callers would reach the {last['never']} who gave up.")
    return ans(paras, link={"label": "See the suggestion", "view": "brief"})


def recall(db: Session, user: User) -> Answer:
    r = metrics.recall_overdue(db)
    p1 = f"{r['value']} clients are overdue for an annual exam."
    if r["demo"]:
        p1 += " This is an example number."
    else:
        rows = db.scalars(select(PimsPatient.species)).all()
        if rows:
            top = max(set(rows), key=rows.count)
            if top:
                p1 += f" Most patients on file are {top.lower()}s."
    batch = metrics.rules(db)["recall_batch"]
    return ans([p1, f"If you approve the recall batch I draft {batch} texts at a time for the front desk to approve, so nobody is flooded with replies."],
               link={"label": "Open brief", "view": "brief"})


def _t12(m: int) -> str:
    return f"{m // 60 % 12 or 12}:{m % 60:02d}"


def saturday(db: Session, user: User) -> Answer:
    rows = db.scalars(select(Shift).where(Shift.weekday == 5, Shift.role.in_(("desk", "open"))).order_by(Shift.start_min)).all()
    if not rows:
        return ans(["Nobody is on the desk on Saturday."], ["Suggestion: add a desk shift on Saturday morning."], link={"label": "Open schedule", "view": "schedule"})
    people = ", ".join(f"{s.person_label} from {_t12(s.start_min)} to {_t12(s.end_min)}" for s in rows)
    at9 = sum(1 for s in rows if s.start_min <= 540 < s.end_min)
    p = f"Saturday has {_num_word(len(rows))} {_plural(len(rows), 'person', 'people')} at the desk, {people}."
    if at9 == 1:
        p += " Only one person is on the phones at 9 am."
    paras = [p]
    if len(rows) < 2:
        paras.append(f"Suggestion: move one weekday shift to Saturday morning, or turn on missed-call texts so callers are not lost while {rows[0].person_label} is busy.")
    return ans(paras, link={"label": "Open schedule", "view": "schedule"})


def week(db: Session, user: User) -> Answer:
    w = metrics.week_compare(db)
    d = w["missed_delta"]
    mv = "up" if d > 0 else ("down" if d < 0 else "flat")
    p1 = f"Compared with last week: missed calls are {mv}{' ' + str(abs(d)) if d else ''}"
    p1 += f", and recall bookings are flat at {w['recall_bookings_last']} (example number)." if w["recall_bookings_last"] == w["recall_bookings_prior"] else "."
    waiting = len(metrics.results_waiting(db))
    p1 += f" {waiting} lab {_plural(waiting, 'result is', 'results are')} waiting {metrics.rules(db)['results_age_days']} days or more right now."
    paras = [p1]
    gaps = metrics.missed_gaps(db)
    if gaps and d > 0:
        paras.append(f"Most of last week's missed calls were {gaps}.")
    return ans(paras, link={"label": "Open brief", "view": "brief"})


def urgent(db: Session, user: User) -> Answer:
    rows = _open_callbacks(db)[:5]
    if not rows:
        return ans(["Your queue is empty."])
    bullets = []
    for c in rows:
        who = c.client_label + (f", for {c.pet_label}" if c.pet_label else "")
        t = f"{who}. {c.what.rstrip('.')}."
        if c.red_flag:
            t += " This could be serious, so call first and tell a nurse."
        bullets.append(t)
    return ans(["In your queue, in this order:"], bullets, link={"label": "Open callbacks", "view": "callbacks"})


def _sheet(db: Session) -> dict[str, Any]:
    s = settings_store.get(db, "answer_sheet", None)
    return s if isinstance(s, dict) else ref.ANSWER_SHEET


def records(db: Session, user: User) -> Answer:
    r = _sheet(db).get("records", {})
    return ans([r.get("intro", "")] if r.get("intro") else [], list(r.get("steps", [])),
               [r["footer"]] if r.get("footer") else [], source="answer_sheet")


def hours(db: Session, user: User) -> Answer:
    h = _sheet(db).get("hours", {})
    return ans(list(h.get("paragraphs", [])), source="answer_sheet")


def pickup(db: Session, user: User) -> Answer:
    pending = db.scalars(select(Draft).where(Draft.status == "pending", Draft.kind == "Ready for pickup", Draft.to_label.like("%Pepper%"))).first()
    if pending is None:
        d = next(x for x in ref.DRAFTS if x["kind"] == "Ready for pickup")
        pending = Draft(kind=d["kind"], to_label=d["to"], body=d["body"], channel="text", status="pending",
                        created_by_tool="Recall sender", created_at=timeutil.now_utc())
        db.add(pending)
        db.flush()
        ev.record(db, ev.RECALL_SENDER, "draft_created", "Draft ready for Pepper pickup message", user, "draft", pending.id)
    return ans(["I wrote one for Pepper's food order. It is in your Messages to approve list."], link={"label": "Open callbacks", "view": "callbacks"})


def waiting(db: Session, user: User) -> Answer:
    now = timeutil.now_utc()
    rows = [r for r in db.scalars(select(LabResult).where(LabResult.stage < 2)).all()]
    rows.sort(key=lambda r: (-timeutil.age_days(r.received_at, now), r.id))
    if not rows:
        return ans(["No results are waiting."], link={"label": "Open results", "view": "results"})
    bullets = [f"{r.pet_label}, {r.test}, {_day(timeutil.age_days(r.received_at, now))}" +
               (", waiting for doctor" if r.stage == 0 else ", waiting for a client call") for r in rows]
    return ans(["Longest waiting:"], bullets, link={"label": "Open results", "view": "results"})


def script(db: Session, user: User) -> Answer:
    r = db.scalars(select(LabResult).where(LabResult.pet_label.like("Simba%")).order_by(LabResult.id.desc())).first()
    clinic = settings_store.get(db, "clinic_name")
    if r is None:
        return ans(["I could not find a waiting result for Simba."], link={"label": "Open results", "view": "results"})
    pet = r.pet_label.split(" (")[0]
    quote = (f"\"Hi, it is {user.display_name} at {clinic}. I am calling for the {r.owner_label} family about {pet}'s {r.test.lower()}. "
             "Dr. Garg has looked at it and [what Dr. Garg wants said]. Do you have a few minutes, or shall I book a call with him?\"")
    return ans([f"Suggested call for {pet}'s {r.test.lower()}, after Dr. Garg has reviewed it:", quote],
               after=["I will not give a diagnosis. The doctor decides what is said."])


def flags(db: Session, user: User) -> Answer:
    rows = [c for c in _open_callbacks(db) if c.red_flag]
    if not rows:
        return ans(["No open callbacks are marked as a possible red flag."], link={"label": "Open callbacks", "view": "callbacks"})
    bullets = [f"{c.client_label}, for {c.pet_label}. {c.what.rstrip('.')}." if c.pet_label else f"{c.client_label}. {c.what.rstrip('.')}." for c in rows]
    return ans([f"{len(rows)} open {_plural(len(rows), 'callback is', 'callbacks are')} marked as a possible red flag:"], bullets,
               ["These should be seen by a nurse or the doctor now."], {"label": "Open callbacks", "view": "callbacks"})


HANDLERS: dict[str, Callable[[Session, User], Answer]] = {
    "night": night, "emerg": emerg, "first": first, "missed": missed, "recall": recall, "saturday": saturday,
    "week": week, "urgent": urgent, "records": records, "hours": hours, "pickup": pickup, "waiting": waiting,
    "script": script, "flags": flags,
}

GUESS = [("overnight", "night"), ("emergenc", "emerg"), ("miss", "missed"), ("recall", "recall"), ("saturday", "saturday"),
         ("hours", "hours"), ("week", "week"), ("record", "records"), ("pickup", "pickup"), ("script", "script"),
         ("urgent", "urgent"), ("result", "waiting"), ("first", "first"), ("today", "first")]


def guess_all(q: str) -> list[str]:
    q = q.lower()
    out: list[str] = []
    for needle, key in GUESS:
        if needle in q and key not in out:
            out.append(key)
    return out


def guess(q: str) -> str | None:
    found = guess_all(q)
    return found[0] if found else None
