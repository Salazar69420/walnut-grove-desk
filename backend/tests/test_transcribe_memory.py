"""New recorded calls are transcribed and filed (tasks, reports); the assistant remembers older conversations."""
from __future__ import annotations

import dataclasses
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app import config
from app.adapters import llm as llm_adapter
from app.assistant import ai, memory
from app.models import Contact, Message, PhoneCall, Task
from app.services import calls_live, timeutil, transcribe


class Router:
    def __init__(self):
        self.calls: list[dict] = []
        self.analysis = {"summary": "Sam called about a refill of Rex's food. Wants a call back.", "intent": "refill", "outcome": "follow_up_needed",
                         "follow_up_needed": True, "follow_up_note": "Call Sam to confirm the food is ready."}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        if body["model"] == ai.TRANSCRIBE_MODEL:
            content = "Speaker 1: Walnut Grove, how can I help?\nSpeaker 2: Hi, I need more food for Rex."
        elif "client_memory" in json.dumps(body.get("response_format", {})):
            content = json.dumps({"memory": "- Client has a dog called Rex\n- Prefers morning appointments"})
        else:
            content = json.dumps(self.analysis)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.fixture()
def router(monkeypatch):
    r = Router()
    monkeypatch.setattr(llm_adapter, "TRANSPORT", httpx.MockTransport(r.handler))
    return r


@pytest.fixture()
def bridge(env, tmp_path, garg):
    d = tmp_path / "bridge"
    for sub in ("requests", "play", "send/requests", "send/results", "send/inbound"):
        (d / sub).mkdir(parents=True)
    config.set_settings(dataclasses.replace(env.settings, telus_dir=str(d)))
    (d / "settings.json").write_text(json.dumps({"v": 1, "recordings_enabled": True}))
    assert garg.put("/api/admin/assistant", {"api_key": "sk-or-v1-testkey-1234"}).status_code == 200
    yield d
    config.set_settings(env.settings)


def _voicemail(env):
    now = timeutil.now_utc()
    with env.session() as s:
        c = s.scalar(select(Contact).where(Contact.is_demo.is_(True)).order_by(Contact.id).limit(1))
        c.phone = "+16045550123"
        s.commit()
        calls_live.ingest(s, {"v": 1, "calls": [{"id": "v1", "at": timeutil.iso(now - timedelta(minutes=10)), "d": "inbound", "r": "voicemail", "s": 40,
                                                  "n": "+16045550123", "rec": "4242"}]})
        return c.id, s.scalar(select(PhoneCall.id).where(PhoneCall.external_ref == "tel-v1"))


def test_a_voicemail_is_transcribed_and_its_task_learns_what_it_was_about(env, bridge, router, monkeypatch):
    from app.api import admin_telus
    monkeypatch.setattr(admin_telus, "read_settings", lambda: {"recordings_enabled": True})
    cid, pid = _voicemail(env)
    with env.session() as s:
        assert transcribe.step(s)["asked"] == 1
    assert len(list((bridge / "requests").iterdir())) == 1  # the helper was asked for a copy, same as the play button
    (bridge / "play" / "4242.mp3").write_bytes(b"ID3fakeaudio")
    (bridge / "play" / "4242.json").write_text(json.dumps({"state": "ready", "ext": "mp3"}))
    with env.session() as s:
        assert transcribe.step(s)["done"] == 1
    with env.session() as s:
        p = s.get(PhoneCall, pid)
        assert p.transcript.startswith("Speaker 1:") and p.intent == "refill" and p.short_note == "Refill; follow-up needed" and "Rex" in p.summary
        t = s.scalar(select(Task).where(Task.kind == "callback", Task.contact_id == cid))
        assert "refill of Rex" in t.reason and t.title.endswith("refill")
        assert s.scalar(select(Message.body).where(Message.phone_call_id == pid)) == "Refill; follow-up needed"
    audio_call = next(c for c in router.calls if c["model"] == ai.TRANSCRIBE_MODEL)
    assert audio_call["messages"][0]["content"][1]["type"] == "input_audio" and audio_call["provider"]["data_collection"] == "deny"


def test_reasons_show_in_the_phone_report(env, bridge, router, garg, monkeypatch):
    with env.session() as s:
        s.add(PhoneCall(started_at=timeutil.now_utc(), direction="inbound", source="telus", intent="booking", transcript="x", short_note="Booking"))
        s.commit()
    d = garg.get("/api/phone/summary?days=7").json()
    assert any(x["label"] == "Booking" for x in d["by_intent"]) and d["transcribed"] >= 1


def test_the_assistant_remembers_older_texts(env, bridge, router):
    with env.session() as s:
        c = s.scalar(select(Contact).where(Contact.is_demo.is_(True)).order_by(Contact.id).limit(1))
        base = timeutil.now_utc() - timedelta(days=3)
        for i in range(memory.WINDOW + memory.REFRESH_EVERY + 2):
            s.add(Message(contact_id=c.id, channel="sms", direction="in" if i % 2 else "out", sender_kind="client" if i % 2 else "staff",
                          body=f"text {i}", delivery_state="received", created_at=base + timedelta(minutes=i)))
        s.commit()
        assert memory.refresh(s, c) is True
        s.commit()
        assert "Rex" in c.ai_memory
        msgs = ai.conversation(s, c, "Hi again")
        assert "WHAT YOU REMEMBER" in msgs[-1]["content"] and "Rex" in msgs[-1]["content"]
        assert len(msgs) <= ai.HISTORY + 1
