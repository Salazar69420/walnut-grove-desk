"""Booking from the app (the Book appointment button), the assistant telling clients open times, and learning from closed tasks."""
from __future__ import annotations

import dataclasses
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app import config
from app.adapters import llm as llm_adapter
from app.assistant import ai, booking
from app.models import CalendarEvent, Contact, KnowledgeEntry, Pet, SmsOffer, Task
from app.services import inbound, timeutil

PILOT = "+16045550161"


class Router:
    def __init__(self):
        self.reply: dict = {}
        self.calls: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": json.dumps(self.reply)}}], "usage": {}, "model": "m"})


@pytest.fixture()
def router(monkeypatch):
    r = Router()
    monkeypatch.setattr(llm_adapter, "TRANSPORT", httpx.MockTransport(r.handler))
    return r


@pytest.fixture()
def bridge(env, tmp_path):
    d = tmp_path / "bridge"
    for sub in ("requests", "send/requests", "send/results", "send/inbound"):
        (d / sub).mkdir(parents=True)
    config.set_settings(dataclasses.replace(env.settings, telus_dir=str(d)))
    yield d
    config.set_settings(env.settings)


def pilot(env, garg, ai_on=False):
    with env.session() as s:
        c = s.scalar(select(Contact).where(Contact.is_demo.is_(True), Contact.kind == "patient").order_by(Contact.id).limit(1))
        c.phone, c.sms_consent, c.ai_enabled, c.display_name = PILOT, "full", True, "Kartik Garg"
        s.query(CalendarEvent).delete()
        s.query(Pet).filter(Pet.contact_id == c.id).delete()
        s.add(Pet(contact_id=c.id, name="Rishav", species="dog", status="active"))
        s.commit()
        cid = c.id
    garg.put("/api/admin/sms", {"mode": "test_numbers_only", "test_numbers": [PILOT], "auto_reply": True})
    if ai_on:
        assert garg.put("/api/admin/assistant", {"api_key": "sk-or-v1-testkey-1234", "enabled": True}).status_code == 200
    return cid


def next_weekday(wd: int):
    d = timeutil.today_local() + timedelta(days=1)
    while d.weekday() != wd:
        d += timedelta(days=1)
    return d


# ---------------------------------------------------------------- the Book appointment button
def test_open_times_follow_the_clinic_hours(env, garg):
    with env.session() as s:
        s.query(CalendarEvent).delete()
        s.commit()
    mon = garg.get(f"/api/calendar/open-times?date={next_weekday(0)}&minutes=30").json()
    assert mon["hours"] == [["08:30", "19:00"]] and mon["times"][0]["label"] == "8:30 AM" and mon["times"][-1]["label"] == "6:30 PM"
    sun = garg.get(f"/api/calendar/open-times?date={next_weekday(6)}&minutes=30").json()
    assert sun["times"][0]["label"] == "9:00 AM" and sun["times"][-1]["label"] == "4:30 PM" and sun["visit_types"]


def test_any_staff_member_books_straight_into_the_schedule(env, bridge, garg, anita):
    cid = pilot(env, garg)
    day = next_weekday(1)
    first = garg.get(f"/api/calendar/open-times?date={day}").json()["times"][0]["start"]
    r = anita.post("/api/calendar/book", {"contact_id": cid, "starts_at": first, "notify": True})
    assert r.status_code == 201, r.text
    assert r.json()["item"]["status"] == "booked" and r.json()["texted"] == "queued"
    assert anita.post("/api/calendar/book", {"contact_id": cid, "starts_at": first}).status_code == 409  # taken now
    left = garg.get(f"/api/calendar/open-times?date={day}").json()["times"]
    assert first not in [t["start"] for t in left]
    late = timeutil.iso(timeutil.at_local(day, 19, 0))
    assert garg.post("/api/calendar/book", {"contact_id": cid, "starts_at": late}).status_code == 409  # after closing
    with env.session() as s:
        e = s.scalar(select(CalendarEvent).where(CalendarEvent.contact_id == cid))
        assert e.status == "booked" and e.source == "app"
        assert [x.starts_at for x in booking.upcoming(s, s.get(Contact, cid))] == [e.starts_at]  # the assistant sees it too


def test_booking_needs_a_signed_in_staff_member(env, anon):
    assert anon.post("/api/calendar/book", {"contact_id": 1, "starts_at": timeutil.iso(timeutil.now_utc() + timedelta(days=1))}).status_code in (401, 403)


# ---------------------------------------------------------------- the assistant tells open times
def test_the_ai_sees_open_times_and_the_clients_own_appointments(env, bridge, garg, router):
    cid = pilot(env, garg, ai_on=True)
    with env.session() as s:
        c = s.get(Contact, cid)
        system, texts = ai.system_prompt(s, None, c)
    assert "OPEN TIMES [A1]" in system and "A1" in texts
    times = None
    with env.session() as s:
        times = booking.availability(s)
    assert times
    router.reply = {"reply": f"Of course! We have {booking.label(times[0])} or {booking.label(times[1])}. Which suits you?", "answered": True, "needs_human": False,
                    "knowledge_ids": ["A1"], "reason": "open times", "task_title": "", "task_note": ""}
    msg = say(env, bridge, "Could Rishav be seen by the doctor soon?")
    assert msg and "Which suits you" in msg
    with env.session() as s:
        assert s.scalar(select(SmsOffer).where(SmsOffer.contact_id == cid, SmsOffer.status == "offered")) is not None
    say(env, bridge, "2")
    with env.session() as s:
        e = s.scalar(select(CalendarEvent).where(CalendarEvent.contact_id == cid))
        assert e.status == "requested" and timeutil.iso(e.starts_at) == timeutil.iso(times[1])


