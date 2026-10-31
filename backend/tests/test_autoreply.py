"""Reply syncing, automatic replies (test numbers only) and corrections that teach the assistant."""
from __future__ import annotations

import dataclasses
import json
import os

import pytest
from sqlalchemy import select

from app import config
from app.models import Contact, KnowledgeEntry, Message, Task
from app.services import inbound

TEST = "+16045550161"
OTHER = "+16045550177"
NEW = "+17785550188"


@pytest.fixture()
def bridge(env, tmp_path):
    d = tmp_path / "bridge"
    for sub in ("requests", "send/requests", "send/results", "send/inbound"):
        (d / sub).mkdir(parents=True)
    config.set_settings(dataclasses.replace(env.settings, telus_dir=str(d)))
    yield d
    config.set_settings(env.settings)


def setup(env, garg, auto=True, ai=True):
    with env.session() as s:
        a, b = s.scalars(select(Contact).where(Contact.is_demo.is_(True)).order_by(Contact.id).limit(2)).all()
        a.phone, a.sms_consent, a.ai_enabled = TEST, "transactional", ai
        b.phone, b.sms_consent, b.ai_enabled = OTHER, "full", True
        s.commit()
        ids = a.id, b.id
    r = garg.put("/api/admin/sms", {"mode": "test_numbers_only", "test_numbers": [TEST], "auto_reply": auto})
    assert r.status_code == 200 and r.json()["auto_reply"] is auto
    return ids


def text_in(bridge, mid, frm, text):
    (bridge / "send" / "inbound" / f"{mid}.json").write_text(json.dumps({"v": 1, "id": str(mid), "from": frm, "text": text, "at": "2026-10-31T12:00:00.000Z"}))


def run(env):
    with env.session() as s:
        return inbound.ingest(s)


def sent(bridge):
    return [json.loads((bridge / "send" / "requests" / f).read_text()) for f in sorted(os.listdir(bridge / "send" / "requests"))]


def approve(env, title, body, cat="patient_communication"):
    with env.session() as s:
        s.add(KnowledgeEntry(category=cat, title=title, body=body, status="approved", proposed_by="human"))
        s.commit()


def test_incoming_texts_land_in_the_conversation_once(env, bridge, garg):
    a, _ = setup(env, garg, auto=False)
    text_in(bridge, 1, TEST, "Hello there")
    text_in(bridge, 1, TEST, "Hello there")  # same TELUS id again (same file name): only one file
    assert run(env)["stored"] == 1
    text_in(bridge, 1, TEST, "Hello there")
    assert run(env) == {"stored": 0, "duplicate": 1, "bad": 0, "replied": 0}
    assert os.listdir(bridge / "send" / "inbound") == []
    with env.session() as s:
        m = s.scalar(select(Message).where(Message.contact_id == a, Message.direction == "in").order_by(Message.id.desc()))
        assert m.body == "Hello there" and m.channel == "sms" and m.delivery_state == "received"
    assert sent(bridge) == []  # auto reply is off


def test_an_unknown_number_becomes_an_inquiry_that_is_not_opted_in(env, bridge, garg):
    setup(env, garg)
    text_in(bridge, 2, NEW, "Do you take new patients?")
    r = run(env)
    assert r["stored"] == 1 and r["replied"] == 0 and sent(bridge) == []  # not a test number: the opt-in question is practice only
    with env.session() as s:
        c = s.scalar(select(Contact).where(Contact.phone == NEW))
        assert c.kind == "inquiry" and c.sms_consent == "none" and c.ai_enabled is False
        outs = s.scalars(select(Message).where(Message.contact_id == c.id, Message.direction == "out")).all()
        assert [m.delivery_state for m in outs] == ["dry_run"] and "reply Y" in outs[0].body


def test_the_test_number_gets_an_automatic_reply_from_approved_knowledge(env, bridge, garg):
    setup(env, garg)
    approve(env, "Lab results", "Results often arrive overnight. The doctor reviews them and then calls you.")
    text_in(bridge, 3, TEST, "Are my lab results in yet?")
    assert run(env)["replied"] == 1
    (req,) = sent(bridge)
    assert req["to"] == TEST and "Results often arrive overnight." in req["text"] and "automated assistant)" not in req["text"]
    with env.session() as s:
        m = s.scalar(select(Message).where(Message.send_job_id == req["id"]))
        assert m.delivery_state == "queued" and m.sender_kind == "ai" and m.direction == "out"


def test_no_automatic_reply_for_anyone_else_even_in_live_mode(env, bridge, garg):
    setup(env, garg)
    garg.put("/api/admin/sms", {"mode": "live", "test_numbers": [TEST], "confirm": "SEND REAL TEXTS", "auto_reply": True})
    text_in(bridge, 4, OTHER, "Are you open today?")
    assert run(env)["replied"] == 0 and sent(bridge) == []


def test_assistant_switched_off_for_the_person_means_no_reply(env, bridge, garg):
    setup(env, garg, ai=False)
    text_in(bridge, 5, TEST, "Hello")
    assert run(env)["replied"] == 0 and sent(bridge) == []


def test_an_emergency_gets_the_emergency_reply_and_a_high_task(env, bridge, garg):
    setup(env, garg)
    text_in(bridge, 6, TEST, "My dog ate rat poison")
    run(env)
    (req,) = sent(bridge)
    assert "emergency veterinarian" in req["text"]
    with env.session() as s:
        assert s.scalar(select(Task).where(Task.kind == "emergency_followup", Task.raised_by == "ai")).priority == "high"


