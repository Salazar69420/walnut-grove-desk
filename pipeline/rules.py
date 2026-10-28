"""Deterministic labeler (rules-v1). All patterns come from rules.yaml."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import yaml

from .config import DEFAULT_RULES
from .schema import (FOLLOWUP_VOCAB, RED_FLAG_VOCAB, CallLabel, CallerType, Intent, Outcome,
                     RealDirection, Urgency)

LABELER_ID = "rules-v1"
FLAGS = re.I | re.S
_SPLIT = re.compile(r"[.,;!?:]|\bbut\b|\band\b|\bthen\b|\bso\b")
_NEG = re.compile(r"\b(?:no|not|never|without|doesn.?t|isn.?t|hasn.?t|didn.?t|wasn.?t|don.?t|nor|neither)\b")


def norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


class Rules:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.th = cfg["thresholds"]
        cl = cfg["clinic"]
        self.clinic_name = [re.compile(p, FLAGS) for p in cl["name_patterns"]]
        union = "(?:" + "|".join(f"(?:{p})" for p in cl["name_patterns"]) + ")"
        self._union = union
        c = self._c
        self.greeting = c(cl["greeting_patterns"])
        self.generic = c(cl["generic_patterns"])
        self.automated = c(cfg["automated"]["patterns"])
        self.vm_markers = c(cfg["voicemail"]["markers"])
        self.vm_clinic_msg = c(cfg["voicemail"]["clinic_message_markers"])
        self.out_strong = c(cfg["outbound"]["strong"])
        self.out_weak = c(cfg["outbound"]["weak"])
        self.in_strong = c(cfg["inbound"]["strong"])
        self.in_weak = c(cfg["inbound"]["weak"])
        self.transfer = c(cfg["outcome"]["transfer"])
        self.hold = c(cfg["outcome"]["hold"])
        self.resolved = c(cfg["outcome"]["resolved"])
        self.cb_followups = set(cfg["outcome"]["callback_promised_followups"])
        self.followups = {k: c(v) for k, v in cfg["followups"].items()}
        ct = cfg["caller_types"]
        self.ct_priority = list(ct["priority"])
        self.ct = {k: c(v) for k, v in ct.items() if k != "priority"}
        self.intent_order = list(cfg["intent_order"])
        self.intents = {k: c(v) for k, v in cfg["intents"].items()}
        self.symptoms = {k: c(v) for k, v in cfg["symptoms"].items()}
        self.soon = c(cfg["soon"])
        self.triage_extra = c(cfg["triage_extra"])
        self.work = c(cfg["work_signals"])
        self.priv_strong = c(cfg["private"]["strong"])
        self.priv_weak = c(cfg["private"]["weak"])
        self._validate()

    def _c(self, pats):
        return [re.compile(p.replace("{CLINIC}", self._union), FLAGS) for p in pats]

    def _validate(self):
        for k in self.symptoms:
            if k not in RED_FLAG_VOCAB:
                raise ValueError(f"symptom group {k!r} not in schema.RED_FLAG_VOCAB")
        for k in self.followups:
            if k not in FOLLOWUP_VOCAB:
                raise ValueError(f"followup {k!r} not in schema.FOLLOWUP_VOCAB")
        valid = {i.value for i in Intent} | {"results"}
        for k in list(self.intents) + self.intent_order:
            if k not in valid:
                raise ValueError(f"unknown intent group {k!r}")
        for k in self.ct:
            if k not in {c.value for c in CallerType}:
                raise ValueError(f"unknown caller type {k!r}")


def load_rules(path: str | None = None) -> Rules:
    with open(path or DEFAULT_RULES, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("WALNUT_PRIVATE_AGGRESSIVE") == "0":  # tests check the base rules in isolation
        cfg.setdefault("thresholds", {})["private_aggressive"] = False
    return Rules(cfg)


def _count(pats, text: str) -> int:
    return sum(1 for p in pats if p.search(text))


def _any(pats, text: str) -> bool:
    return any(p.search(text) for p in pats)


def _non_negated(pat, text: str, window: int) -> bool:
    for m in pat.finditer(text):
        pre = text[max(0, m.start() - window):m.start()]
        pre = _SPLIT.split(pre)[-1]
        if _NEG.search(pre):
            continue
        return True
    return False


def detect_language(declared: str | None, text: str) -> str:
    d = (declared or "").strip().lower()
    if re.fullmatch(r"[a-z]{2,3}", d):
        return d
    if re.search(r"[ऀ-ॿ]", text):
        return "hin"
    if re.search(r"[਀-੿]", text):
        return "pan"
    return "eng" if text.strip() else "unknown"


def _other_clinic_count(rs: Rules, text: str) -> int:
    n = 0
    for p in rs.ct.get("other_clinic", []):
        m = p.search(text)
        if m and not _any(rs.clinic_name, m.group(0)):
            n += 1
    return n


def label_call(rs: Rules, plain_text: str | None, seconds: float | None, language: str | None = None,
               has_number: bool = True, ext: str | None = None) -> CallLabel:
    th = rs.th
    text = norm(plain_text)
    words = re.findall(r"\w+", text)
    nw = len(words)
    secs = seconds or 0.0
    lang = detect_language(language, text)

    work = _count(rs.work, text)
    auto_hits = _count(rs.automated, text)
    vm_leave = _count(rs.vm_markers, text)

    # ---- dead air / automated -------------------------------------------------------------
    dead = nw < th["dead_air_min_words"] or len(text) < th["dead_air_min_chars"]
    if not dead and nw >= th["low_diversity_min_words"] and len(set(words)) / nw < th["low_diversity_ratio"]:
        dead = True
    if not dead and auto_hits >= 1 and work <= 1 and vm_leave == 0 and nw < 80:
        dead = True
    # a real conversation with a personal marker is never dead air
    if dead and nw >= th["dead_air_min_words"] and _count(rs.priv_strong, text) >= 1:
        dead = False
    if dead:
        out_hint = _count(rs.out_strong, text) + _count(rs.out_weak, text)
        return CallLabel(
            real_direction=RealDirection.outbound if out_hint else RealDirection.unknown,
            caller_type=CallerType.automated_or_dead_air, intent=Intent.other,
            outcome=Outcome.no_answer if nw < th["dead_air_min_words"] else Outcome.unclear,
            urgency=Urgency.none, red_flags=[], promised_followup=False, followup_text=None,
            # short human talk with no automated marker carries no clinic value, so policy treats it as private
            contains_private_content=bool(th.get("private_aggressive") and nw > 0 and auto_hits == 0 and vm_leave == 0),
            language=lang, confidence=0.6 if nw == 0 else 0.5)

    # ---- caller type ------------------------------------------------------------------------
    cw, ow = th["client_weight"], th["other_type_weight"]
    scores = {}
    for t, pats in rs.ct.items():
        if t == "private_personal":
            continue
        n = _other_clinic_count(rs, text) if t == "other_clinic" else _count(pats, text)
        scores[t] = n * (cw if t == "client" else ow)
    priv_strong = _count(rs.priv_strong, text)
    priv_weak = _count(rs.priv_weak, text)
    private_marker = priv_strong >= 1 or (priv_weak >= th["weak_private_min_hits"] and priv_weak > work)
    other_best = max([v for k, v in scores.items() if k != "client"] or [0])

    if private_marker and work <= th["private_max_work_signals"] and other_best == 0:
        ctype = CallerType.private_personal
    else:
        cands = [t for t in rs.ct_priority if scores.get(t, 0) > 0]
        if cands:
            best = max(cands, key=lambda t: (scores[t], -rs.ct_priority.index(t)))
            ctype = CallerType(best)
        else:
            ctype = CallerType.unknown

    window = th["negation_window_chars"]
    flags_pre = [name for name, pats in rs.symptoms.items()
                 if any(_non_negated(p, text, window) for p in pats)][:5]

    # ---- direction ----------------------------------------------------------------------------
    head = text[: th["head_window_chars"]]
    gwin = text[: th["greeting_window_chars"]]
    greeting = _any(rs.greeting, gwin)
    out_strong = _count(rs.out_strong, head)
    out_weak = _count(rs.out_weak, head)
    in_strong = _count(rs.in_strong, head)
    in_weak = _count(rs.in_weak, text)
    clinic_name = _any(rs.clinic_name, text)
    if ctype == CallerType.staff_internal:
        direction = RealDirection.internal
    elif greeting and out_strong == 0:
        direction = RealDirection.inbound
    elif out_strong:
        direction = RealDirection.outbound
    elif in_strong:
        direction = RealDirection.inbound
    elif out_weak > in_weak:
        direction = RealDirection.outbound
    elif in_weak > out_weak:
        direction = RealDirection.inbound
    elif vm_leave and clinic_name:
        direction = RealDirection.inbound
    else:
        direction = RealDirection.unknown

    if ctype == CallerType.unknown and (
            (direction == RealDirection.outbound and work >= 1)
            or (direction == RealDirection.inbound and greeting)
            or flags_pre):
        ctype = CallerType.client   # counterparty of a clinic call/greeting with no other signal

    # ---- red flags / urgency ------------------------------------------------------------------
    flags = flags_pre
    soon = _count(rs.soon, text) > 0

    # ---- intent -------------------------------------------------------------------------------
    if ctype == CallerType.private_personal:
        intent = Intent.other
    elif ctype == CallerType.lab_or_courier:
        intent = Intent.courier_or_lab_logistics
    elif ctype == CallerType.staff_internal:
        intent = Intent.internal_logistics
    elif ctype == CallerType.vendor_or_sales:
        intent = Intent.vendor_support
    else:
        iscore = {k: _count(p, text) for k, p in rs.intents.items()}
        iscore["triage_symptom"] = iscore.get("triage_symptom", 0) + 2 * len(flags) + \
            _count(rs.triage_extra, text)
        best, best_s = None, 0
        for g in rs.intent_order:
            s = iscore.get(g, 0)
            if s > best_s:
                best, best_s = g, s
        if flags and ctype in (CallerType.client, CallerType.unknown):
            best = "triage_symptom"
        if best is None:
            intent = Intent.other
        elif best == "results":
            intent = Intent.results_call_out if direction == RealDirection.outbound else Intent.results_question
        else:
            intent = Intent(best)

    urgency = Urgency.emergency_signs if flags else (
        Urgency.soon if (soon or intent == Intent.triage_symptom) else Urgency.none)

    # ---- follow-up ----------------------------------------------------------------------------
    follow = next((name for name, pats in rs.followups.items() if _any(pats, text)), None)

    # ---- outcome ------------------------------------------------------------------------------
    vm = vm_leave >= 1 or (_any(rs.vm_clinic_msg, text) and nw <= th["vm_message_max_words"]
                           and direction == RealDirection.outbound)
    hold_dropped = False
    last_end = -1
    for p in rs.hold:
        for m in p.finditer(text):
            last_end = max(last_end, m.end())
    if last_end >= 0 and len(text) - last_end < 60 and len(text) - last_end <= th["hold_tail_chars"]:
        hold_dropped = True
    if vm:
        outcome = Outcome.voicemail_left
    elif _any(rs.transfer, text):
        outcome = Outcome.transferred
    elif hold_dropped:
        outcome = Outcome.hold_then_dropped
    elif follow in rs.cb_followups:
        outcome = Outcome.callback_promised
    elif _any(rs.resolved, text):
        outcome = Outcome.resolved_on_call
    else:
        outcome = Outcome.unclear
    promised = follow is not None or outcome == Outcome.callback_promised
    if promised and follow is None:
        follow = "clinic to call client back"

    # ---- private ------------------------------------------------------------------------------
    private = (ctype == CallerType.private_personal or private_marker
               or (work == 0 and other_best == 0 and secs > th["private_min_seconds"])
               # non-English calls with little clinic work are treated as private (when in doubt, private)
               or (lang not in ("eng", "fra", "unknown") and work <= 1))
    if th.get("private_aggressive"):
        # when in doubt, private: anything the rules cannot place as clinic work is treated as private
        private = private or ctype == CallerType.unknown or lang != "eng" or (
            intent == Intent.other and outcome == Outcome.unclear)

    conf = 0.35
    conf += 0.15 if direction != RealDirection.unknown else 0
    conf += 0.15 if ctype != CallerType.unknown else 0
    conf += 0.10 if intent != Intent.other else 0
    conf += 0.10 if outcome != Outcome.unclear else 0
    conf += 0.10 if work >= 3 else 0
    conf -= 0.10 if nw < 25 else 0
    conf = round(min(0.95, max(0.05, conf)), 2)

    return CallLabel(
        real_direction=direction, caller_type=ctype, intent=intent, outcome=outcome, urgency=urgency,
        red_flags=flags, promised_followup=promised, followup_text=follow if promised else None,
        contains_private_content=bool(private), language=lang, confidence=conf)
