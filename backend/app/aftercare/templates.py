"""Every client-facing string the after-hours assistant can say.

The engine never builds client text any other way. Each function returns a `Tpl`
(a str subclass) so a test can prove that every emitted string came from here.
Client free text is never echoed, except a pet name that passed a strict check.
"""
from __future__ import annotations


class Tpl(str):
    """A string that was produced by this module."""

    __slots__ = ()


def _t(s: str) -> Tpl:
    return Tpl(s)


DISCLOSURE = "automated assistant"
RECORDED = "This call is recorded."

# ---------------------------------------------------------------- opening
def opening(clinic_name: str, channel: str, custom_line: str | None = None) -> Tpl:
    base = (
        f"Hi, this is the automated assistant for {clinic_name}. The clinic is closed until 8 am. "
        "I can take an appointment request or pass on a message."
    )
    if custom_line and DISCLOSURE in custom_line.lower():
        base = custom_line.strip()
    first, _, rest = base.partition(". ")
    if channel == "call":
        base = f"{first}. {RECORDED}" + (f" {rest}" if rest else "")
    if not base.endswith((".", "?", "!")):
        base += "."
    return _t(base + " What do you need?")


# ---------------------------------------------------------------- nodes
def book1_prompt() -> Tpl:
    return _t("Happy to help. Which pet, and what is the visit for?")


def sick_prompt() -> Tpl:
    return _t("Is this an emergency? For example trouble breathing, bleeding, a seizure, or something he ate.")


def time_prompt() -> Tpl:
    return _t("Dr. Garg has these openings. Which works best?")


def time_prompt_none() -> Tpl:
    return _t("I do not see any openings I can offer right now. Shall I ask the front desk to call you after 8 am?")


def time_reprompt() -> Tpl:
    return _t("Please pick one of the openings below, or choose another day and the front desk will call you.")


def sick_reprompt() -> Tpl:
    return _t("Please tell me if this is an emergency, yes or no.")


def refill1_prompt() -> Tpl:
    return _t("Which pet, and what do you need?")


def msg_prompt() -> Tpl:
    return _t("What would you like me to tell the clinic team? I will pass it on for the morning.")


def done_book(slot_label: str, pet: str | None) -> Tpl:
    who = pet or "your pet"
    return _t(f"Done. I have asked for {slot_label} for {who}. The front desk will confirm by text after 8 am. Reply here if anything changes.")


def done_book_call(slot_label: str, pet: str | None) -> Tpl:
    who = pet or "your pet"
    return _t(f"Done. I have asked for {slot_label} for {who}. The front desk will confirm after 8 am.")


def done_cb() -> Tpl:
    return _t("No problem. I have noted that you would like a call, and the front desk will phone you after 8 am.")


def done_booking_off() -> Tpl:
    return _t("The assistant is not taking appointment requests tonight. I have noted that you would like to book, and the front desk will phone you after 8 am.")


def done_visit_front_desk() -> Tpl:
    return _t("The front desk books that kind of visit. I have noted your request, and they will phone you after 8 am.")


def done_change() -> Tpl:
    return _t("Thanks. I have noted that you want to change an appointment, and the front desk will phone you after 8 am.")


def done_refill() -> Tpl:
    return _t("Thanks, I have passed that to the front desk. They will call you after 8 am. A vet has to approve any medicine refill.")


def done_msg() -> Tpl:
    return _t("Thanks. I have passed that to the front desk and they will call you after 8 am.")


def done_msg_off() -> Tpl:
    return _t(
        "I am not able to take messages right now. The clinic is closed until 8 am, so please call back after 8 am. "
        "I have still noted your request for the front desk."
    )


def advice_refusal() -> Tpl:
    return _t(
        "I am an automated assistant and I cannot give medical advice or medicine amounts. "
        "If you are worried about your pet right now, please contact an emergency veterinarian. "
        "I can also pass a message to the clinic team for the morning."
    )


def too_long() -> Tpl:
    return _t("That message is a bit long for me. Please say it in a few words, or choose one of the buttons.")


def finished() -> Tpl:
    return _t("This conversation is finished. Please start a new one if you need anything else.")


# ---------------------------------------------------------------- emergency
def _hours_part(hours_claim: str) -> str:
    h = (hours_claim or "").lower()
    if "24 hours" in h or "24 hour" in h or "24/7" in h:
        return " and says it is open 24 hours"
    return ""


