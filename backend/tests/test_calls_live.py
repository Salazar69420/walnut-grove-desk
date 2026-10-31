"""Live calls from TELUS land in the Phone log once, linked to the one contact with that number."""
from __future__ import annotations

from sqlalchemy import select

from app.models import Contact, PhoneCall
from app.services import calls_live


def test_new_calls_are_added_once_and_linked(env):
    with env.session() as s:
        c = s.scalar(select(Contact).where(Contact.is_demo.is_(True)).order_by(Contact.id).limit(1))
        c.phone = "+16045550161"
        s.add(PhoneCall(started_at=c.created_at, direction="inbound", source="import", external_ref="imp-x", recording_id="777", is_demo=False))
        s.commit()
        cid = c.id
    data = {"v": 1, "calls": [
        {"id": "a1", "at": "2026-10-31T17:00:00+00:00", "d": "inbound", "r": "answered", "s": 95, "n": "+16045550161", "rec": None},
        {"id": "a2", "at": "2026-10-31T17:10:00+00:00", "d": "inbound", "r": "missed", "s": 0, "n": "+17785550188", "rec": None},
        {"id": "a3", "at": "2026-10-31T16:00:00+00:00", "d": "outbound", "r": "answered", "s": 60, "n": "+16045550199", "rec": "777"},
        {"id": "../bad", "at": "x"},
    ]}
    with env.session() as s:
        r = calls_live.ingest(s, data)
        assert (r["added"], r["numbers_added"]) == (2, 1)
    with env.session() as s:
        assert calls_live.ingest(s, data) == {"added": 0, "numbers_added": 0, "linked": 0, "callbacks": 0}
        a1 = s.scalar(select(PhoneCall).where(PhoneCall.external_ref == "tel-a1"))
        a2 = s.scalar(select(PhoneCall).where(PhoneCall.external_ref == "tel-a2"))
        assert a1.contact_id == cid and a1.source == "telus" and a1.duration_seconds == 95
        assert a2.contact_id is None and a2.handled_by == "none" and a2.result == "missed"
        assert s.scalar(select(PhoneCall).where(PhoneCall.recording_id == "777")).phone == "+16045550199"
        s.add(Contact(display_name="New Person", phone="+17785550188", kind="inquiry"))
        s.commit()
        assert calls_live.relink(s) >= 1
        s.commit()
        assert s.scalar(select(PhoneCall).where(PhoneCall.external_ref == "tel-a2")).contact_id is not None


def test_the_badge_says_why_it_is_not_connected(env, garg, tmp_path):
    import dataclasses
    import json
    from app import config
    config.set_settings(dataclasses.replace(env.settings, telus_dir=str(tmp_path)))
    try:
        assert garg.get("/api/phone/live").json()["state"] == "off"
        (tmp_path / "calls").mkdir()
        (tmp_path / "calls" / "status.json").write_text(json.dumps({"v": 1, "ok": False, "code": "write_scopes", "at": "2026-10-31T10:00:00+00:00"}))
        r = garg.get("/api/phone/live").json()
        assert r["state"] == "off" and "Read permissions" in r["reason"]
        from app.services import timeutil
        (tmp_path / "calls" / "status.json").write_text(json.dumps({"v": 1, "ok": True, "code": "ok", "at": timeutil.iso(timeutil.now_utc())}))
        assert garg.get("/api/phone/live").json()["state"] == "live"
    finally:
        config.set_settings(env.settings)


