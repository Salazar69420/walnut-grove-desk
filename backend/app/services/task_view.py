"""Tasks at a glance: turn a task's generated title and running note into labelled parts the screen can lay out.

Titles and reasons are written by several places (missed calls, call transcripts, text and phone bookings, the
assistant). They read like "Urgent, check-in on a pet: Caller (604) 555-0123" with a note of stacked lines such as
"Call Sat Oct 31, 9:12 AM: ...". The list should instead show a label (Missed call, Pickup, Confirm booking...),
who it is about, the number on its own, when it happened and one clean sentence. Nothing here changes the task.
"""
from __future__ import annotations

import re
from typing import Any

PHONE = re.compile(r"\(\d{3}\) \d{3}-\d{4}|\+\d{10,14}")
_WHEN = r"[A-Z][a-z]{2} [A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d{2} [AP]M"
CALL_LINE = re.compile(rf"^Call (?P<when>{_WHEN}): (?P<text>.+)$")
MISSED_LINE = re.compile(rf"^(?P<who>.+?) (?P<what>left a voicemail|called and nobody answered) at (?P<when>{_WHEN})\.(?: Number: (?P<phone>[^.]+)\.)?$")
NUMBER_LINE = re.compile(r"^Number: (?P<phone>.+?)\.$")
AGREED = re.compile(r"^A time was agreed on a call \((?P<text>.*)\)\. It is in the Calendar", re.S)

T_BOOKING = re.compile(r"^Confirm (?P<how>phone|text) booking: (?P<who>.+), (?P<when>[A-Z][a-z]{2} [A-Z][a-z]{2} \d{1,2}, .+)$")
T_CALLBACK = re.compile(r"^Call back (?P<who>.+?)(?:: (?P<topic>.+))?$")
T_URGENT = re.compile(r"^Urgent, (?P<topic>[^:]{2,60}): (?P<who>.+)$")
T_TOPIC = re.compile(r"^(?P<topic>[A-Z][^:]{1,40}): (?P<who>.+)$")
T_NOREPLY = re.compile(r"^Automatic reply not sent to (?P<who>.+)$")

# label -> tone (the colour family on screen)
TONES = {"urgent": "crit", "booking": "info", "missed": "warn", "care": "ok", "admin": "mute", "text": "vio", "other": "acc"}
TOPIC_TONE = {
    "booking": "booking", "reschedule or cancel": "booking", "confirm booking": "booking",
    "pickup": "care", "refill": "care", "food or supply order": "care", "asked about results": "care", "results": "care", "records request": "admin",
    "check-in on a pet": "care", "symptom question": "urgent", "complaint": "urgent", "vendor or business": "admin", "wrong number or spam": "admin",
    "billing or insurance": "admin", "price or policy question": "other", "missed call": "missed", "voicemail": "missed", "reply not sent": "text",
}


def _cap(s: str) -> str:
    s = s.strip()
    return s[:1].upper() + s[1:]


def _unknown(name: str) -> str | None:
    """'Caller (604) 555-0123' or a bare number: a caller we have no name for. Returns the number."""
    m = PHONE.search(name or "")
    if m and re.sub(r"^Caller\s*", "", name.strip()) == m.group(0):
        return m.group(0)
    return None


def _clip(text: str, n: int = 170) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    stop = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[:stop + 1] if stop > 60 else cut.rsplit(" ", 1)[0] + "...").strip()


def glance(title: str, reason: str | None, kind: str, kind_label: str, contact_name: str | None) -> dict[str, Any]:
    title = (title or "").strip()
    lines = [x.strip() for x in (reason or "").splitlines() if x.strip()]
    label, who, channel, when, appt, urgent = "", "", "", "", "", False

    if m := T_BOOKING.match(title):
        label, who, channel, appt = "Confirm booking", m["who"], m["how"], m["when"]
    elif m := T_URGENT.match(title):
        label, who, channel, urgent = _cap(m["topic"]), m["who"], "phone", True
    elif m := T_CALLBACK.match(title):
        who, channel = m["who"], "missed"
        label = _cap(m["topic"]) if m["topic"] else ""
    elif m := T_NOREPLY.match(title):
        label, who, channel = "Reply not sent", m["who"], "text"
    elif (m := T_TOPIC.match(title)) and (m["topic"].lower() in TOPIC_TONE or m["topic"] == "Call"):
        label, who, channel = ("Call follow-up" if m["topic"] == "Call" else m["topic"]), m["who"], "phone"

    # the note: newest call summary, missed attempts, the next step and the number
    calls, missed, voicemail, phone, next_step, other = [], 0, False, "", "", []
    for ln in lines:
        if m := CALL_LINE.match(ln):
            text = m["text"]
            if " Next step: " in text:
                text, nxt = text.split(" Next step: ", 1)
                next_step = nxt.strip()
            calls.append((m["when"], text))
        elif m := MISSED_LINE.match(ln):
            missed += 1
            voicemail = voicemail or m["what"] == "left a voicemail"
            when = m["when"]
            phone = phone or (m["phone"] or "")
        elif m := NUMBER_LINE.match(ln):
            phone = phone or m["phone"]
        else:
            other.append(ln)

    if not label:
        label = ("Voicemail" if voicemail else "Missed call") if channel == "missed" else kind_label
        if not who:
            who = contact_name or ""
    if calls:
        when = calls[-1][0]
        if channel != "text" and not (channel == "missed" and missed):
            channel = "phone"  # someone spoke on a call, even if it began as a missed call

    if calls:
        summary = calls[-1][1]
    elif other and (m := AGREED.match(" ".join(other))):
        summary = m["text"]
    elif other:
        summary = other[0]
        if channel == "text" and ". Read the conversation" in summary:
            summary = summary.split(". Read the conversation", 1)[0] + "."
    elif missed:
        summary = ("Left a voicemail" if voicemail else "Called, nobody answered") + (f" ({missed} tries)" if missed > 1 else "") + "."
    else:
        summary = "" if title.lower().startswith(label.lower()) else title

    # who: prefer the linked contact's real name; a "Caller (604)..." becomes Unknown caller with the number beside it
    num = _unknown(who)
    if contact_name and not _unknown(contact_name):
        who = contact_name
    elif num:
        who, phone = "Unknown caller", phone or num
    if not phone and (m := PHONE.search(" ".join(lines))) and who == "Unknown caller":
        phone = m.group(0)
    if missed > 1 and calls:
        summary = f"{summary} Also {missed} missed tries."

    group = TOPIC_TONE.get(label.lower(), "booking" if kind == "booking" else "missed" if channel == "missed" else "other")
    if urgent:
        group = "urgent"
    return {"label": label, "group": group, "tone": TONES[group], "urgent": urgent, "who": who.strip(), "phone": phone.strip(),
            "channel": channel, "when": when, "appointment": appt, "summary": _clip(summary),
            "next_step": _clip(next_step, 140), "attempts": missed}