def emergency(hospital: dict | None) -> Tpl:
    """hospital is a confirmed, non-placeholder hospital dict, or None."""
    if hospital is None:
        return _t(
            "That sounds like it needs a vet now. Please contact the nearest emergency veterinarian right away. "
            "The clinic will follow up with you in the morning."
        )
    name = hospital.get("name", "").strip()
    place = (hospital.get("place") or "").strip()
    where = f"{name} in {place}" if place else name
    s = f"That sounds like it needs a vet now. {where} is on the clinic emergency list{_hours_part(hospital.get('hours_claim', ''))}. "
    if (hospital.get("phone") or "").strip():
        s += f"Their phone number is {hospital['phone'].strip()}. "
    if (hospital.get("address") or "").strip():
        s += f"The address is {hospital['address'].strip()}. "
    s += "Please go now, and call them on the way if you can. The clinic will check in with you in the morning."
    return _t(s)


# ---------------------------------------------------------------- option labels
def label(key: str) -> Tpl:
    return _t(LABELS[key])


LABELS = {
    "book": "Book a visit",
    "refill": "Refill or food order",
    "urgent": "Something urgent",
    "yes_urgent": "Yes, it is urgent",
    "no_wait": "No, it can wait",
    "another_day": "Another day, please call me",
    "leave_msg": "Leave a message for the clinic",
    "biscuit": "Biscuit, yearly check-up",
    "willow": "Willow, vaccines",
    "teddy": "Teddy, he seems unwell",
    "biscuit_refill": "Biscuit, carprofen refill",
    "pepper_food": "Pepper, food order",
    "yes_call": "Yes, ask the front desk to call me",
}

# ---------------------------------------------------------------- staff-facing summaries (not client text)
def summary_booking(pet: str | None, visit: str, slot_label: str) -> str:
    return f"Wants {visit.lower()}. Asked for {slot_label}."


# ---------------------------------------------------------------- staff-reviewed reply drafts (the assistant only suggests; a person sends)
def reply_answers(first: str, bodies: list[str]) -> Tpl:
    """Approved clinic knowledge, word for word, in a short wrapper."""
    body = " ".join(b.strip() for b in bodies if b.strip())
    return _t(f"Hi {first}, thanks for your message. {body} If there is anything else, just reply here.")


def reply_followup(first: str) -> Tpl:
    return _t(f"Hi {first}, thanks for your message. We want to give you the right answer, so a member of our team will get back to you shortly.")


def reply_medical(first: str) -> Tpl:
    return _t(
        f"Hi {first}, thanks for letting us know. We cannot give medical advice or medicine amounts by text. "
        "A member of our veterinary team will call you as soon as they can. If your pet seems to be getting worse, "
        "please contact an emergency veterinarian."
    )


def all_client_templates() -> list[str]:
    """Static strings, for tests that scan for forbidden words."""
    return [
        str(f()) for f in (
            book1_prompt, sick_prompt, time_prompt, time_prompt_none, time_reprompt, sick_reprompt, refill1_prompt,
            msg_prompt, done_cb, done_booking_off, done_visit_front_desk, done_change, done_refill, done_msg,
            done_msg_off, advice_refusal, too_long, finished,
        )
    ] + [str(reply_followup("there")), str(reply_medical("there"))]


def slot_label(label: str) -> Tpl:
    """An opening shown as a button. Comes from the open_slots table, formatted by the server."""
    return _t(label)


def confirmation_sms(clinic_name: str, slot_label: str, pet: str | None) -> Tpl:
    who = f" for {pet}" if pet else ""
    return _t(f"Hi, this is {clinic_name}. Your appointment{who} is confirmed for {slot_label}. Reply here if anything changes.")


def missed_call_sms(clinic_name: str) -> Tpl:
    return _t(f"Hi, this is {clinic_name}. Sorry we missed your call. Reply here and we will call you back, or ask for a time to book.")


def retell_booking_say(slot_label: str, pet: str | None) -> Tpl:
    who = pet or "your pet"
    return _t(f"I have asked for {slot_label} for {who}. The front desk will confirm after 8 am.")


def retell_callback_say() -> Tpl:
    return _t("Thanks. I have passed that to the front desk and they will call you after 8 am.")
