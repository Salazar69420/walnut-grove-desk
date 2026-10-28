"""Strict label schema shared by rules, LLM labeler, gold labels and evaluation."""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RealDirection(str, Enum):
    inbound = "inbound"
    outbound = "outbound"
    internal = "internal"
    unknown = "unknown"


class CallerType(str, Enum):
    client = "client"
    other_clinic = "other_clinic"
    lab_or_courier = "lab_or_courier"
    vendor_or_sales = "vendor_or_sales"
    staff_internal = "staff_internal"
    private_personal = "private_personal"
    automated_or_dead_air = "automated_or_dead_air"
    unknown = "unknown"


class Intent(str, Enum):
    booking = "booking"
    reschedule_or_cancel = "reschedule_or_cancel"
    refill = "refill"
    food_or_supply_order = "food_or_supply_order"
    pickup_ready = "pickup_ready"
    results_question = "results_question"
    results_call_out = "results_call_out"
    recall_or_reminder = "recall_or_reminder"
    check_in = "check_in"
    records_request = "records_request"
    courier_or_lab_logistics = "courier_or_lab_logistics"
    pricing_or_policy_question = "pricing_or_policy_question"
    triage_symptom = "triage_symptom"
    billing_or_insurance = "billing_or_insurance"
    complaint = "complaint"
    vendor_support = "vendor_support"
    internal_logistics = "internal_logistics"
    other = "other"


class Outcome(str, Enum):
    resolved_on_call = "resolved_on_call"
    callback_promised = "callback_promised"
    voicemail_left = "voicemail_left"
    no_answer = "no_answer"
    transferred = "transferred"
    hold_then_dropped = "hold_then_dropped"
    unclear = "unclear"


class Urgency(str, Enum):
    none = "none"
    soon = "soon"
    emergency_signs = "emergency_signs"


# Fixed vocabularies: labels never carry free text from a call.
RED_FLAG_VOCAB = [
    "trouble_breathing",
    "bleeding",
    "seizure",
    "collapse",
    "very_sleepy_after_surgery",
    "hit_by_car",
    "toxin_chocolate",
    "toxin_xylitol",
    "toxin_rat_poison",
    "toxin_grapes",
    "toxin_medication",
    "cannot_pass_urine",
    "swollen_hard_belly",
    "vomiting_blood",
    "pale_gums",
    "heatstroke",
    "eye_injury",
    "difficult_birth",
    "emergency_stated",
]

FOLLOWUP_VOCAB = [
    "call back with results",
    "call back to confirm",
    "clinic to call client back",
    "doctor to review and call back",
    "check stock and call back",
    "client to call clinic back",
    "send records",
    "other",
]

_LANG_RE = re.compile(r"^(?:[a-z]{2,3}|unknown)$")


class CallLabel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    real_direction: RealDirection
    caller_type: CallerType
    intent: Intent
    outcome: Outcome
    urgency: Urgency
    red_flags: list[str] = Field(default_factory=list, max_length=5)
    promised_followup: bool
    followup_text: Optional[str] = None
    contains_private_content: bool
    language: str
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("red_flags")
    @classmethod
    def _flags(cls, v: list[str]) -> list[str]:
        for item in v:
            if len(item) > 40:
                raise ValueError("red flag too long")
            if item not in RED_FLAG_VOCAB:
                raise ValueError(f"red flag not in fixed vocabulary: {item!r}")
        return v

    @field_validator("followup_text")
    @classmethod
    def _follow(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in FOLLOWUP_VOCAB:
            raise ValueError("followup_text not in fixed vocabulary")
        return v

    @field_validator("language")
    @classmethod
    def _lang(cls, v: str) -> str:
        v = v.strip().lower()
        if not _LANG_RE.match(v):
            raise ValueError("language must be a 2-3 letter code or 'unknown'")
        return v


LABEL_FIELDS = [
    "real_direction",
    "caller_type",
    "intent",
    "outcome",
    "urgency",
    "red_flags",
    "promised_followup",
    "followup_text",
    "contains_private_content",
    "language",
]
