"""Automations: ideas to start from, reports, schedules, events from anywhere in the app, and the delivery rules."""
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import Automation, ChatMessage, Contact, Task
from app.services import automations as A
from app.services import calls_live, timeutil


def make(garg, **kw):
    body = {"name": "Test", "trigger_type": "schedule", "trigger": {"days": A.DAYS, "time": "07:45"}, "action": "report",
            "config": {"report": "morning", "channel": "chat", "to": "everyone"}}
    body.update(kw)
    r = garg.post("/api/automations", body)
    assert r.status_code == 201, r.text
    return r.json()["item"]


def test_ideas_are_offered_and_only_owners_build_automations(env, garg, priya, anita):
    d = garg.get("/api/automations").json()
    assert {t["key"] for t in d["templates"]} >= {"morning_sms", "missed_call_text_back", "booking_to_desk"} and d["can_edit"]
    assert priya.get("/api/automations").json()["can_edit"] is False
    assert priya.post("/api/automations", {"name": "x", "trigger_type": "schedule", "trigger": {}, "action": "report"}).status_code == 403
    assert anita.get("/api/automations").status_code == 403


def test_a_report_runs_now_into_chats(env, garg):
    a = make(garg)
    r = garg.post(f"/api/automations/{a['id']}/run", {}).json()
    assert r["ok"] and "Chats" in r["result"]
    with env.session() as s:
        body = s.scalar(select(ChatMessage.body).order_by(ChatMessage.id.desc()).limit(1))
        assert body.startswith("Morning report") and "Waiting for approval" in body
    assert garg.get("/api/automations/preview/weekly").json()["title"].startswith("Weekly summary")


def test_sms_reports_respect_the_test_list_and_email_needs_setup(env, garg):
    a = make(garg, config={"report": "end_of_day", "channel": "sms", "to": "+16045550999"})
    r = garg.post(f"/api/automations/{a['id']}/run", {}).json()
    assert not r["ok"] and "test list" in r["result"]
    b = make(garg, config={"report": "weekly", "channel": "email", "to": "dr@example.com"})
    r = garg.post(f"/api/automations/{b['id']}/run", {}).json()
    assert not r["ok"] and "Email is not set up" in r["result"]


def test_the_schedule_runs_once_per_due_time():
    a = Automation(name="x", trigger_type="schedule", trigger_json='{"days": ["mon"], "time": "07:45"}', action="report", action_json="{}")
    mon = datetime(2026, 11, 2, 7, 50, tzinfo=timeutil.local(timeutil.now_utc()).tzinfo)
    assert A.due(a, mon) and not A.due(a, mon - timedelta(minutes=10)) and not A.due(a, mon + timedelta(hours=1))
    a.last_run_at = mon.astimezone(timeutil.now_utc().tzinfo)
    assert not A.due(a, mon + timedelta(minutes=2))
    assert not A.due(a, mon + timedelta(days=1))  # Tuesday is not picked


def test_a_missed_call_starts_its_automations_and_texting_needs_opt_in(env, garg):
    make(garg, name="Tell everyone", trigger_type="event", trigger={"event": "missed_call"}, action="chat", config={"to": "everyone", "text": "Heads up: {details}"})
    make(garg, name="Text back", trigger_type="event", trigger={"event": "missed_call"}, action="text_client", config={"text": "Hi {first_name}, sorry we missed you."})
    with env.session() as s:
        c = s.scalar(select(Contact).where(Contact.is_demo.is_(True)).order_by(Contact.id).limit(1))
        c.phone, c.sms_consent = "+16045550777", "none"
        s.commit()
        calls_live.ingest(s, {"v": 1, "calls": [{"id": "am1", "at": timeutil.iso(timeutil.now_utc() - timedelta(minutes=3)), "d": "inbound", "r": "missed", "s": 0,
                                                  "n": "+16045550777", "rec": None}]})
    with env.session() as s:
        assert s.scalar(select(ChatMessage).where(ChatMessage.body.like("Heads up: Missed call from%"))) is not None
        autos = {a.name: a for a in s.scalars(select(Automation)).all()}
        assert autos["Tell everyone"].last_ok is True and autos["Text back"].last_ok is False and "opted in" in autos["Text back"].last_result


def test_automations_never_trigger_each_other(env, garg):
    make(garg, name="Urgent to task", trigger_type="event", trigger={"event": "urgent_task"}, action="task", config={"title": "Look at it", "priority": "high"})
    with env.session() as s:
        s.add(Task(kind="other", classification="inquiry", title="Fire", priority="high", status="open", raised_by="human", created_at=timeutil.now_utc()))
        s.commit()
    with env.session() as s:
        assert len(s.scalars(select(Task).where(Task.title == "Look at it")).all()) == 1


def test_the_owner_briefing_lists_items(env, garg):
    from app.models import CalendarEvent, PhoneCall
    with env.session() as s:
        c = s.scalar(select(Contact).where(Contact.is_demo.is_(True)).order_by(Contact.id).limit(1))
        y = timeutil.now_utc() - timedelta(days=1)
        s.add(CalendarEvent(kind="checkup", classification="patient", title=f"Check-up: {c.display_name} (agreed on a call)", contact_id=c.id,
                            starts_at=timeutil.now_utc() + timedelta(days=2), ends_at=timeutil.now_utc() + timedelta(days=2, minutes=30), status="requested", source="app"))
        s.add(PhoneCall(started_at=timeutil.at_local(timeutil.today_local() - timedelta(days=1), 10, 5), direction="inbound", result="voicemail", source="telus",
                        handled_by="none", contact_id=c.id, phone="+16045550123", external_ref="tel-brief1"))
        s.add(Task(kind="callback", classification="inquiry", title="Call back the Shahs", priority="high", status="open", raised_by="rule", created_at=timeutil.now_utc()))
        s.commit()
        name = c.display_name
    p = garg.get("/api/automations/preview/owner_brief").json()
    assert p["title"].startswith("Good morning")
    t = p["text"]
    assert "NEEDS YOUR APPROVAL" in t and name in t and "(by call)" in t and "10:05 AM" in t and "(voicemail)" in t and "URGENT Call back the Shahs" in t and "TODAY" in t
    assert any(x["key"] == "owner_brief_sms" for x in garg.get("/api/automations").json()["templates"])
