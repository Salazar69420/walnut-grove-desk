"""The TELUS send worker and client. All HTTP is mocked: no network, no real numbers (555 numbers only)."""
from __future__ import annotations

import json
import logging
import os
import stat
import uuid
from types import SimpleNamespace

import httpx
import pytest

from pipeline.telus import config as tc
from pipeline.telus import endpoints, send_worker
from pipeline.telus.client import ReadOnlyViolation, TelusHTTPError
from pipeline.telus.send_client import SendClient

TEST_NUM = "+16045550161"
OTHER = "+16045550142"
SENDER = "+16045550100"
SECRETS = ["FAKE_CLIENT_ID", "FAKE_CLIENT_SECRET", "FAKE_JWT.FAKE_PAYLOAD.FAKE_SIGNATURE"]
SCOPE = "SMS RingOut ReadAccounts"


class FakeSend:
    def __init__(self, scope=SCOPE, numbers=None, sms_status=200, login_status=200):
        self.scope, self.sms_status, self.login_status = scope, sms_status, login_status
        self.numbers = [{"phoneNumber": SENDER, "usageType": "DirectNumber", "features": ["SmsSender", "CallerId"]}] if numbers is None else numbers
        self.log: list[tuple[str, str, dict | None, str | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content and request.headers.get("content-type", "").startswith("application/json") else None
        self.log.append((request.method, request.url.path, body, request.headers.get("authorization")))
        if request.method == "POST" and request.url.path == endpoints.TOKEN_PATH:
            if self.login_status != 200:
                return httpx.Response(self.login_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "FAKE_ACCESS", "expires_in": 3600, "scope": self.scope})
        if request.url.path == endpoints.PHONE_NUMBERS_PATH:
            return httpx.Response(200, json={"records": self.numbers})
        if request.url.path == endpoints.SMS_PATH:
            if self.sms_status != 200:
                return httpx.Response(self.sms_status, json={"errorCode": "X", "message": f"detail about {TEST_NUM} and secret text"})
            return httpx.Response(200, json={"id": "999", "messageStatus": "Queued"})
        if request.url.path == endpoints.RINGOUT_PATH:
            return httpx.Response(200, json={"id": "7", "status": {"callStatus": "InProgress"}})
        return httpx.Response(404)

    def posts(self, path):
        return [x for x in self.log if x[0] == "POST" and x[1] == path]


@pytest.fixture
def env(tmp_path):
    root, bridge = tmp_path / "walnut", tmp_path / "bridge"
    root.mkdir()
    (bridge / "send" / "requests").mkdir(parents=True)
    tc.write_credentials(str(root), {"TELUS_ENV": "sandbox", "TELUS_SERVER_URL": endpoints.SERVER_URLS["sandbox"], "TELUS_CLIENT_ID": "FAKE_CLIENT_ID",
                                     "TELUS_CLIENT_SECRET": "FAKE_CLIENT_SECRET", "TELUS_JWT": "FAKE_JWT.FAKE_PAYLOAD.FAKE_SIGNATURE"})
    tc.copy_current_to_send(str(root))
    return SimpleNamespace(root=str(root), bridge=str(bridge), tmp=tmp_path)


def config(e, mode="test_numbers_only", numbers=(TEST_NUM,)):
    with open(os.path.join(e.bridge, "send_config.json"), "w") as f:
        json.dump({"v": 1, "mode": mode, "test_numbers": list(numbers)}, f)


def request(e, kind="sms", to=TEST_NUM, text="Hello from the clinic", job=None, **over):
    job = job or uuid.uuid4().hex
    body = {"v": 1, "id": job, "kind": kind, "to": to, "requested_by": "Dr. Garg", "requested_at": "2026-10-31T12:00:00Z"}
    if kind == "sms":
        body["text"] = text
    body.update(over)
    path = os.path.join(e.bridge, "send", "requests", f"{job}.json")
    with open(path, "w") as f:
        json.dump(body, f)
    return job, path


def work(e, fake, now=None):
    w = send_worker.SendWorker(e.root, e.bridge, client_factory=lambda c: SendClient(c, transport=httpx.MockTransport(fake.handler), sleep=lambda s: None),
                               **({"now": now} if now else {}))
    assert w.run() == 0


def result(e, job):
    with open(os.path.join(e.bridge, "send", "results", f"{job}.json")) as f:
        return json.load(f)


def test_practice_mode_sends_nothing_and_never_calls_telus(env):
    fake = FakeSend()
    config(env, "practice")
    job, path = request(env)
    work(env, fake)
    r = result(env, job)
    assert r["state"] == "blocked" and r["code"] == "practice_mode" and fake.log == [] and not os.path.exists(path)


def test_a_missing_or_garbage_config_means_practice_mode(env):
    fake = FakeSend()
    job, _ = request(env)
    work(env, fake)
    assert result(env, job)["code"] == "practice_mode"
    open(os.path.join(env.bridge, "send_config.json"), "w").write("{not json")
    job2, _ = request(env)
    work(env, fake)
    assert result(env, job2)["code"] == "practice_mode" and fake.log == []


def test_test_mode_sends_only_to_the_test_number(env):
    fake = FakeSend()
    config(env)
    job, _ = request(env)
    work(env, fake)
    r = result(env, job)
    assert r["state"] == "sent" and r["code"] == "sent" and r["telus_status"] == "Queued"
    (_, _, body, auth), = fake.posts(endpoints.SMS_PATH)
    assert body == {"from": {"phoneNumber": SENDER}, "to": [{"phoneNumber": TEST_NUM}], "text": "Hello from the clinic"} and auth == "Bearer FAKE_ACCESS"
    n = len(fake.log)
    job2, _ = request(env, to=OTHER)
    work(env, fake)
    assert result(env, job2) | {"at": 0} == {"v": 1, "id": job2, "state": "blocked", "code": "not_allowed_number", "telus_status": None, "at": 0}
    assert len(fake.log) == n  # nothing was sent to TELUS for the other number, not even a login


def test_live_mode_sends_to_any_valid_number(env):
    fake = FakeSend()
    config(env, "live", ())
    job, _ = request(env, to=OTHER)
    work(env, fake)
    assert result(env, job)["state"] == "sent" and fake.posts(endpoints.SMS_PATH)[0][2]["to"] == [{"phoneNumber": OTHER}]


def test_results_and_logs_never_hold_a_number_or_the_text(env, caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeSend(sms_status=400)
    config(env)
    job, _ = request(env, text="a private message")
    work(env, fake)
    text = json.dumps(result(env, job)) + caplog.text
    for needle in (TEST_NUM, "6045550161", "a private message", "secret text", "FAKE_ACCESS", *SECRETS):
        assert needle not in text, needle
    assert result(env, job)["code"] == "rejected"


@pytest.mark.parametrize("over", [{"to": "6045550161"}, {"to": "+16045550161x"}, {"to": "+11111111111"}, {"extra": 1}, {"text": ""}, {"v": 2}, {"text": "bad \x07 char"}])
def test_bad_requests_are_rejected_and_deleted(env, over):
    fake = FakeSend()
    config(env, "live", ())
    job, path = request(env, **over)
    work(env, fake)
    assert not os.path.exists(path) and fake.log == [] and not os.path.exists(os.path.join(env.bridge, "send", "results", f"{job}.json"))


def test_a_request_whose_name_and_id_differ_is_rejected(env):
    fake = FakeSend()
    config(env, "live", ())
    job, path = request(env)
    body = json.load(open(path))
    body["id"] = "f" * 32
    json.dump(body, open(path, "w"))
    work(env, fake)
    assert fake.log == [] and not os.path.exists(path)


def test_sms_with_a_from_number_or_ringout_with_text_is_rejected(env):
    fake = FakeSend()
    config(env, "live", ())
    _, p1 = request(env, from_number=SENDER)
    _, p2 = request(env, kind="ringout", from_number=SENDER)
    body = json.load(open(p2))
    body["text"] = "x"
    json.dump(body, open(p2, "w"))
    work(env, fake)
    assert fake.log == [] and not os.path.exists(p1) and not os.path.exists(p2)


@pytest.mark.parametrize("scope,numbers,status,code", [
    ("ReadAccounts", None, 200, "no_permission"),
    (SCOPE, [], 200, "no_sms_number"),
    (SCOPE, [{"phoneNumber": SENDER, "features": ["CallerId"]}], 200, "no_sms_number"),
    (SCOPE, None, 400, "rejected"),
    (SCOPE, None, 403, "no_permission"),
    (SCOPE, None, 429, "rate_limited"),
    (SCOPE, None, 500, "error"),
])
def test_failures_have_fixed_codes(env, scope, numbers, status, code):
    fake = FakeSend(scope=scope, numbers=numbers, sms_status=status)
    config(env)
    job, _ = request(env)
    work(env, fake)
    r = result(env, job)
    assert r["state"] == "failed" and r["code"] == code


def test_login_failure_and_missing_credentials(env):
    config(env)
    fake = FakeSend(login_status=401)
    job, _ = request(env)
    work(env, fake)
    assert result(env, job)["code"] == "login_failed"
    os.unlink(tc.send_credentials_path(env.root))
    job2, _ = request(env)
    work(env, FakeSend())
    assert result(env, job2)["code"] == "not_configured"


def test_at_most_thirty_sends_an_hour(env):
    fake = FakeSend()
    config(env, "live", ())
    jobs = [request(env)[0] for _ in range(send_worker.MAX_PER_HOUR + 2)]
    work(env, fake, now=lambda: 1_000_000.0)
    work(env, fake, now=lambda: 1_000_000.0)  # the worker takes at most 20 requests per run
    codes = [result(env, j)["code"] for j in jobs]
    assert codes.count("sent") == send_worker.MAX_PER_HOUR and codes.count("rate_limited_local") == 2
    assert len(fake.posts(endpoints.SMS_PATH)) == send_worker.MAX_PER_HOUR


def test_ringout_starts_a_call_from_the_staff_phone(env):
    fake = FakeSend()
    config(env)
    job, _ = request(env, kind="ringout", from_number=OTHER)
    work(env, fake)
    assert result(env, job)["state"] == "calling"
    assert fake.posts(endpoints.RINGOUT_PATH)[0][2] == {"from": {"phoneNumber": OTHER}, "to": {"phoneNumber": TEST_NUM}, "playPrompt": False}
    job2, _ = request(env, kind="ringout", from_number=OTHER, to=OTHER)  # neither side is the test number
    work(env, fake)
    assert result(env, job2)["code"] == "not_allowed_number"


# ---------------------------------------------------------------- the client cannot do anything else
def make_client(fake=None):
    fake = fake or FakeSend()
    creds = tc.Credentials(env="sandbox", server_url=endpoints.SERVER_URLS["sandbox"], client_id="FAKE_CLIENT_ID", client_secret="FAKE_CLIENT_SECRET", jwt="FAKE_JWT.FAKE_PAYLOAD.FAKE_SIGNATURE")
    return SendClient(creds, transport=httpx.MockTransport(fake.handler), sleep=lambda s: None), fake


@pytest.mark.parametrize("method,path", [
    ("POST", "/restapi/v1.0/account/~/extension/~/message-store"), ("POST", "/restapi/v1.0/account/~/call-log"), ("DELETE", endpoints.SMS_PATH),
    ("PUT", endpoints.RINGOUT_PATH), ("GET", endpoints.CALL_LOG_PATH), ("GET", endpoints.SMS_PATH), ("POST", endpoints.SMS_PATH + "/../x"),
])
def test_the_send_client_refuses_every_other_request(method, path):
    c, fake = make_client()
    with pytest.raises(ReadOnlyViolation):
        c._send(method, path)
    assert fake.log == []


def test_post_json_only_takes_the_two_send_paths():
    c, fake = make_client()
    c.login()
    with pytest.raises(ReadOnlyViolation):
        c.post_json("/restapi/v1.0/account/~/extension/~/fax", {})
    assert fake.posts("/restapi/v1.0/account/~/extension/~/fax") == []


def test_a_timeout_is_never_retried_so_a_text_cannot_be_sent_twice():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path == endpoints.TOKEN_PATH:
            return httpx.Response(200, json={"access_token": "T", "expires_in": 3600, "scope": SCOPE})
        raise httpx.ReadTimeout("slow")
    creds = tc.Credentials(env="sandbox", server_url=endpoints.SERVER_URLS["sandbox"], client_id="a", client_secret="b", jwt="c")
    c = SendClient(creds, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    c.login()
    with pytest.raises(TelusHTTPError):
        c.post_json(endpoints.SMS_PATH, {"x": 1})
    assert calls.count(endpoints.SMS_PATH) == 1


# ---------------------------------------------------------------- credentials
def test_send_credentials_are_a_private_copy_and_not_overwritten_by_accident(env):
    p = tc.send_credentials_path(env.root)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert open(p).read() == open(tc.credentials_path(env.root)).read()
    with pytest.raises(tc.TelusConfigError):
        tc.copy_current_to_send(env.root)
    tc.copy_current_to_send(env.root, force=True)
