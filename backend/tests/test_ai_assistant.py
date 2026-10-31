"""The AI writes replies (OpenRouter, mocked here) only for the pilot contact, and every reply is checked before use."""
from __future__ import annotations

import dataclasses
import json

import httpx
import pytest
from sqlalchemy import select

from app import config
from app.adapters import llm as llm_adapter
from app.assistant import ai
from app.models import Contact, KnowledgeEntry, Task

PILOT = "+16045550161"
OTHER = "+16045550177"


class FakeRouter:
    def __init__(self, reply=None, status=200):
        self.reply = reply or {"reply": "We are open Saturday and Sunday from 9 am to 5 pm.", "answered": True, "needs_human": False, "knowledge_ids": [], "reason": "hours"}
        self.status = status
        self.calls: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [
                {"id": "deepseek/deepseek-v4.1-flash-1017", "name": "DeepSeek V4.1 Flash", "context_length": 1000, "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
                 "supported_parameters": ["structured_outputs", "response_format"], "architecture": {"output_modalities": ["text"]}},
                {"id": "x/no-json", "name": "No JSON", "pricing": {"prompt": "0", "completion": "0"}, "supported_parameters": ["temperature"]},
                {"id": "x/image", "name": "Image", "pricing": {"prompt": "0", "completion": "0"}, "supported_parameters": ["structured_outputs"], "architecture": {"output_modalities": ["image"]}},
            ]})
        self.calls.append({"body": json.loads(request.content), "auth": request.headers.get("authorization")})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"code": self.status, "message": "x"}})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": json.dumps(self.reply)}}],
                                         "usage": {"prompt_tokens": 900, "completion_tokens": 40, "cost": 0.0002}, "model": "deepseek/deepseek-v4.1-flash-1017"})


@pytest.fixture()
def router(monkeypatch):
    fake = FakeRouter()
    monkeypatch.setattr(llm_adapter, "TRANSPORT", httpx.MockTransport(fake.handler))
    ai._models_cache.update(at=0.0, items=[])
    return fake


@pytest.fixture()
def bridge(env, tmp_path):
    d = tmp_path / "bridge"
    for sub in ("requests", "send/requests", "send/results", "send/inbound"):
        (d / sub).mkdir(parents=True)
    config.set_settings(dataclasses.replace(env.settings, telus_dir=str(d)))
    yield d
    config.set_settings(env.settings)


def setup(env, garg, enabled=True):
    with env.session() as s:
        a, b = s.scalars(select(Contact).where(Contact.is_demo.is_(True)).order_by(Contact.id).limit(2)).all()
        a.phone, a.sms_consent, a.ai_enabled, a.display_name, a.email = PILOT, "full", True, "Kartik Garg", "kartik@example.com"
        b.phone, b.sms_consent, b.ai_enabled = OTHER, "full", True
        e = KnowledgeEntry(category="clinic_info", title="Weekend hours", body="We are open Saturday and Sunday from 9 am to 5 pm.", status="approved", proposed_by="human")
        s.add(e)
        s.commit()
        ids = a.id, b.id, e.id
    assert garg.put("/api/admin/sms", {"mode": "test_numbers_only", "test_numbers": [PILOT]}).status_code == 200
    r = garg.put("/api/admin/assistant", {"api_key": "sk-or-v1-testkey-1234", **({"enabled": True} if enabled else {})})
    assert r.status_code == 200, r.text
    return ids


def ask(who, cid, text):
    return who.post(f"/api/contacts/{cid}/suggest-reply", {"text": text}).json()


def test_settings_keep_the_key_secret_and_validate_the_model(env, bridge, garg, priya, router):
    assert garg.put("/api/admin/assistant", {"enabled": True}).status_code == 409  # no key yet
    r = garg.put("/api/admin/assistant", {"api_key": "sk-or-v1-abcdefghij1234"}).json()
    assert r["has_key"] and r["key_last4"] == "1234" and "sk-or" not in json.dumps(r) and r["model"] == ai.DEFAULT_MODEL
    assert garg.put("/api/admin/assistant", {"model": "not a model"}).status_code == 400
    assert garg.put("/api/admin/assistant", {"model": "openai/gpt-x"}).json()["model"] == "openai/gpt-x"
    assert priya.get("/api/admin/assistant").status_code == 403
    assert "Hard rules" in r["rules"] and r["system_prompt"] == ai.DEFAULT_PROMPT


def test_model_list_offers_only_json_capable_text_models(env, garg, router):
    items = garg.get("/api/admin/assistant/models").json()["items"]
    assert [i["id"] for i in items] == ["deepseek/deepseek-v4.1-flash-1017"] and items[0]["in_per_million"] == 0.15