n = iter(range(5000, 9000))


def say(env, bridge, text):
    mid = next(n)
    before = {f.name for f in (bridge / "send" / "requests").iterdir()}
    at = timeutil.iso(timeutil.now_utc() - timedelta(seconds=30))
    (bridge / "send" / "inbound" / f"{mid}.json").write_text(json.dumps({"v": 1, "id": str(mid), "from": PILOT, "text": text, "at": at}))
    with env.session() as s:
        inbound.ingest(s)
    new = [f for f in (bridge / "send" / "requests").iterdir() if f.name not in before]
    return json.loads(new[0].read_text())["text"] if new else None


# ---------------------------------------------------------------- learning from how a person handled a task
def raise_task(env, bridge, garg, router):
    cid = pilot(env, garg, ai_on=True)
    router.reply = {"reply": "Good question! I will check with the team and someone will get back to you soon.", "answered": False, "needs_human": False,
                    "knowledge_ids": [], "reason": "not in knowledge", "task_title": "Asked about grooming", "task_note": "Wants to know if we groom cats."}
    say(env, bridge, "Do you do grooming for cats?")
    with env.session() as s:
        t = s.scalar(select(Task).where(Task.contact_id == cid, Task.raised_by == "ai", Task.kind == "unknown_knowledge"))
        assert t is not None and t.title.startswith("Asked about grooming")
        return cid, t.id


def test_a_closed_task_teaches_a_pending_knowledge_entry(env, bridge, garg, router):
    cid, tid = raise_task(env, bridge, garg, router)
    assert garg.post(f"/api/contacts/{cid}/messages", {"channel": "sms", "body": "Hi Kartik, we do not groom, but the groomer next door does."}).status_code == 201
    router.reply = {"should_learn": True, "category": "clinic_info", "title": "Grooming", "text": "We do not offer grooming, but the groomer next door does.",
                    "reason": "general"}
    assert garg.patch(f"/api/tasks/{tid}", {"status": "done"}).status_code == 200
    with env.session() as s:
        e = s.scalar(select(KnowledgeEntry).where(KnowledgeEntry.title == "Grooming"))
        assert e is not None and e.status == "pending" and e.proposed_by == "ai" and "handled a task" in e.source_ref
    sent_to_model = router.calls[-1]["messages"][1]["content"]
    assert "grooming for cats" in sent_to_model and "groomer next door" in sent_to_model


@pytest.mark.parametrize("reply", [
    {"should_learn": False, "category": "clinic_info", "title": "x", "text": "x", "reason": "about this client only"},
    {"should_learn": True, "category": "clinic_info", "title": "Rishav grooming", "text": "Rishav can come in on Monday for a trim.", "reason": "x"},
    {"should_learn": True, "category": "clinic_info", "title": "Dose", "text": "Give 50 mg of gabapentin twice a day.", "reason": "x"},
])
def test_nothing_personal_or_medical_is_learned(env, bridge, garg, router, reply):
    cid, tid = raise_task(env, bridge, garg, router)
    garg.post(f"/api/contacts/{cid}/messages", {"channel": "sms", "body": "Thanks Kartik, we will sort it out for you."})
    router.reply = reply
    with env.session() as s:
        before = len(s.scalars(select(KnowledgeEntry)).all())
    garg.patch(f"/api/tasks/{tid}", {"status": "done"})
    with env.session() as s:
        assert len(s.scalars(select(KnowledgeEntry)).all()) == before


def test_no_staff_reply_means_nothing_to_learn(env, bridge, garg, router):
    _, tid = raise_task(env, bridge, garg, router)
    calls = len(router.calls)
    garg.patch(f"/api/tasks/{tid}", {"status": "done"})
    assert len(router.calls) == calls


# ---------------------------------------------------------------- sounding like a person (owner screenshot 2026-10-31)
def test_thanks_gets_a_warm_reply_that_remembers_the_appointment_and_no_task(env, bridge, garg):
    cid = pilot(env, garg)
    say(env, bridge, "Can I book an appointment?")
    say(env, bridge, "3")
    with env.session() as s:
        tasks = len(s.scalars(select(Task).where(Task.contact_id == cid)).all())
    reply = say(env, bridge, "Thank you so much see you then")
    assert reply and "passed this to our team" not in reply and "Rishav" in reply
    with env.session() as s:
        assert len(s.scalars(select(Task).where(Task.contact_id == cid)).all()) == tasks


def test_a_placeholder_name_is_never_used_and_offered_times_are_spread_out(env, bridge, garg):
    cid = pilot(env, garg)
    with env.session() as s:
        s.get(Contact, cid).display_name = "Text from (604) 555-0161"
        s.commit()
    offer = say(env, bridge, "Can I book an appointment?")
    assert "Text" not in offer.split("!")[0] and "yearly check-up" not in offer.lower()
    with env.session() as s:
        slots = json.loads(s.scalar(select(SmsOffer.slots_json).where(SmsOffer.contact_id == cid, SmsOffer.status == "offered")))
    a, b = (timeutil.datetime.fromisoformat(x) for x in slots[:2])
    assert abs(b - a) >= timedelta(hours=2)
