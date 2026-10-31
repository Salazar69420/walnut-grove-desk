"""Real texting from the app: practice by default, test numbers only, live with a typed confirmation. The send helper is simulated."""
from __future__ import annotations

import dataclasses
import json
import os
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import config
from app.models import Contact, Message, Outbox
from app.services import timeutil

TEST = "+16045550161"
OTHER = "+16045550177"


@pytest.fixture()
def bridge(env, tmp_path):
    d = tmp_path / "bridge"
    (d / "requests").mkdir(parents=True)
    (d / "send" / "requests").mkdir(parents=True)
    (d / "send" / "results").mkdir(parents=True)
    config.set_settings(dataclasses.replace(env.settings, telus_dir=str(d)))
    yield d
    config.set_settings(env.settings)


def two_contacts(env):
    with env.session() as s:
        a, b = s.scalars(select(Contact).where(Contact.is_demo.is_(True)).order_by(Contact.id).limit(2)).all()
        a.phone, a.sms_consent = TEST, "full"
        b.phone, b.sms_consent = OTHER, "full"
        s.commit()
        return a.id, b.id


def requests(bridge):
    return sorted(os.listdir(bridge / "send" / "requests"))


def text(who, cid, body="Hello from the clinic", **kw):
    return who.post(f"/api/contacts/{cid}/messages", {"channel": "sms", "body": body, **kw})


def set_mode(who, mode, numbers=(), confirm=None):
    return who.put("/api/admin/sms", {"mode": mode, "test_numbers": list(numbers), **({"confirm": confirm} if confirm else {})})


def test_practice_is_the_default_and_sends_nothing(env, bridge, garg):
    a, _ = two_contacts(env)
    assert garg.get("/api/admin/sms").json()["mode"] == "practice"
    assert garg.get("/api/auth/me").json()["settings"]["sms_mode"] == "practice"
    r = text(garg, a)
    assert r.status_code in (200, 201)
    assert requests(bridge) == []
    with env.session() as s:
        m = s.scalar(select(Message).where(Message.contact_id == a).order_by(Message.id.desc()))
        assert m.delivery_state == "dry_run" and m.send_job_id is None


def test_only_admins_change_the_mode_and_live_needs_the_phrase(env, bridge, priya, garg):
    assert set_mode(priya, "test_numbers_only", [TEST]).status_code == 403
    assert set_mode(garg, "test_numbers_only", []).status_code == 400  # a test mode with no test number
    assert set_mode(garg, "live").status_code == 400
    assert set_mode(garg, "live", confirm="yes").status_code == 400
    r = set_mode(garg, "test_numbers_only", ["604 555 0161"])
    assert r.status_code == 200 and r.json()["test_numbers"] == [TEST]  # normalised
    cfg = json.loads((bridge / "send_config.json").read_text())
    assert cfg["mode"] == "test_numbers_only" and cfg["test_numbers"] == [TEST]  # the helper's own copy


def test_test_mode_texts_the_test_number_for_real_and_everyone_else_in_practice(env, bridge, garg):
    a, b = two_contacts(env)
    set_mode(garg, "test_numbers_only", [TEST])
    text(garg, a, "Real hello")
    text(garg, b, "Practice hello")
    files = requests(bridge)
    assert len(files) == 1
    req = json.loads((bridge / "send" / "requests" / files[0]).read_text())
    assert req["kind"] == "sms" and req["to"] == TEST and req["text"] == "Real hello" and "from_number" not in req
    with env.session() as s:
        ma = s.scalar(select(Message).where(Message.contact_id == a).order_by(Message.id.desc()))
        mb = s.scalar(select(Message).where(Message.contact_id == b).order_by(Message.id.desc()))
        assert ma.delivery_state == "queued" and ma.send_job_id == files[0][:-5]
        assert mb.delivery_state == "dry_run"
        assert s.scalar(select(Outbox.status).where(Outbox.source_id == str(ma.id))) == "queued"


def test_consent_is_still_required_in_live_mode(env, bridge, garg):
    a, _ = two_contacts(env)
    with env.session() as s:
        s.get(Contact, a).sms_consent = "none"
        s.commit()
    set_mode(garg, "live", confirm="SEND REAL TEXTS")
    assert text(garg, a).status_code == 409 and requests(bridge) == []


