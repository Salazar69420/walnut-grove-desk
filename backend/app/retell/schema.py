"""Internal request shapes for the voice agent functions."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

FUNCTION_NAMES = (
    "check_emergency", "get_open_slots", "create_appointment_request", "create_callback", "get_emergency_hospital",
)


class Loose(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class FunctionCall(Loose):
    call_id: str = Field(default="", max_length=120)
    name: str = Field(default="", max_length=60)
    args: dict[str, Any] = Field(default_factory=dict)


class CheckEmergencyArgs(Loose):
    text: str = Field(min_length=1, max_length=1000)


class GetOpenSlotsArgs(Loose):
    visit_type: str | None = Field(default=None, max_length=120)
    after: str | None = Field(default=None, max_length=40)


class CreateAppointmentArgs(Loose):
    client_name: str = Field(min_length=1, max_length=120)
    pet_name: str = Field(min_length=1, max_length=120)
    visit_type: str = Field(min_length=1, max_length=120)
    slot_id: int
    phone_last4: str | None = Field(default=None, max_length=4)

    @field_validator("phone_last4")
    @classmethod
    def _digits(cls, v: str | None) -> str | None:
        if v is not None and v != "" and not re.fullmatch(r"\d{4}", v):
            raise ValueError("phone_last4 must be 4 digits")
        return v or None


class CreateCallbackArgs(Loose):
    client_name: str = Field(min_length=1, max_length=120)
    pet_name: str | None = Field(default=None, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class GetEmergencyHospitalArgs(Loose):
    pass


ARG_MODELS = {
    "check_emergency": CheckEmergencyArgs,
    "get_open_slots": GetOpenSlotsArgs,
    "create_appointment_request": CreateAppointmentArgs,
    "create_callback": CreateCallbackArgs,
    "get_emergency_hospital": GetEmergencyHospitalArgs,
}


class CallAnalysis(Loose):
    call_summary: str = Field(default="", max_length=4000)


class WebhookCall(Loose):
    call_id: str = Field(default="", max_length=120)
    transcript: str = Field(default="", max_length=100_000)
    call_analysis: CallAnalysis = Field(default_factory=CallAnalysis)
    start_timestamp: int | None = None  # milliseconds since the epoch, as Retell sends it
    end_timestamp: int | None = None


class Webhook(Loose):
    event: str = Field(max_length=60)
    call: WebhookCall = Field(default_factory=WebhookCall)
