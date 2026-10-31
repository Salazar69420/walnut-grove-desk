"""Booking by text and situational suggestions."""
from __future__ import annotations

import dataclasses
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import config
from app.assistant import booking
from app.models import CalendarEvent, Contact, Pet, Product, SmsOffer, Task
from app.services import inbound, timeutil

PILOT = "+16045550161"
ALL_DAY = {d: [["00:00", "23:30"]] for d in booking.DAYS}


@pytest.fixture()
def bridge(env, tmp_path):
    d = tmp_path / "bridge"
    for sub in ("requests", "send/requests", "send/results", "send/inbound"):
        (d / sub).mkdir(parents=True)
    config.set_settings(dataclasses.replace(env.settings, telus_dir=str(d)))
    yield d
    config.set_settings(env.settings)


def setup(env, garg, hours=True):
    with env.session() as s:
        c = s.scalar(select(Contact).where(Contact.is_demo.is_(True), Contact.kind == "patient").order_by(Contact.id).limit(1))
        c.phone, c.sms_consent, c.ai_enabled, c.display_name = PILOT, "full", True, "Kartik Garg"
        s.query(CalendarEvent).delete()
        s.commit()
        cid = c.id
    garg.put("/api/admin/sms", {"mode": "test_numbers_only", "test_numbers": [PILOT], "auto_reply": True})
    if hours:
        r = garg.put("/api/calendar/booking-hours", {"enabled": True, "days": ALL_DAY, "lead_minutes": 0, "slot_minutes": 30, "horizon_days": 14})
        assert r.status_code == 200, r.text
    return cid


n = iter(range(1000, 10000))


def say(env, bridge, text):
    mid = next(n)
    before = {f.name for f in (bridge / "send" / "requests").iterdir()}
    (bridge / "send" / "inbound" / f"{mid}.json").write_text(json.dumps({"v": 1, "id": str(mid), "from": PILOT, "text": text, "at": timeutil.iso(timeutil.now_utc())}))
    with env.session() as s:
        inbound.ingest(s)
    new = [f for f in (bridge / "send" / "requests").iterdir() if f.name not in before]
    return json.loads(new[0].read_text())["text"] if new else None


def test_the_clinic_hours_are_the_default_and_booking_by_text_can_be_switched_off(env, bridge, garg):
    setup(env, garg, hours=False)
    h = garg.get("/api/calendar/booking-hours").json()
    assert h["enabled"] is True and h["days"]["mon"] == [["08:30", "19:00"]] and h["days"]["sat"] == h["days"]["sun"] == [["09:00", "17:00"]]
    assert garg.put("/api/calendar/booking-hours", {"enabled": False}).status_code == 200
    reply = say(env, bridge, "Can I book a check-up?")
    assert reply and "1)" not in reply
    with env.session() as s:
        assert s.scalar(select(SmsOffer.id)) is None


def test_only_the_owner_sets_the_hours(env, bridge, priya):
    assert priya.put("/api/calendar/booking-hours", {"enabled": True}).status_code == 403
    assert priya.get("/api/calendar/booking-hours").status_code == 200


def test_offer_then_pick_creates_a_requested_appointment_and_a_task(env, bridge, garg):
    cid = setup(env, garg)
    offer = say(env, bridge, "Hi, can I book Biscuit in for his vaccines?")
    assert "1)" in offer and "2)" in offer and "3)" in offer and "vaccines" in offer.lower()
    with env.session() as s:
        o = s.scalar(select(SmsOffer).where(SmsOffer.contact_id == cid))
        second = json.loads(o.slots_json)[1]
    reply = say(env, bridge, "2 please")
    assert "I have asked for" in reply and booking.label(booking.crm.aware(booking.datetime.fromisoformat(second))) in reply
    with env.session() as s:
        e = s.scalar(select(CalendarEvent).where(CalendarEvent.contact_id == cid))
        assert e.status == "requested" and timeutil.iso(booking.crm.aware(e.starts_at)) == second and e.kind == "vaccine"
        assert s.scalar(select(Task).where(Task.kind == "booking", Task.contact_id == cid)).title.startswith("Confirm text booking")
        assert s.scalar(select(SmsOffer).where(SmsOffer.contact_id == cid)).status == "requested"


def test_a_taken_time_is_never_offered(env, bridge, garg):
    cid = setup(env, garg)
    with env.session() as s:
        first = booking.free_times(s, 20, n=1)[0]
        s.add(CalendarEvent(kind="checkup", title="Busy", starts_at=first, ends_at=first + timedelta(minutes=30), status="booked", source="app"))
        s.commit()
    say(env, bridge, "Can I book vaccines?")
    with env.session() as s:
        offered = json.loads(s.scalar(select(SmsOffer).where(SmsOffer.contact_id == cid)).slots_json)
    assert timeutil.iso(first) not in offered


def test_asking_for_other_times_offers_later_ones(env, bridge, garg):
    cid = setup(env, garg)
    say(env, bridge, "Can I book a check-up?")
    with env.session() as s:
        first = json.loads(s.scalar(select(SmsOffer).where(SmsOffer.contact_id == cid)).slots_json)
    reply = say(env, bridge, "None of those work, something later?")
    assert "1)" in reply
    with env.session() as s:
        o = s.scalars(select(SmsOffer).where(SmsOffer.contact_id == cid).order_by(SmsOffer.id)).all()
        assert o[0].status == "expired" and min(json.loads(o[-1].slots_json)) > max(first)