def test_live_mode_texts_anyone_with_consent(env, bridge, garg):
    _, b = two_contacts(env)
    set_mode(garg, "live", confirm="SEND REAL TEXTS")
    text(garg, b)
    assert len(requests(bridge)) == 1


def test_scheduled_or_attached_texts_are_refused_when_real(env, bridge, garg):
    a, _ = two_contacts(env)
    set_mode(garg, "test_numbers_only", [TEST])
    later = (timeutil.now_utc() + timedelta(hours=2)).isoformat()
    assert text(garg, a, scheduled_for=later).status_code == 409
    assert requests(bridge) == []


def test_without_the_helper_nothing_is_saved_and_the_mode_cannot_be_turned_on(env, bridge, garg):
    a, _ = two_contacts(env)
    os.rmdir(bridge / "send" / "requests")
    assert set_mode(garg, "test_numbers_only", [TEST]).status_code == 409
    # if the helper disappears after the mode was set, a send fails cleanly and leaves no message behind
    (bridge / "send" / "requests").mkdir()
    set_mode(garg, "test_numbers_only", [TEST])
    os.rmdir(bridge / "send" / "requests")
    with env.session() as s:
        before = len(s.scalars(select(Message).where(Message.contact_id == a)).all())
    assert text(garg, a).status_code == 503
    with env.session() as s:
        assert len(s.scalars(select(Message).where(Message.contact_id == a)).all()) == before


def _result(bridge, job, state, code):
    (bridge / "send" / "results" / f"{job}.json").write_text(json.dumps({"v": 1, "id": job, "state": state, "code": code, "telus_status": None, "at": "x"}))


def test_results_from_the_helper_reach_the_message(env, bridge, garg):
    a, _ = two_contacts(env)
    set_mode(garg, "test_numbers_only", [TEST])
    text(garg, a, "one")
    text(garg, a, "two")
    j1, j2 = [f[:-5] for f in requests(bridge)]
    with env.session() as s:
        ids = {m.send_job_id: m.id for m in s.scalars(select(Message).where(Message.send_job_id.is_not(None))).all()}
    assert garg.get(f"/api/messages/{ids[j1]}/delivery").json()["state"] == "queued"
    _result(bridge, j1, "sent", "sent")
    _result(bridge, j2, "failed", "no_sms_number")
    assert garg.get(f"/api/messages/{ids[j1]}/delivery").json() == {"state": "sent", "code": None, "text": None}
    d = garg.get(f"/api/messages/{ids[j2]}/delivery").json()
    assert d["state"] == "failed" and d["code"] == "no_sms_number" and "text-capable number" in d["text"]
    with env.session() as s:
        assert s.scalar(select(Outbox.status).where(Outbox.source_id == str(ids[j1]))) == "sent"


def test_the_timeline_picks_up_results_and_a_silent_helper_times_out(env, bridge, garg):
    a, _ = two_contacts(env)
    set_mode(garg, "test_numbers_only", [TEST])
    text(garg, a, "hello")
    job = requests(bridge)[0][:-5]
    _result(bridge, job, "sent", "sent")
    items = garg.get(f"/api/contacts/{a}/timeline?channel=sms").json()["items"]
    assert items[0]["delivery_state"] == "sent"
    text(garg, a, "nobody answers this one")
    with env.session() as s:
        m = s.scalar(select(Message).where(Message.contact_id == a, Message.delivery_state == "queued"))
        m.created_at = timeutil.now_utc() - timedelta(minutes=10)
        s.commit()
        mid = m.id
    d = garg.get(f"/api/messages/{mid}/delivery").json()
    assert d["state"] == "failed" and d["code"] == "sender_not_running"


def test_campaigns_never_send_for_real_even_in_live_mode(env, bridge, garg, priya, monkeypatch):
    monkeypatch.setattr("app.services.campaigns.in_quiet_hours", lambda now=None: False)
    two_contacts(env)
    set_mode(garg, "live", confirm="SEND REAL TEXTS")
    cid = priya.post("/api/campaigns", {"name": "Live test", "channel": "sms", "purpose": "care", "segment": {}, "subject": "", "body": "Hi {first_name}"}).json()["item"]["id"]
    priya.post(f"/api/campaigns/{cid}/submit")
    garg.post(f"/api/campaigns/{cid}/approve")
    assert garg.post(f"/api/campaigns/{cid}/run").json()["counts"]["sent"] > 0
    assert requests(bridge) == []
