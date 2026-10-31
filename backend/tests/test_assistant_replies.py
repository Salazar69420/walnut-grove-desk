"""Suggest a reply: emergency first, no medical advice, approved knowledge only, tasks when it does not know."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Contact, Hospital, KnowledgeEntry, Message, Task
from app.seed import answer_sheet
from app.services import timeutil


def a_contact(env, ai=True, name=None):
    with env.session() as s:
        c = s.scalar(select(Contact).where(Contact.is_demo.is_(True), Contact.kind == "patient").limit(1))
        c.ai_enabled = ai
        s.commit()
        return c.id


def approve(env, title, body, cat="clinic_info"):
    with env.session() as s:
        s.add(KnowledgeEntry(category=cat, title=title, body=body, status="approved", proposed_by="human", is_demo=False))
        s.commit()


def ask(who, cid, text=None):
    return who.post(f"/api/contacts/{cid}/suggest-reply", {"text": text} if text else {})


def test_off_by_default_per_person_and_needs_something_to_answer(env, priya):
    cid = a_contact(env, ai=False)
    assert ask(priya, cid, "What are your hours?").status_code == 409
    cid = a_contact(env, ai=True)
    with env.session() as s:
        s.query(Message).filter(Message.contact_id == cid).delete()
        s.commit()
    r = ask(priya, cid)
    assert r.status_code == 409 and "no message" in r.json()["detail"]


def test_answers_come_only_from_approved_knowledge_word_for_word(env, priya):
    cid = a_contact(env)
    body = "We can board hamsters for up to one week."
    with env.session() as s:
        s.add(KnowledgeEntry(category="clinic_info", title="Hamster boarding", body=body, status="pending", proposed_by="import"))
        s.commit()
    r = ask(priya, cid, "Do you offer hamster boarding?").json()
    assert r["kind"] == "unknown" and r["task"]["title"].startswith("No approved answer")  # pending knowledge is never used
    approve(env, "Hamster boarding", body)
    r = ask(priya, cid, "Do you offer hamster boarding please?").json()
    assert r["kind"] == "answer" and body in r["draft"] and r["task"] is None
    assert r["sources"][0]["title"] == "Hamster boarding"


def test_prices_come_only_from_approved_fees(env, priya, garg):
    cid = a_contact(env)
    fid = priya.post("/api/fees", {"service": "Microchip", "price_dollars": 58}).json()["item"]["id"]
    assert ask(priya, cid, "How much is a microchip?").json()["kind"] == "unknown"
    garg.post(f"/api/fees/{fid}/approve")
    r = ask(priya, cid, "How much is a microchip?").json()
    assert r["kind"] == "answer" and "$58" in r["draft"] and r["sources"][0]["kind"] == "fee"


@pytest.mark.parametrize("text", ["What dose of Metacam should I give?", "Is it safe to give him a pill for the pain?", "What is wrong with my cat?", "How many mg of gabapentin?"])
def test_medical_questions_are_never_answered(env, priya, text):
    cid = a_contact(env)
    approve(env, "Pain pills", "Give one tablet of the pain pill per day.", "patient_knowledge")  # even an approved entry must not be used for a medical question
    r = ask(priya, cid, text).json()
    assert r["kind"] == "medical" and "tablet" not in r["draft"].lower() and "cannot give medical advice" in r["draft"]
    assert r["task"] and r["task"]["title"].startswith("Medical question")


def test_a_poison_question_is_treated_as_an_emergency_not_a_dose_question(env, priya):
    cid = a_contact(env)
    r = ask(priya, cid, "How much ibuprofen can I give my dog?").json()
    assert r["kind"] == "emergency" and r["task"]["title"].startswith("Possible emergency") and "ibuprofen" not in r["draft"].lower()


def test_emergency_wording_wins_over_everything_and_names_only_a_confirmed_hospital(env, priya):
    cid = a_contact(env)
    approve(env, "Chocolate", "Chocolate is fine in small amounts.", "patient_knowledge")
    r = ask(priya, cid, "My dog ate a whole bar of dark chocolate and is shaking").json()
    assert r["kind"] == "emergency" and "emergency veterinarian" in r["draft"] and r["sources"] == []
    with env.session() as s:
        t = s.get(Task, r["task"]["id"])
        assert t.priority == "high" and t.kind == "emergency_followup" and t.raised_by == "ai"
        s.add(Hospital(name="Test Animal ER", place="Langley", phone="604-555-0100", address="1 Test Road", confirmed=False, is_placeholder=False))
        s.commit()
    assert "Test Animal ER" not in ask(priya, cid, "my dog is having a seizure").json()["draft"]  # unconfirmed hospitals are never named
    with env.session() as s:
        h = s.scalar(select(Hospital).where(Hospital.name == "Test Animal ER"))
        h.confirmed = True
        s.commit()
    assert "Test Animal ER" in ask(priya, cid, "my dog is having a seizure").json()["draft"]


def test_one_task_per_person_per_kind_per_day(env, priya):
    cid = a_contact(env)
    ids = {ask(priya, cid, "Do you sell hamster wheels?").json()["task"]["id"] for _ in range(3)}
    assert len(ids) == 1


def test_latest_text_from_the_client_is_used_when_nothing_is_typed(env, priya):
    cid = a_contact(env)
    approve(env, "Parking", "There is free parking behind the building.")
    with env.session() as s:
        s.add(Message(contact_id=cid, channel="sms", direction="in", sender_kind="client", body="Is there parking at the clinic?", delivery_state="received", created_at=timeutil.now_utc()))
        s.commit()
    r = ask(priya, cid).json()
    assert r["kind"] == "answer" and "free parking" in r["draft"]


def test_answer_sheet_loads_as_pending_only_and_twice_is_harmless(env, garg):
    with env.session() as s:
        first = answer_sheet.load(s)
        s.commit()
        again = answer_sheet.load(s)
        s.commit()
    assert first["added"] >= 10 and again["added"] == 0
    with env.session() as s:
        rows = s.scalars(select(KnowledgeEntry).where(KnowledgeEntry.source_kind == "answer_sheet", KnowledgeEntry.is_demo.is_(False))).all()
        assert rows and all(r.status == "pending" for r in rows)
        assert all("heard" not in r.body.lower() for r in rows)  # the caveat is in the source fields, never in client text
    assert garg.get("/api/knowledge?status=approved&q=Opening hours").json()["items"] == []