def test_a_missed_call_raises_one_callback_task_and_an_answered_call_closes_it(env):
    from app.models import Message, Task
    from app.services import timeutil
    now = timeutil.now_utc()
    iso = lambda m: timeutil.iso(now - __import__("datetime").timedelta(minutes=m))
    with env.session() as s:
        c = s.scalar(select(Contact).where(Contact.is_demo.is_(True)).order_by(Contact.id).limit(1))
        c.phone = "+16045550123"
        s.commit()
        cid = c.id
    with env.session() as s:
        calls_live.ingest(s, {"v": 1, "calls": [
            {"id": "m1", "at": iso(30), "d": "inbound", "r": "missed", "s": 0, "n": "+16045550123", "rec": None},
            {"id": "m2", "at": iso(20), "d": "inbound", "r": "voicemail", "s": 30, "n": "+16045550123", "rec": "55"},
        ]})
        (t,) = s.scalars(select(Task).where(Task.kind == "callback", Task.contact_id == cid)).all()
        assert t.status == "open" and "voicemail" in t.reason
        assert len(s.scalars(select(Message).where(Message.contact_id == cid, Message.channel == "call")).all()) >= 2  # shows in the conversation
    with env.session() as s:
        calls_live.ingest(s, {"v": 1, "calls": [{"id": "m3", "at": iso(5), "d": "outbound", "r": "answered", "s": 90, "n": "+16045550123", "rec": None}]})
        assert s.scalar(select(Task).where(Task.kind == "callback", Task.contact_id == cid)).status == "done"


def test_any_staff_contact_resolves_the_earlier_tasks(env, garg):
    from datetime import timedelta
    from app.models import Task
    from app.services import timeutil
    now = timeutil.now_utc()
    with env.session() as s:
        c = s.scalar(select(Contact).where(Contact.is_demo.is_(True)).order_by(Contact.id).limit(1))
        c.phone, c.sms_consent = "+16045550133", "full"
        s.query(Task).filter(Task.contact_id == c.id).delete()
        old = Task(kind="unknown_knowledge", title="No answer", contact_id=c.id, status="open", raised_by="ai", created_at=now - timedelta(hours=2))
        book = Task(kind="booking", title="Confirm text booking", contact_id=c.id, status="open", raised_by="ai", created_at=now - timedelta(hours=2))
        s.add_all([old, book])
        s.commit()
        cid, oid, bid = c.id, old.id, book.id
    assert garg.post(f"/api/contacts/{cid}/messages", {"channel": "email", "body": "Hi, here is the answer."}).status_code in (201, 409)
    with env.session() as s:
        t = s.get(Task, oid)
        if t.status != "done":  # no email on file: use a call instead
            calls_live.ingest(s, {"v": 1, "calls": [{"id": "r1", "at": timeutil.iso(now - timedelta(minutes=5)), "d": "outbound", "r": "voicemail", "s": 25,
                                                      "n": "+16045550133", "rec": None}]})
            s.expire_all()
            t = s.get(Task, oid)
        assert t.status == "done" and "Resolved automatically" in t.reason
        assert s.get(Task, bid).status == "open"  # bookings close when the appointment is approved


def test_a_staff_text_resolves_and_a_later_task_stays(env, garg, tmp_path):
    from datetime import timedelta
    from app.models import Task
    from app.services import timeutil
    now = timeutil.now_utc()
    with env.session() as s:
        c = s.scalar(select(Contact).where(Contact.is_demo.is_(True)).order_by(Contact.id.desc()).limit(1))
        c.phone, c.sms_consent = "+16045550144", "full"
        s.query(Task).filter(Task.contact_id == c.id).delete()
        s.add(Task(kind="callback", title="Call back", contact_id=c.id, status="open", raised_by="rule", created_at=now - timedelta(hours=1)))
        s.add(Task(kind="callback", title="Later", contact_id=c.id, status="open", raised_by="rule", created_at=now + timedelta(minutes=5)))
        s.commit()
        cid = c.id
    assert garg.post(f"/api/contacts/{cid}/messages", {"channel": "sms", "body": "Hi, calling you back now."}).status_code == 201
    with env.session() as s:
        states = {t.title: t.status for t in s.scalars(select(Task).where(Task.contact_id == cid)).all()}
        assert states == {"Call back": "done", "Later": "open"}
