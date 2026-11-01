"""Creative studio: the interview, the writer, generation and download, the share page, campaigns, budget and permissions."""
import dataclasses
import json

import httpx
import pytest
from sqlalchemy import select

from app import config
from app.adapters import llm as llm_adapter
from app.models import Creative
from app.services import creatives as C


class Fakes:
    def __init__(self):
        self.asked = 0
        self.ai_calls: list[dict] = []
        self.ws_calls: list[tuple[str, dict]] = []
        self.status = "processing"

    def ai(self, r: httpx.Request) -> httpx.Response:
        body = json.loads(r.content)
        self.ai_calls.append(body)
        system = body["messages"][0]["content"]
        if "You are interviewing" in system:
            self.asked += 1
            out = {"done": True} if self.asked > 2 else {"done": False, "question": f"Question {self.asked}?", "options": ["A", "B", "C"]}
        else:
            out = {"plan": {"hook": "a wagging tail", "arc": "nervous to happy", "checks": "40 dentals a month; no rule broken"},
                   "prompt": "A bright clinic photo of a happy golden retriever after a dental check, headline \"40 smiles a month\", Walnut Grove Animal Hospital.",
                   "caption": "Healthy teeth, happy pets. #LangleyBC", "sms_text": "Dental month at Walnut Grove Animal Hospital: book a check-up!", "title": "Dental month"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(out)}}], "usage": {"cost": 0.0012}})

    def ws(self, r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/result"):
            return httpx.Response(200, json={"data": {"status": self.status, "outputs": ["https://cdn.example/out.png"] if self.status == "completed" else []}})
        if r.url.host == "cdn.example":
            return httpx.Response(200, content=b"\x89PNG fake image")
        self.ws_calls.append((r.url.path, json.loads(r.content) if r.content and r.headers.get("content-type", "").startswith("application/json") else {}))
        return httpx.Response(200, json={"data": {"id": "task1", "urls": {"get": "https://api.wavespeed.ai/api/v3/predictions/task1/result"}}})


@pytest.fixture()
def fakes(env, monkeypatch, tmp_path, garg):
    f = Fakes()
    monkeypatch.setattr(llm_adapter, "TRANSPORT", httpx.MockTransport(f.ai))
    monkeypatch.setattr(C, "TRANSPORT", httpx.MockTransport(f.ws))
    config.set_settings(dataclasses.replace(env.settings, public_url="https://desk.example"))
    assert garg.put("/api/admin/assistant", {"api_key": "sk-or-v1-testkey-1234"}).status_code == 200
    yield f
    config.set_settings(env.settings)


def run_interview(garg, kind="image", fmt="seasonal_reminder"):
    c = garg.post("/api/creatives", {"kind": kind, "format": fmt}).json()["item"]
    assert c["question"], c
    assert c["question"]["question"] == "Question 1?" and c["question"]["options"] == ["A", "B", "C"]
    c = garg.post(f"/api/creatives/{c['id']}/answer", {"answer": "Dental month, we do 40 a month"}).json()["item"]
    assert c["question"]["question"] == "Question 2?"
    c = garg.post(f"/api/creatives/{c['id']}/answer", {"answer": "Warm and reassuring"}).json()["item"]
    return garg.get(f"/api/creatives/{c['id']}").json()["item"]


def test_interview_then_writer_then_review(env, garg, fakes):
    c = run_interview(garg)
    assert c["status"] == "review" and c["title"] == "Dental month" and "40 smiles" in c["prompt"] and c["plan"]["hook"] == "a wagging tail"
    assert [q["answer"] for q in c["qa"]] == ["Dental month, we do 40 a month", "Warm and reassuring"]
    first = fakes.ai_calls[0]["messages"]
    assert "never ask about camera" in first[0]["content"].lower() or "NEVER ask about camera" in first[0]["content"]
    assert "CLINIC FACTS YOU ALREADY KNOW" in first[0]["content"] and "Opening hours" in first[0]["content"]
    assert len(fakes.ai_calls[2]["messages"]) > len(fakes.ai_calls[1]["messages"])  # one growing conversation, not separate calls
    assert c["cost_usd"] > 0  # model cost is counted


def test_generate_download_share_and_campaign(env, garg, fakes):
    c = run_interview(garg)
    assert garg.post(f"/api/creatives/{c['id']}/generate", {}).status_code == 409  # no WaveSpeed key yet
    assert garg.put("/api/creatives/settings/studio", {"wavespeed_key": "ws-test-key-123456", "monthly_budget_usd": 20}).status_code == 200
    g = garg.post(f"/api/creatives/{c['id']}/generate", {}).json()["item"]
    assert g["status"] == "generating" and fakes.ws_calls[0][0].endswith("openai/gpt-image-2/text-to-image") and fakes.ws_calls[0][1]["aspect_ratio"] == "4:5"
    with env.session() as s:
        C.poll_once(s)
        assert s.get(Creative, c["id"]).status == "generating"
    fakes.status = "completed"
    with env.session() as s:
        assert C.poll_once(s) == 1
    done = garg.get(f"/api/creatives/{c['id']}").json()["item"]
    assert done["status"] == "done" and done["share_url"].startswith("https://desk.example/c/")
    token = done["share_url"].rsplit("/", 1)[1]
    page = garg.c.get(f"/c/{token}")
    assert page.status_code == 200 and "Walnut Grove" in page.text and "Healthy teeth" in page.text
    assert garg.c.get(f"/c/{token}/media").content == b"\x89PNG fake image"
    assert garg.c.get("/c/not-a-real-token-123456").status_code == 404
    camp = garg.post("/api/campaigns", {"name": "Dental", "channel": "sms", "purpose": "care", "body": "Hi {first_name}, dental month!", "creative_id": c["id"]})
    assert camp.status_code == 201, camp.text
    assert camp.json()["item"]["creative"]["share_url"] == done["share_url"]
    prev = garg.post("/api/campaigns/preview", {"channel": "sms", "purpose": "care", "body": "Hi {first_name}, dental month!", "creative_id": c["id"]}).json()
    assert all(done["share_url"] in s["text"] for s in prev["samples"])


def test_budget_cap_and_permissions(env, garg, priya, anita, fakes):
    garg.put("/api/creatives/settings/studio", {"wavespeed_key": "ws-test-key-123456", "monthly_budget_usd": 1})
    c = run_interview(garg, kind="video", fmt="happy_visit")
    assert garg.put(f"/api/creatives/{c['id']}", {"options": {"resolution": "1080p", "duration": 15}}).json()["item"]["estimate_usd"] == 13.5
    r = garg.post(f"/api/creatives/{c['id']}/generate", {})
    assert r.status_code == 409 and "budget" in r.json()["detail"]
    assert priya.get("/api/creatives").json()["can_make"] is False and priya.post(f"/api/creatives/{c['id']}/generate", {}).status_code == 403
    assert anita.get("/api/creatives").status_code == 403
    assert garg.post("/api/campaigns", {"name": "Video test", "channel": "sms", "purpose": "care", "body": "Hi {first_name}, hello there", "creative_id": c["id"]}).status_code == 400
