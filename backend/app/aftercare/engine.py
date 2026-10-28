"""The after-hours conversation engine. Pure Python, deterministic, no I/O.

    start(channel, ctx) -> State
    step(state, inp, ctx) -> (State, [bot_texts], outcome | None)
    view(state, ctx) -> {"node", "options", "done", "accepts_text"}

inp is {"option_index": int} or {"text": str}. All bot text comes from aftercare.templates.
Free text from a client is never echoed, except a pet name that passes a strict check.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.aftercare import emergency as em
from app.aftercare import templates as T

MAX_TEXT = 500

ADVICE_PATTERNS = [
    r"\bhow (much|many)\b", r"\bmgs?\b", r"\bmilligram", r"\bmls?\b", r"\bdos(e|es|ed|age|ing)\b", r"\bunits?\b of\b",
    r"\bcan i (give|feed|use|take|put|try)\b", r"\bshould i (give|feed|worry|wait|be worried|use|try|take)\b",
    r"\bcould i (give|feed|use|try)\b", r"\bmay i (give|feed)\b",
    r"\bis it (safe|ok|okay|normal|fine|dangerous|serious|bad|toxic|poisonous|harmful)\b",
    r"\bis (he|she|it|they|my \w+|the \w+) (ok|okay|fine|alright|safe|going to be|gonna be|dying|sick|in danger)\b",
    r"\bwill (he|she|it|they) (be|die|live|recover|survive)\b",
    r"\bwhat (is|s|are) (wrong|happening|going on)\b", r"\bwhat does (he|she|it) have\b",
    r"\bwhat should i (give|do|feed|use|try)\b", r"\bwhat can i (give|do|feed|use|try)\b", r"\bwhat do i (give|do|feed)\b",
    r"\bwhat (medicine|medication|meds|drug|pill|pills|tablet|tablets|treatment|remedy)\b",
    r"\bwhich (medicine|medication|meds|drug|pill|pills|tablet|tablets)\b",
    r"\b(aspirin|benadryl|advil|motrin|tramadol|gabapentin|prednisone|melatonin|pepto|imodium|ivermectin)\b",
    r"\b(diagnos\w*|prescrib\w*|treat|treating|treatment|remedy|remedies|cure|home care)\b",
    r"\b(tell me|show me|give me|need) (the |a |an )?(dose|dosage|amount|number)\b",
    r"\bwhat (do|should) you (think|recommend|suggest)\b", r"\bany advice\b", r"\badvise\b", r"\brecommend\b",
    r"\b(ignore|forget|disregard|override|bypass)\b.{0,30}\b(rules|instructions|prompt|guidelines|restrictions|policy|policies)\b",
    r"\b(pretend|act as|you are now|roleplay|role play|jailbreak|developer mode|system prompt|dan mode)\b",
    r"\bare you a (vet|doctor|veterinarian)\b", r"\bgive (him|her|it|them) (some|a|an|the)\b",
]
_ADVICE_RX = [re.compile(p, re.I) for p in ADVICE_PATTERNS]

BOOK_WORDS = ["book", "appointment", "check-up", "checkup", "check up", "vaccine", "vaccines", "vaccination", "shots", "shot", "visit", "exam", "schedule"]
REFILL_WORDS = ["refill", "medication", "medicine", "pills", "prescription", "food", "order", "meds", "script"]
CANCEL_WORDS = ["cancel", "reschedule", "resched", "move my appointment", "change my appointment", "change the appointment", "postpone"]
URGENT_WORDS = ["urgent", "emergency", "emergencies", "right now it is bad", "dying"]
UNWELL_WORDS = ["unwell", "sick", "ill", "not well", "off colour", "not himself", "not herself", "not right", "poorly"]

VISIT_MAP = [  # (keywords, visit type name)
    (["check-up", "checkup", "check up", "yearly", "annual", "exam", "wellness"], "Yearly check-up"),
    (["vaccine", "vaccines", "vaccination", "vaccinations", "shot", "shots", "booster"], "Vaccines"),
    (["unwell", "sick", "ill", "not well", "poorly", "not eating", "limping", "itchy", "vomit"], "Sick visit"),
    (["dental", "teeth", "tooth"], "Dental consult"),
    (["surgery", "drop-off", "drop off", "spay", "neuter"], "Surgery drop-off"),
]

_PET_RX = re.compile(r"^\s*([A-Z][a-z]{1,15})\s*(,|$)")


def _has_word(text: str, words: list[str]) -> bool:
    t = " " + em.normalize(text) + " "
    return any(" " + em.normalize(w) + " " in t for w in words)


def is_advice_request(text: str) -> bool:
    return any(rx.search(text or "") or rx.search(em.normalize(text)) for rx in _ADVICE_RX)


@dataclass
class Ctx:
    clinic_name: str = "Walnut Grove Animal Hospital"
    toggles: dict = field(default_factory=lambda: {"req": True, "msg": True, "missed": False})
    visit_types: list[dict] = field(default_factory=list)  # id, name, minutes, bookable
    slots: list[dict] = field(default_factory=list)  # id, label, start (aware datetime), visit_type_id
    hospitals: list[dict] = field(default_factory=list)
    keywords: list[str] = field(default_factory=lambda: list(em.DEFAULT_KEYWORDS))
    layer2: Callable[[str], str] | None = None
    now: datetime | None = None
    opening_line: str | None = None

    def confirmed_hospital(self) -> dict | None:
        for h in self.hospitals:
            if h.get("confirmed") and not h.get("is_placeholder") and (h.get("name") or "").strip():
                return h
        return None

    def future_slots(self, visit_type_id: int | None = None) -> list[dict]:
        now = self.now or datetime.now(timezone.utc)
        out = []
        for s in sorted(self.slots, key=lambda x: x["start"]):
            if s["start"] <= now or s.get("taken"):
                continue
            if s.get("visit_type_id") not in (None, visit_type_id) and visit_type_id is not None:
                continue
            out.append(s)
        return out


@dataclass
class State:
    channel: str = "text"
    node: str = "start"
    picks: dict = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)
    done: bool = False
    outcome: dict | None = None

    def to_dict(self) -> dict:
        return {"channel": self.channel, "node": self.node, "picks": self.picks, "messages": self.messages,
                "done": self.done, "outcome": self.outcome}

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        return cls(channel=d.get("channel", "text"), node=d.get("node", "start"), picks=dict(d.get("picks", {})),
                   messages=list(d.get("messages", [])), done=bool(d.get("done")), outcome=d.get("outcome"))


# ---------------------------------------------------------------- start and options
def start(channel: str, ctx: Ctx) -> State:
    if channel not in ("text", "call"):
        raise ValueError("channel must be text or call")
    st = State(channel=channel)
    st.messages.append({"who": "bot", "text": str(T.opening(ctx.clinic_name, channel, ctx.opening_line))})
    return st


def _offer_slots(ctx: Ctx, vt_id: int | None) -> list[dict]:
    """Up to two openings, the second on a different day when there is one."""
    avail = ctx.future_slots(vt_id)
    if not avail:
        return []
    first = avail[0]
    rest = [s for s in avail[1:] if s["start"].date() != first["start"].date()] or avail[1:]
    out = [first] + rest[:1]
    return [{"id": s["id"], "label": s["label"]} for s in out]


def _options(state: State, ctx: Ctx) -> list[tuple[str, tuple]]:
    n = state.node
    if state.done:
        return []
    if n == "start":
        return [(T.label("book"), ("goto", "book1")), (T.label("refill"), ("goto", "refill1")), (T.label("urgent"), ("emerg",))]
    if n == "advice":
        return [(T.label("leave_msg"), ("goto", "msg1")), (T.label("urgent"), ("emerg",))]
    if n == "book1":
        return [(T.label("biscuit"), ("book", "Biscuit", "yearly check-up")), (T.label("willow"), ("book", "Willow", "vaccines")),
                (T.label("teddy"), ("sick", "Teddy"))]
    if n == "sick":
        return [(T.label("yes_urgent"), ("emerg",)), (T.label("no_wait"), ("after_book",))]
    if n == "time":
        offered = state.picks.get("offered", [])
        opts: list[tuple[str, tuple]] = [(T.slot_label(s["label"]), ("slot", s["id"])) for s in offered]
        if offered:
            opts.append((T.label("another_day"), ("cb_day",)))
        else:
            opts.append((T.label("yes_call"), ("cb_day",)))
        return opts
    if n == "refill1":
        return [(T.label("biscuit_refill"), ("refill", "Biscuit", "carprofen refill")), (T.label("pepper_food"), ("refill", "Pepper", "food order"))]
    return []


def view(state: State, ctx: Ctx) -> dict:
    opts = _options(state, ctx)
    return {
        "node": state.node,
        "options": [{"index": i, "label": str(lab)} for i, (lab, _) in enumerate(opts)],
        "done": state.done,
        "accepts_text": not state.done,
    }


# ---------------------------------------------------------------- step
def step(state: State, inp: dict, ctx: Ctx) -> tuple[State, list[str], dict | None]:
    st = copy.deepcopy(state)
    bot: list[str] = []
    if st.done:
        t = T.finished()
        st.messages.append({"who": "bot", "text": str(t)})
        return st, [t], None

    opts = _options(st, ctx)
    action: tuple | None = None
    if inp.get("option_index") is not None:
        i = inp["option_index"]
        if not isinstance(i, int) or isinstance(i, bool) or not (0 <= i < len(opts)):
            raise ValueError("no such option")
        client_text = str(opts[i][0])
        action = opts[i][1]
        free = False
    else:
        client_text = (inp.get("text") or "").strip()
        if not client_text:
            raise ValueError("empty message")
        if len(client_text) > MAX_TEXT:
            raise ValueError("message too long")
        free = True
    st.messages.append({"who": "client", "text": client_text})

    # 1. Emergency check comes first, on every node, for every client input.
    det = em.detect(client_text, ctx.keywords, ctx.layer2 if free else None)
    if det["emergency"]:
        return _emergency(st, ctx, det, client_text, free)
    if action and action[0] == "emerg":
        return _emergency(st, ctx, {"emergency": True, "matched": [], "layer": "1"}, client_text, False)
    negated = _has_word(client_text, ["not urgent", "not an emergency", "isnt urgent", "isnt an emergency", "not really urgent", "no emergency", "not an urgent"])
    if free and not negated and st.node == "sick" and _has_word(client_text, ["yes", "yeah", "yep", "it is", "he is", "she is", "they are"]) and not _has_word(client_text, ["no", "nope", "can wait"]):
        return _emergency(st, ctx, {"emergency": True, "matched": ["yes"], "layer": "1"}, client_text, True)
    if free and not negated and _has_word(client_text, URGENT_WORDS):
        return _emergency(st, ctx, {"emergency": True, "matched": ["urgent"], "layer": "1"}, client_text, True)

    # 2. Advice guard.
    if free and is_advice_request(client_text):
        st.node = "advice"
        st.picks["advice_count"] = st.picks.get("advice_count", 0) + 1
        return _say(st, [T.advice_refusal()]), [T.advice_refusal()], None

    out: dict | None = None
    if action is not None:
        bot, out = _do_action(st, ctx, action)
    else:
        bot, out = _free_text(st, ctx, client_text)
    for t in bot:
        st.messages.append({"who": "bot", "text": str(t)})
    if out is not None:
        st.done = True
        st.outcome = out
    return st, bot, out


def _say(st: State, texts: list[str]) -> State:
    for t in texts:
        st.messages.append({"who": "bot", "text": str(t)})
    return st


def _emergency(st: State, ctx: Ctx, det: dict, client_text: str, free: bool) -> tuple[State, list[str], dict]:
    hosp = ctx.confirmed_hospital()
    text = T.emergency(hosp)
    shown = [m for m in det["matched"] if not m.startswith("re:")][:5]
    if free:
        summary = f"Client wrote: {client_text[:200]}"
    else:
        summary = "Said it was urgent."
    if shown:
        summary += f" Matched: {', '.join(shown)}."
    summary += f" Pointed to {hosp['name']}." if hosp else " No confirmed emergency hospital, told to contact the nearest emergency vet."
    out = {
        "kind": "emergency", "summary": summary, "pet": st.picks.get("pet"), "matched": det["matched"],
        "layer": det["layer"], "hospital_id": hosp.get("id") if hosp else None,
    }
    st.node = "done_em"
    st.done = True
    st.outcome = out
    _say(st, [text])
    return st, [text], out


def _pet_from_text(text: str) -> str | None:
    m = _PET_RX.match(text or "")
    if not m:
        return None
    w = m.group(1)
    if w.lower() in {"hi", "hello", "yes", "no", "can", "please", "thanks", "okay", "ok", "dose", "ignore", "tell", "what", "how", "the", "my", "our", "its", "his", "her", "mg"}:
        return None
    return w


def _visit_from_text(text: str) -> str | None:
    t = em.normalize(text)
    for words, name in VISIT_MAP:
        if _has_word(t, words):
            return name
    return None


def _vt(ctx: Ctx, name: str | None) -> dict | None:
    if not name:
        return None
    for v in ctx.visit_types:
        if v["name"].lower() == name.lower():
            return v
    return None


def _go_time(st: State, ctx: Ctx) -> tuple[list[str], dict | None]:
    """After pet and visit are known: callbacks if requests are off or the visit is front desk only."""
    visit_name = st.picks.get("visit_name")
    vt = _vt(ctx, visit_name)
    if not ctx.toggles.get("req", True):
        return [T.done_booking_off()], _cb_outcome(st, "Booking", "Wants to book" + _visit_bit(st) + ".", ctx)
    if vt is None or not vt.get("bookable"):
        return [T.done_visit_front_desk()], _cb_outcome(st, "Booking", "Wants to book" + _visit_bit(st) + " (front desk books this visit type).", ctx)
    offered = _offer_slots(ctx, vt["id"])
    st.picks["offered"] = offered
    st.picks["visit_type_id"] = vt["id"]
    st.node = "time"
    if not offered:
        return [T.time_prompt_none()], None
    return [T.time_prompt()], None


def _visit_bit(st: State) -> str:
    v = st.picks.get("visit_name") or st.picks.get("visit_text")
    return f" for {v.lower()}" if v else ""


def _cb_outcome(st: State, cb_kind: str, summary: str, ctx: Ctx | None = None) -> dict:
    pet = st.picks.get("pet")
    st.node = "done_cb"
    return {"kind": "callback", "callback_kind": cb_kind, "summary": summary, "pet": pet}


def _done_message(st: State, ctx: Ctx, text: str, summary_prefix: str = "Left a message") -> tuple[list[str], dict]:
    st.picks["message"] = text[:300]
    out = _cb_outcome(st, "Message", f"{summary_prefix}: {text[:300]}", ctx)
    st.node = "done_msg"
    return ([T.done_msg()] if ctx.toggles.get("msg", True) else [T.done_msg_off()]), out


def _do_refill(st: State, ctx: Ctx, pet: str | None, item: str) -> tuple[list[str], dict]:
    st.picks["pet"] = pet
    st.picks["refill_text"] = item
    out = _cb_outcome(st, "Refill", f"Asked for: {(pet + ', ') if pet else ''}{item}.", ctx)
    st.node = "done_refill"
    return ([T.done_refill()] if ctx.toggles.get("msg", True) else [T.done_msg_off()]), out


def _book_done(st: State, ctx: Ctx, slot: dict) -> tuple[list[str], dict]:
    pet = st.picks.get("pet")
    vt = _vt(ctx, st.picks.get("visit_name"))
    st.node = "done_book"
    maker = T.done_book_call if st.channel == "call" else T.done_book
    out = {
        "kind": "booking", "pet": pet, "slot_id": slot["id"], "slot_label": slot["label"],
        "visit_type_id": vt["id"] if vt else None, "visit_name": vt["name"] if vt else None,
        "summary": f"Wants {(vt['name'] if vt else 'a visit').lower()}. Asked for {slot['label']}.",
    }
    return [maker(slot["label"], pet)], out


def _do_action(st: State, ctx: Ctx, action: tuple) -> tuple[list[str], dict | None]:
    kind = action[0]
    if kind == "goto":
        st.node = action[1]
        return [{"book1": T.book1_prompt, "refill1": T.refill1_prompt, "msg1": T.msg_prompt}[action[1]]()], None
    if kind == "book":
        st.picks.update(pet=action[1], visit_text=action[2], visit_name=_visit_from_text(action[2]))
        return _go_time(st, ctx)
    if kind == "sick":
        st.picks.update(pet=action[1], visit_text="unwell", visit_name="Sick visit")
        st.node = "sick"
        return [T.sick_prompt()], None
    if kind == "after_book":
        return _go_time(st, ctx)
    if kind == "slot":
        for s in st.picks.get("offered", []):
            if s["id"] == action[1]:
                return _book_done(st, ctx, s)
        return [T.time_reprompt()], None
    if kind == "cb_day":
        return [T.done_cb()], _cb_outcome(st, "Booking", "Wants a call about an appointment.", ctx)
    if kind == "refill":
        return _do_refill(st, ctx, action[1], action[2])
    raise ValueError("unknown action")


NO = ["no", "nope", "nah", "can wait", "not urgent", "not an emergency", "it can wait"]


def _free_text(st: State, ctx: Ctx, text: str) -> tuple[list[str], dict | None]:
    n = st.node
    if n in ("start", "advice"):
        return _classify_start(st, ctx, text)
    if n == "book1":
        pet = _pet_from_text(text)
        if pet:
            st.picks["pet"] = pet
        visit = _visit_from_text(text)
        st.picks["visit_text"] = text[:120]
        if _has_word(text, UNWELL_WORDS) and visit in (None, "Sick visit"):
            st.picks["visit_name"] = "Sick visit"
            st.node = "sick"
            return [T.sick_prompt()], None
        if visit is None and st.picks.get("book1_tries", 0) < 1:
            st.picks["book1_tries"] = 1
            return [T.book1_prompt()], None
        st.picks["visit_name"] = visit
        return _go_time(st, ctx)
    if n == "sick":
        t = em.normalize(text)
        if _has_word(t, NO):
            return _go_time(st, ctx)
        return [T.sick_reprompt()], None
    if n == "time":
        offered = st.picks.get("offered", [])
        t = em.normalize(text)
        if not offered:
            if _has_word(t, ["yes", "yeah", "yep", "please", "ok", "okay", "sure", "call me"]):
                return [T.done_cb()], _cb_outcome(st, "Booking", "Wants a call about an appointment.", ctx)
            return [T.time_reprompt()], None
        if _has_word(t, ["another", "different", "other day", "call me", "none", "neither", "no"]):
            return [T.done_cb()], _cb_outcome(st, "Booking", "Wants a call about an appointment.", ctx)
        pick = _match_slot(t, offered)
        if pick is not None:
            return _book_done(st, ctx, pick)
        return [T.time_reprompt()], None
    if n == "refill1":
        pet = _pet_from_text(text)
        return _do_refill(st, ctx, pet, text[:200])
    if n == "msg1":
        return _done_message(st, ctx, text)
    return _done_message(st, ctx, text)


_DAYS = {"mon": "monday", "tue": "tuesday", "wed": "wednesday", "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday"}


def _match_slot(t: str, offered: list[dict]) -> dict | None:
    words = set(t.split())
    ordinals = [({"first", "1", "1st"}, 0), ({"second", "2", "2nd"}, 1)]
    for keys, idx in ordinals:
        if words & keys and idx < len(offered):
            # only when no day or time is named
            return offered[idx]
    hits = []
    for s in offered:
        ln = em.normalize(s["label"]).split()  # e.g. thu oct 29 9 30 am
        day_hit = ln[0] in words or _DAYS.get(ln[0], "") in words
        time_hit = bool(re.search(r"\b" + re.escape(" ".join(ln[-3:-1])) + r"\b", t))
        if day_hit or time_hit:
            hits.append(s)
    return hits[0] if len(hits) == 1 else None


def _classify_start(st: State, ctx: Ctx, text: str) -> tuple[list[str], dict | None]:
    if _has_word(text, CANCEL_WORDS):
        return [T.done_change()], _cb_outcome(st, "Booking", "Wants to cancel or change an appointment.", ctx)
    if _has_word(text, ["refill", "prescription", "food order"]):
        st.node = "refill1"
        return [T.refill1_prompt()], None
    if _has_word(text, BOOK_WORDS):
        st.node = "book1"
        return [T.book1_prompt()], None
    if _has_word(text, REFILL_WORDS):
        st.node = "refill1"
        return [T.refill1_prompt()], None
    return _done_message(st, ctx, text)
