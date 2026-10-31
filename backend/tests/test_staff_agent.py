"""Staff assistant with AI (OpenRouter mocked): tools under the person's own permissions, changes only on Confirm."""
from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from app.adapters import llm as llm_adapter
from app.models import KnowledgeEntry, Task


class Script:
    """Pretend model: plays back a list of answers; each is either tool calls or final text."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.requests: list[dict] = []

    def handler(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        step = self.steps.pop(0) if self.steps else {"content": "done"}
        if "tools" in step:
            calls = [{"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(step["tools"])]
            msg = {"role": "assistant", "content": "", "tool_calls": calls}
        else:
            msg = {"role": "assistant", "content": step["content"]}
        return httpx.Response(200, json={"choices": [{"message": msg}]})


def on(garg):
    assert garg.put("/api/admin/assistant", {"api_key": "sk-or-v1-testkey-1234", "staff_enabled": True}).status_code == 200


def use(monkeypatch, steps):
    s = Script(steps)
    monkeypatch.setattr(llm_adapter, "TRANSPORT", httpx.MockTransport(s.handler))
    return s


def chat(who, text):
    return who.post("/api/assistant/chat", {"messages": [{"role": "user", "content": text}]})


def test_off_by_default_and_the_panel_knows(env, garg):
    assert garg.get("/api/assistant/chips").json()["ai"] is False
    assert chat(garg, "hi").status_code == 409
    assert garg.put("/api/admin/assistant", {"staff_enabled": True}).status_code == 409  # needs a key first


def test_it_looks_things_up_with_tools_and_answers(env, garg, monkeypatch):
    on(garg)
    s = use(monkeypatch, [{"tools": [("clinic_overview", {}), ("list_tasks", {"status": "active"})]}, {"content": "You have 3 open tasks."}])
    r = chat(garg, "What is open today?").json()
    assert r["text"] == "You have 3 open tasks." and r["tools"] == ["clinic_overview", "list_tasks"]
    tool_msgs = [m for m in s.requests[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 2 and "open_tasks" in tool_msgs[0]["content"]
    assert s.requests[0]["provider"]["data_collection"] == "deny" and s.requests[0]["tool_choice"] == "auto"


def test_tools_follow_the_persons_permissions(env, garg, anita, monkeypatch):
    on(garg)
    s = use(monkeypatch, [{"content": "ok"}])
    chat(anita, "hello")
    names = {t["function"]["name"] for t in s.requests[0]["tools"]}
    assert "campaigns" not in names and "report" not in names and "clinic_overview" in names  # a nurse cannot see campaigns or reports
    s2 = use(monkeypatch, [{"tools": [("campaigns", {})]}, {"content": "ok"}])
    chat(anita, "show campaigns")
    tool = [m for m in s2.requests[1]["messages"] if m["role"] == "tool"][0]
    assert "not available" in tool["content"]


def test_a_change_happens_only_after_confirm(env, garg, monkeypatch):
    on(garg)
    use(monkeypatch, [{"tools": [("propose_change", {"action": "add_knowledge", "params": {"category": "clinic_info", "title": "Friday hours", "text": "On Fridays we close at 6 pm.", "approve": True},
                                                        "summary": "Add approved knowledge: on Fridays we close at 6 pm."})]}, {"content": "Press Confirm to add it."}])
    r = chat(garg, "Add that we close at 6 on Fridays").json()
    assert len(r["proposals"]) == 1
    with env.session() as s:
        assert s.scalar(select(KnowledgeEntry).where(KnowledgeEntry.title == "Friday hours")) is None  # nothing yet
    done = garg.post(f"/api/assistant/proposals/{r['proposals'][0]['id']}/confirm").json()
    assert "approved" in done["done"]
    with env.session() as s:
        assert s.scalar(select(KnowledgeEntry).where(KnowledgeEntry.title == "Friday hours")).status == "approved"
    assert garg.post(f"/api/assistant/proposals/{r['proposals'][0]['id']}/confirm").status_code == 404  # only once


def test_a_person_cannot_confirm_what_they_may_not_do_or_someone_elses(env, garg, priya, monkeypatch):
    on(garg)
    use(monkeypatch, [{"tools": [("propose_change", {"action": "setting", "params": {"setting": "automatic_replies", "on": True}, "summary": "Turn on automatic replies"})]}, {"content": "x"}])
    r = chat(priya, "turn on automatic replies").json()
    assert r["proposals"] == []  # the desk may not change that, so nothing is proposed
    use(monkeypatch, [{"tools": [("propose_change", {"action": "task", "params": {"title": "Call the lab"}, "summary": "Create a task: call the lab"})]}, {"content": "x"}])
    r = chat(garg, "make a task to call the lab").json()
    assert priya.post(f"/api/assistant/proposals/{r['proposals'][0]['id']}/confirm").status_code == 403
    assert garg.post(f"/api/assistant/proposals/{r['proposals'][0]['id']}/confirm").status_code == 200
    with env.session() as s:
        assert s.scalar(select(Task).where(Task.title == "Call the lab")) is not None


def test_cancel_drops_a_proposal(env, garg, monkeypatch):
    on(garg)
    use(monkeypatch, [{"tools": [("propose_change", {"action": "task", "params": {"title": "Never"}, "summary": "x"})]}, {"content": "x"}])
    pid = chat(garg, "x").json()["proposals"][0]["id"]
    garg.post(f"/api/assistant/proposals/{pid}/cancel")
    assert garg.post(f"/api/assistant/proposals/{pid}/confirm").status_code == 404


def test_provider_failure_is_a_plain_message(env, garg, monkeypatch):
    on(garg)
    monkeypatch.setattr(llm_adapter, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    r = chat(garg, "hi")
    assert r.status_code == 502 and "question buttons still work" in r.json()["detail"]


def test_owner_tools_and_personal_prompt(env, garg, priya, monkeypatch):
    on(garg)
    s = use(monkeypatch, [{"tools": [("system_status", {}), ("staff_list", {}), ("audit_log", {"limit": 5}), ("list_tasks", {"assigned": "me_or_unassigned"})]}, {"content": "ok"}])
    chat(garg, "how is everything")
    names = {t["function"]["name"] for t in s.requests[0]["tools"]}
    assert {"system_status", "staff_list", "audit_log"} <= names
    assert "owner and head of the clinic" in s.requests[0]["messages"][0]["content"]
    tool_out = {m["tool_call_id"]: m["content"] for m in s.requests[1]["messages"] if m["role"] == "tool"}
    assert "database_mb" in tool_out["c0"] and "sk-or" not in tool_out["c0"]
    assert '"role": "owner"' in tool_out["c1"] and "password" not in tool_out["c1"]
    assert '"entries"' in tool_out["c2"] and '"tasks"' in tool_out["c3"]
    s2 = use(monkeypatch, [{"content": "ok"}])
    chat(priya, "hi")
    desk = {t["function"]["name"] for t in s2.requests[0]["tools"]}
    assert not ({"system_status", "staff_list", "audit_log"} & desk) and "owner and head" not in s2.requests[0]["messages"][0]["content"]