def test_a_day_and_part_of_day_are_respected(env, bridge, garg):
    cid = setup(env, garg)
    say(env, bridge, "Can I book a check-up on Tuesday morning?")
    with env.session() as s:
        slots = [booking.crm.aware(booking.datetime.fromisoformat(x)) for x in json.loads(s.scalar(select(SmsOffer).where(SmsOffer.contact_id == cid)).slots_json)]
    assert slots and all(timeutil.local(x).weekday() == 1 and timeutil.local(x).hour < 12 for x in slots)


def test_visits_that_are_not_bookable_by_text_go_to_the_team(env, bridge, garg):
    cid = setup(env, garg)
    reply = say(env, bridge, "Can I book a dental for my dog?")
    assert "team" in reply and "1)" not in reply
    with env.session() as s:
        assert s.scalar(select(Task).where(Task.kind == "booking", Task.contact_id == cid)) is not None


def test_an_emergency_wins_over_booking(env, bridge, garg):
    setup(env, garg)
    reply = say(env, bridge, "Can I book an appointment, my dog is having a seizure")
    assert "emergency veterinarian" in reply


def test_a_staff_suggestion_never_books(env, bridge, garg):
    cid = setup(env, garg)
    say(env, bridge, "Can I book vaccines?")
    r = garg.post(f"/api/contacts/{cid}/suggest-reply", {"text": "1"}).json()
    assert r["kind"] == "booking" and "Calendar" in r["note"]
    with env.session() as s:
        assert s.scalar(select(CalendarEvent.id).where(CalendarEvent.contact_id == cid)) is None


def test_staff_confirm_a_text_booking_and_can_text_the_client(env, bridge, garg, anita):
    cid = setup(env, garg)
    say(env, bridge, "Can I book vaccines?")
    say(env, bridge, "1")
    with env.session() as s:
        eid = s.scalar(select(CalendarEvent.id).where(CalendarEvent.contact_id == cid))
    r = anita.post(f"/api/calendar/events/{eid}/confirm", {"notify": True}).json()  # any staff member can approve
    assert r["item"]["status"] == "booked" and r["texted"] == "queued"
    assert garg.post(f"/api/calendar/events/{eid}/confirm", {}).status_code == 409


# ---------------------------------------------------------------- suggestions
def product(env, **kw):
    with env.session() as s:
        p = Product(name=kw.get("name", "Flea and tick chew"), price_cents=kw.get("price", 4500), pitch=kw.get("pitch", "While you are in, we also carry a monthly flea and tick chew."),
                    triggers=kw.get("triggers", "Vaccines, flea, tick"), species=kw.get("species", ""), status=kw.get("status", "approved"))
        s.add(p)
        s.commit()
        return p.id


def test_suggestions_are_off_by_default(env, bridge, garg):
    setup(env, garg)
    product(env)
    say(env, bridge, "Can I book vaccines?")
    assert "flea" not in say(env, bridge, "1").lower()


def test_one_fitting_suggestion_after_a_booking_then_not_again_soon(env, bridge, garg):
    setup(env, garg)
    product(env)
    product(env, name="Dental chews", pitch="We also have dental chews.", triggers="dental, teeth")
    assert garg.put("/api/upsell", {"enabled": True, "days_between": 14}).status_code == 200
    say(env, bridge, "Can I book vaccines?")
    reply = say(env, bridge, "1")
    assert "monthly flea and tick chew" in reply and "($45)" in reply and "dental chews" not in reply.lower()
    say(env, bridge, "Can I book vaccines for my other dog?")
    assert "flea" not in say(env, bridge, "1").lower()


def test_no_suggestion_for_a_species_it_does_not_fit_or_a_hard_moment(env, bridge, garg):
    cid = setup(env, garg)
    product(env, species="cat")
    garg.put("/api/upsell", {"enabled": True, "days_between": 14})
    with env.session() as s:
        s.query(Pet).filter(Pet.contact_id == cid).update({"species": "dog"})
        s.commit()
    say(env, bridge, "Can I book vaccines?")
    assert "flea" not in say(env, bridge, "1").lower()


def test_products_import_as_pending_and_need_a_sentence_to_approve(env, garg, priya):
    csv_text = "Name,Price,Pitch,Triggers,Species\nFlea chew,$45.00,We also carry a monthly flea chew.,\"Vaccines, flea\",dog\nNo pitch item,12,,,\n,5,,,\n"
    r = priya.post("/api/products/import", files={"file": ("products.csv", csv_text.encode(), "text/csv")})
    assert r.status_code == 200 and r.json() == {"added": 2, "skipped": 1}
    items = {p["name"]: p for p in garg.get("/api/products?status=pending").json()["items"]}
    assert items["Flea chew"]["price_cents"] == 4500 and items["Flea chew"]["species"] == "dog"
    assert priya.post(f"/api/products/{items['Flea chew']['id']}/approve").status_code == 403
    assert garg.post(f"/api/products/{items['No pitch item']['id']}/approve").status_code == 400
    assert garg.post(f"/api/products/{items['Flea chew']['id']}/approve").json()["item"]["status"] == "approved"
