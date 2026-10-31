"""Every client-facing string the after-hours assistant can say.

The engine never builds client text any other way. Each function returns a `Tpl`
(a str subclass) so a test can prove that every emitted string came from here.
Client free text is never echoed, except a pet name that passed a strict check.
"""
from __future__ import annotations

import re


class Tpl(str):
    """A string that was produced by this module."""

    __slots__ = ()


def _t(s: str) -> Tpl:
    return Tpl(s)


PLACEHOLDER = re.compile(r"^(text from|call from|unknown|new contact|caller)\b|\d", re.I)


def first_name(display_name: str | None) -> str:
    """The first name to greet someone by, or "" when we do not know it (a contact still called "Text from (604) ...")."""
    name = (display_name or "").strip()
    if not name or PLACEHOLDER.search(name):
        return ""
    return name.split()[0]


def _hi(first: str) -> str:
    return f"Hi {first}" if first and first != "there" else "Hi there"


def _c(first: str) -> str:
    """", Kartik" or nothing, for "Perfect, Kartik!" style lines."""
    return f", {first}" if first and first != "there" else ""


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
    """Approved clinic knowledge, word for word, in a short friendly wrapper."""
    body = " ".join(b.strip() for b in bodies if b.strip())
    return _t(f"{_hi(first)}! {body} Let me know if there is anything else I can help with.")


def reply_followup(first: str) -> Tpl:
    return _t(f"{_hi(first)}, thanks for reaching out. I want to make sure you get the right answer, so I have passed this to our team and someone will get back to you soon.")


def reply_medical(first: str, pet: str | None = None) -> Tpl:
    who = pet or "your pet"
    return _t(
        f"{_hi(first)}, thank you for letting us know, and I am sorry {who} is not feeling well. I cannot give medical advice by text, "
        f"so I have asked our veterinary team to call you as soon as they can. If {who} gets worse or you are worried, please contact an emergency vet right away."
    )


def emergency_text(first: str, pet: str | None, hospital: dict | None) -> Tpl:
    """The text-message version of the emergency answer: the same safety content as emergency(), said kindly."""
    who = pet or "your pet"
    if hospital is None:
        where = "Please contact the nearest emergency veterinarian right now."
    else:
        name = hospital.get("name", "").strip()
        where = f"Please go to {name} now" + (f", they are at {hospital['address'].strip()}" if (hospital.get("address") or "").strip() else "") + \
                (f" and their number is {hospital['phone'].strip()}" if (hospital.get("phone") or "").strip() else "") + "."
    return _t(f"{(first + ', I') if first and first != 'there' else 'I'} am so sorry, that sounds like {who} needs a vet right away. {where} Our team will follow up with you as soon as we can.")


def optin_prompt() -> Tpl:
    return _t("Hi, this is Walnut Grove Animal Hospital, thanks for your text! To get replies from us by text, please reply Y. You can reply STOP at any time.")


def optin_confirmed(first: str) -> Tpl:
    return _t(f"Thank you{_c(first)}! You are all set to get texts from us. You can reply STOP at any time.")


# ---------------------------------------------------------------- booking by text (times always come from the code, never from a model)
def booking_offer(first: str, visit: str | None, labels: list[str]) -> Tpl:
    opts = " ".join(f"{i}) {l}." for i, l in enumerate(labels, 1))
    what = f" for a {visit.lower()}" if visit else ""
    return _t(f"Happy to help{_c(first)}! Here are a few open times{what}: {opts} Just reply with the number that suits you, or tell me a day and time that works better.")


def booking_requested(first: str, label: str, pet: str | None) -> Tpl:
    who = f" for {pet}" if pet else ""
    return _t(f"Perfect{_c(first)}! I have asked for {label}{who}. One of our team will confirm it with you by text shortly.")


def booking_none(first: str, visit: str) -> Tpl:
    return _t(f"{_hi(first)}, I am sorry, I could not find an open time for a {visit.lower()} just now. Someone from our team will reach out to find a time that works for you.")


def booking_by_team(first: str, visit: str) -> Tpl:
    return _t(f"{_hi(first)}, a {visit.lower()} needs a quick chat with our team first, so someone will contact you soon to set it up.")


def booking_confirmed(first: str, label: str, pet: str | None) -> Tpl:
    who = f" for {pet}" if pet else ""
    return _t(f"Good news{_c(first)}, you are all set{who} for {label}. We look forward to seeing you! If anything changes, just text us here. Walnut Grove Animal Hospital")


# Short, natural replies to thanks, greetings and goodbyes (no task, no "passed to our team").
def reply_smalltalk(first: str, kind: str, pet: str | None = None, appt: str | None = None, seed: int = 0) -> Tpl:
    if kind == "thanks":
        opts = [f"You are so welcome{_c(first)}!", f"Our pleasure{_c(first)}!", f"Anytime{_c(first)}!"]
        tail = f" We look forward to seeing you{' and ' + pet if pet else ''} on {appt}." if appt else " We are here whenever you need us."
    elif kind == "greeting":
        opts = [f"{_hi(first)}! How can we help{' you and ' + pet if pet else ''} today?", f"{_hi(first)}, thanks for texting! What can we do for you{' and ' + pet if pet else ''}?"]
        tail = ""
    elif kind == "bye":
        opts = [f"Take care{_c(first)}!", f"Have a lovely day{_c(first)}!"]
        tail = f" See you on {appt}." if appt else " We are here if you need us."
    else:  # okay, great, sounds good
        opts = [f"Great{_c(first)}!", f"Sounds good{_c(first)}!", f"Perfect{_c(first)}!"]
        tail = f" See you on {appt}." if appt else " Just text us if anything else comes up."
    return _t(opts[seed % len(opts)] + tail)


AUTO_SIGNATURE = ""  # the owner chose no tag; the assistant still says it is automated whenever asked (see assistant/ai.py RULES)


def auto_signed(text: str) -> Tpl:
    return _t(text.rstrip())


def all_client_templates() -> list[str]:
    """Static strings, for tests that scan for forbidden words."""
    return [
        str(f()) for f in (
            book1_prompt, sick_prompt, time_prompt, time_prompt_none, time_reprompt, sick_reprompt, refill1_prompt,
            msg_prompt, done_cb, done_booking_off, done_visit_front_desk, done_change, done_refill, done_msg,
            done_msg_off, advice_refusal, too_long, finished,
        )
    ] + [str(reply_followup("there")), str(reply_medical("there")), str(optin_prompt()), str(emergency_text("there", None, None))] + [
        str(reply_smalltalk("", k, None, None, i)) for k in ("thanks", "greeting", "bye", "ok") for i in range(3)]


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