def test_the_pilot_contact_gets_an_ai_reply_built_on_approved_knowledge(env, bridge, garg, router):
    a, _, kid = setup(env, garg)
    router.reply["knowledge_ids"] = [f"K{kid}"]
    r = ask(garg, a, "Are you open on the weekend?")
    assert r["kind"] == "answer" and r["draft"] == "We are open Saturday and Sunday from 9 am to 5 pm." and r["ai"] and r["model"] == ai.DEFAULT_MODEL
    assert r["sources"] == [{"id": kid, "title": "Weekend hours", "kind": "knowledge"}]
    body = router.calls[0]["body"]
    assert body["model"] == ai.DEFAULT_MODEL and body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["strict"] is True
    assert body["provider"] == {"require_parameters": True, "data_collection": "deny"}
    system = body["messages"][0]["content"]
    assert "Hard rules" in system and f"[K{kid}] Weekend hours" in system
    sent = json.dumps(body["messages"][1:])
    assert "Kartik" in sent and "Kartik Garg" not in sent and PILOT not in sent and "5550161" not in sent and "kartik@example.com" not in sent
    assert router.calls[0]["auth"] == "Bearer sk-or-v1-testkey-1234"


def test_nobody_else_ever_reaches_the_ai(env, bridge, garg, router):
    _, b, _ = setup(env, garg)
    r = ask(garg, b, "Are you open on the weekend?")
    assert router.calls == [] and not r.get("ai")


def test_switched_off_means_rules_only(env, bridge, garg, router):
    a, _, _ = setup(env, garg, enabled=False)
    ask(garg, a, "Are you open on the weekend?")
    assert router.calls == []


def test_emergencies_and_medical_questions_never_reach_the_ai(env, bridge, garg, router):
    a, _, _ = setup(env, garg)
    assert ask(garg, a, "My dog ate chocolate and is shaking")["kind"] == "emergency"
    assert ask(garg, a, "What dose of gabapentin should I give?")["kind"] == "medical"
    assert router.calls == []


@pytest.mark.parametrize("reply", [
    {"reply": "We are open Saturday and Sunday from 8 am to 9 pm.", "answered": True, "needs_human": False, "knowledge_ids": ["KID"], "reason": "x"},  # invented hours
    {"reply": "We are open on the weekend.", "answered": True, "needs_human": False, "knowledge_ids": ["K99999"], "reason": "x"},  # an id it was not given
    {"reply": "We are open on the weekend.", "answered": True, "needs_human": False, "knowledge_ids": [], "reason": "x"},  # claims an answer with no source
    {"reply": "Give him 50 mg of Benadryl, it is fine.", "answered": True, "needs_human": False, "knowledge_ids": ["KID"], "reason": "x"},  # medical
    {"reply": "", "answered": False, "needs_human": False, "knowledge_ids": [], "reason": "x"},
])
def test_unsafe_ai_replies_are_never_used(env, bridge, garg, router, reply):
    a, _, kid = setup(env, garg)
    router.reply = json.loads(json.dumps(reply).replace('"KID"', f'"K{kid}"'))
    r = ask(garg, a, "Are you open on the weekend?")
    assert not r.get("ai") and r["draft"] != reply["reply"]
    assert "We are open Saturday and Sunday from 9 am to 5 pm." in r["draft"]  # the rules answered from the same approved entry


@pytest.mark.parametrize("status", [401, 402, 429, 500])
def test_provider_errors_fall_back_to_the_rules(env, bridge, garg, router, status):
    a, _, _ = setup(env, garg)
    router.status = status
    r = ask(garg, a, "Are you open on the weekend?")
    assert r["kind"] == "answer" and not r.get("ai")


def test_when_the_ai_does_not_know_it_holds_and_raises_a_task(env, bridge, garg, router):
    a, _, _ = setup(env, garg)
    router.reply = {"reply": "Thanks Kartik, a member of our team will get back to you about grooming soon.", "answered": False, "needs_human": False, "knowledge_ids": [], "reason": "grooming not in knowledge"}
    r = ask(garg, a, "Do you do grooming?")
    assert r["kind"] == "unknown" and r["draft"].startswith("Thanks Kartik") and r["task"]
    with env.session() as s:
        t = s.get(Task, r["task"]["id"])
        assert t.kind == "unknown_knowledge" and "grooming not in knowledge" in t.reason


def test_the_prompt_grows_with_approved_knowledge(env, bridge, garg, router):
    setup(env, garg)
    before = garg.get("/api/admin/assistant/prompt").json()
    with env.session() as s:
        s.add(KnowledgeEntry(category="clinic_info", title="Parking", body="Free parking behind the building.", status="approved", proposed_by="human"))
        s.add(KnowledgeEntry(category="clinic_info", title="Secret", body="Not approved yet.", status="pending", proposed_by="human"))
        s.commit()
    after = garg.get("/api/admin/assistant/prompt").json()
    assert after["knowledge_count"] == before["knowledge_count"] + 1
    assert "Free parking behind the building." in after["system_prompt"] and "Not approved yet." not in after["system_prompt"]


def test_try_it_answers_without_sending_or_raising_tasks(env, bridge, garg, router):
    _, _, kid = setup(env, garg)
    router.reply["knowledge_ids"] = [f"K{kid}"]
    with env.session() as s:
        tasks_before = len(s.scalars(select(Task)).all())
    r = garg.post("/api/admin/assistant/try", {"text": "Open Sunday?"}).json()
    assert r["kind"] == "answer" and r["reply"].startswith("We are open")
    assert garg.post("/api/admin/assistant/try", {"text": "my cat is having a seizure"}).json()["kind"] == "emergency"
    with env.session() as s:
        assert len(s.scalars(select(Task)).all()) == tasks_before