def test_a_medical_question_gets_no_advice(env, bridge, garg):
    setup(env, garg)
    text_in(bridge, 7, TEST, "What dose of gabapentin should I give?")
    run(env)
    (req,) = sent(bridge)
    assert "cannot give medical advice" in req["text"] and "gabapentin" not in req["text"].lower()


def test_stop_unsubscribes_and_nothing_more_is_sent(env, bridge, garg):
    a, _ = setup(env, garg)
    text_in(bridge, 8, TEST, "STOP")
    run(env)
    text_in(bridge, 9, TEST, "Are you open?")
    run(env)
    assert sent(bridge) == []
    with env.session() as s:
        assert s.get(Contact, a).sms_consent == "unsubscribed"


@pytest.mark.parametrize("bad", [{"from": "12345"}, {"text": ""}, {"id": "../x"}, {"v": 2}])
def test_bad_files_are_dropped(env, bridge, garg, bad):
    setup(env, garg)
    body = {"v": 1, "id": "10", "from": TEST, "text": "hi", "at": "x"}
    body.update(bad)
    (bridge / "send" / "inbound" / "10.json").write_text(json.dumps(body))
    assert run(env)["bad"] == 1 and os.listdir(bridge / "send" / "inbound") == [] and sent(bridge) == []


def test_at_most_six_automatic_replies_an_hour(env, bridge, garg):
    setup(env, garg)
    for i in range(8):
        text_in(bridge, 100 + i, TEST, f"Question number {i}")
    assert run(env)["replied"] == 6


def test_a_correction_teaches_the_assistant_once(env, bridge, garg, priya):
    setup(env, garg)
    text_in(bridge, 20, TEST, "Do you trim bird nails?")
    run(env)
    (first,) = sent(bridge)
    assert "get back to you" in first["text"]  # it did not know
    with env.session() as s:
        reply_id = s.scalar(select(Message.id).where(Message.send_job_id == first["id"]))
    # the desk can suggest; it waits for the owner
    r = priya.post(f"/api/messages/{reply_id}/correct", {"answer": "Yes, we trim bird nails. Book a short nail visit.", "category": "clinic_info"})
    assert r.status_code == 200 and r.json()["item"]["status"] == "pending"
    # the owner's correction is approved at once and used next time
    r = garg.post(f"/api/messages/{reply_id}/correct", {"answer": "Yes, we trim bird nails. A short visit is enough.", "title": "Bird nail trims", "category": "clinic_info"})
    assert r.json()["item"]["status"] == "approved" and r.json()["item"]["source_quote"] == "Do you trim bird nails?"
    before = {r["id"] for r in sent(bridge)}
    text_in(bridge, 21, TEST, "Can you trim my bird's nails?")
    run(env)
    (new,) = [r for r in sent(bridge) if r["id"] not in before]
    assert "Yes, we trim bird nails. A short visit is enough." in new["text"]


def test_only_assistant_replies_can_be_corrected(env, bridge, garg):
    a, _ = setup(env, garg)
    with env.session() as s:
        mid = s.scalar(select(Message.id).where(Message.contact_id == a).limit(1))
    assert garg.post(f"/api/messages/{mid}/correct", {"answer": "x y z"}).status_code in (400, 404)


# ---------------------------------------------------------------- opt-in by text
def not_opted_in(env, garg):
    a, _ = setup(env, garg)
    with env.session() as s:
        c = s.get(Contact, a)
        c.sms_consent, c.display_name = "none", "Kartik Garg"
        s.commit()
    approve(env, "Lab results", "Results often arrive overnight. The doctor reviews them and then calls you.")
    return a


def test_a_person_who_is_not_opted_in_only_gets_the_opt_in_question(env, bridge, garg):
    a = not_opted_in(env, garg)
    text_in(bridge, 20, TEST, "Are my lab results in yet?")
    r = run(env)
    assert r["optin_asked"] == 1 and r["replied"] == 0
    (req,) = sent(bridge)
    assert "reply Y" in req["text"] and "Results" not in req["text"]
    text_in(bridge, 21, TEST, "Hello?")
    run(env)
    assert len(sent(bridge)) == 1  # asked once a day at most
    with env.session() as s:
        assert s.get(Contact, a).sms_consent == "none"


def test_replying_y_opts_in_and_the_waiting_question_is_answered(env, bridge, garg):
    a = not_opted_in(env, garg)
    text_in(bridge, 22, TEST, "Are my lab results in yet?")
    run(env)
    text_in(bridge, 23, TEST, "Y")
    r = run(env)
    assert r["opted_in"] == 1 and r["replied"] == 1
    texts = [x["text"] for x in sent(bridge)]
    assert len(texts) == 3 and any("all set" in t for t in texts) and any("Results often arrive overnight." in t for t in texts)
    with env.session() as s:
        c = s.get(Contact, a)
        assert c.sms_consent == "transactional" and c.consent_source.startswith("Replied Y by text")


def test_a_yes_from_someone_never_asked_is_not_an_opt_in(env, bridge, garg):
    a = not_opted_in(env, garg)
    text_in(bridge, 24, TEST, "ok")
    r = run(env)
    assert r.get("opted_in") is None and r["optin_asked"] == 1
    with env.session() as s:
        assert s.get(Contact, a).sms_consent == "none"


def test_staff_cannot_text_someone_who_is_not_opted_in(env, bridge, garg):
    a = not_opted_in(env, garg)
    r = garg.post(f"/api/contacts/{a}/messages", {"channel": "sms", "body": "Hi there"})
    assert r.status_code == 409
