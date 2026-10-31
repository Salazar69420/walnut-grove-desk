"""TELUS connector tests. All HTTP is mocked (httpx.MockTransport); nothing touches the network or a real root."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sqlite3
import urllib.parse

import httpx
import pytest

from pipeline import analytics, cli, db as dbm, load
from pipeline.telus import config as tc
from pipeline.telus import endpoints
from pipeline.telus.client import (MAX_RETRIES, ReadOnlyClient, ReadOnlyViolation, TelusAuthError,
                                   TelusError, TelusHTTPError)

FAKE_ID = "FAKE_CLIENT_ID"
FAKE_SECRET = "FAKE_CLIENT_SECRET"
FAKE_JWT = "FAKE_JWT.FAKE_PAYLOAD.FAKE_SIGNATURE"
FAKE_NUMBER = "+1 (604) 555-0142"
FAKE_DIGITS = "6045550142"
FAKE_NUMBER2 = "778-555-0199"
FAKE_NAME = "Fake Person Zed"
HOST = "platform.devtest.ringcentral.com"
GOOD_SCOPE = "ReadAccounts ReadCallLog ReadCallRecording ReadMessages"
SECRETS = [FAKE_ID, FAKE_SECRET, FAKE_JWT, "FAKE_JWT", "FAKE_SIGNATURE", "FAKE_ACCESS_TOKEN"]


class FakeRC:
    """A tiny pretend RingCentral. Records every request it receives."""

    def __init__(self, records=None, scope=GOOD_SCOPE, expires_in=3600, per_page=2):
        self.records = records if records is not None else []
        self.scope = scope
        self.expires_in = expires_in
        self.per_page = per_page
        self.requests: list[tuple[str, str, str]] = []  # method, host, path
        self.logins = 0
        self.script: list = []  # per-GET-call overrides, popped from the front: httpx.Response or Exception
        self.fail_page: int | None = None
        self.token = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.host, request.url.path))
        if request.method == "POST":
            assert request.url.path == endpoints.TOKEN_PATH
            self.logins += 1
            self.token = f"FAKE_ACCESS_TOKEN_{self.logins}"
            body = {"access_token": self.token, "token_type": "bearer", "expires_in": self.expires_in,
                    "scope": self.scope, "refresh_token": "FAKE_REFRESH_TOKEN"}
            return httpx.Response(200, json=body)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "token_invalid"})
        q = dict(request.url.params)
        page = int(q.get("page", "1"))
        if self.fail_page == page:
            return httpx.Response(500, json={"message": f"boom {FAKE_JWT}"})
        per = self.per_page
        chunk = self.records[(page - 1) * per: page * per]
        total_pages = max(1, -(-len(self.records) // per))
        body = {"records": chunk, "paging": {"page": page, "perPage": per, "totalPages": total_pages},
                "navigation": {}}
        if page < total_pages:
            body["navigation"]["nextPage"] = {"uri": "https://evil.example.invalid/next"}
        return httpx.Response(200, json=body)

    def transport(self):
        return httpx.MockTransport(self.handler)


def rec(i, start="2026-08-04T17:05:00.000Z", direction="Inbound", result="Accepted", frm=FAKE_NUMBER,
        to="+16045550000", recording=False):
    r = {"id": f"FAKEID{i}", "startTime": start, "duration": 30 + i, "direction": direction, "result": result,
         "type": "Voice", "extension": {"id": "1234"},
         "from": {"phoneNumber": frm, "name": FAKE_NAME}, "to": {"phoneNumber": to, "extensionId": "1234"}}
    if recording:
        r["recording"] = {"id": f"REC{i}", "uri": "https://example.invalid/r", "type": "Automatic"}
    return r


def creds(env="sandbox"):
    return tc.Credentials(env=env, server_url=endpoints.SERVER_URLS[env], client_id=FAKE_ID,
                          client_secret=FAKE_SECRET, jwt=FAKE_JWT)


def make_client(rc, **kw):
    return ReadOnlyClient(creds(), transport=rc.transport(), sleep=kw.pop("sleep", lambda s: None), **kw)


@pytest.fixture
def troot(tmp_path):
    root = tmp_path / "walnut"
    root.mkdir()
    values = {"TELUS_ENV": "sandbox", "TELUS_SERVER_URL": endpoints.SERVER_URLS["sandbox"],
              "TELUS_CLIENT_ID": FAKE_ID, "TELUS_CLIENT_SECRET": FAKE_SECRET, "TELUS_JWT": FAKE_JWT}
    tc.write_credentials(str(root), values)
    return str(root)


@pytest.fixture
def mock_http(monkeypatch):
    """Route every httpx.Client the connector builds through a FakeRC."""
    def install(rc):
        real = httpx.Client

        class Patched(real):
            def __init__(self, *a, **kw):
                kw["transport"] = rc.transport()
                super().__init__(*a, **kw)
        monkeypatch.setattr(httpx, "Client", Patched)
        return rc
    return install


def assert_clean(text: str):
    for s in SECRETS:
        assert s not in text, f"secret leaked: {s[:6]}..."
    assert FAKE_DIGITS not in re.sub(r"\D", "", text) and FAKE_NAME not in text


# ------------------------------------------------------------------ read-only guarantees
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "post", "TRACE"])
def test_non_get_methods_raise_and_send_nothing(method):
    rc = FakeRC()
    c = make_client(rc)
    with pytest.raises(ReadOnlyViolation):
        c.request(method, endpoints.CALL_LOG_PATH)
    assert rc.requests == []
    for name in ("post", "put", "patch", "delete"):
        assert not hasattr(c, name)  # no convenience verbs exist at all
    c.close()


def test_login_post_works_and_goes_only_to_token_path():
    rc = FakeRC()
    c = make_client(rc)
    c.login()
    assert rc.requests == [("POST", HOST, endpoints.TOKEN_PATH)]
    assert c.scopes == GOOD_SCOPE.split() and c.expires_in == 3600
    c.close()


def test_login_request_shape():
    seen = {}

    def h(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = urllib.parse.parse_qs(request.content.decode())
        seen["ctype"] = request.headers["content-type"]
        return httpx.Response(200, json={"access_token": "FAKE_ACCESS_TOKEN_X", "expires_in": 60, "scope": "ReadAccounts"})
    c = ReadOnlyClient(creds(), transport=httpx.MockTransport(h))
    c.login()
    assert base64.b64decode(seen["auth"].split()[1]).decode() == f"{FAKE_ID}:{FAKE_SECRET}"
    assert seen["body"] == {"grant_type": [endpoints.JWT_GRANT_TYPE], "assertion": [FAKE_JWT]}
    assert "application/x-www-form-urlencoded" in seen["ctype"]


@pytest.mark.parametrize("path", [
    "/restapi/v1.0/account/~/extension/~/sms", endpoints.TOKEN_PATH + "/x", endpoints.TOKEN_PATH + "/",
    "/restapi/v1.0/account/~/call-log", "/", "https://evil.example.invalid/x"])
def test_post_to_any_other_path_raises(path):
    rc = FakeRC()
    c = make_client(rc)
    with pytest.raises(ReadOnlyViolation):
        c._send("POST", path)
    assert rc.requests == []


@pytest.mark.parametrize("path", [
    "/restapi/v1.0/account/~/extension/~/sms",
    "/restapi/v1.0/account/~/extension/~/ring-out",
    "/restapi/v1.0/account/~/call-log/../extension",
    "/restapi/v1.0/account/~/call-log?perPage=1",
    "/restapi/v1.0/account/~/recording/abc/content/extra",
    "/restapi/v1.0/account/~/recording/a/b",
    "/restapi/v1.0/account/123/call-log",
    "/restapi/oauth/token",
    "//evil.example.invalid/restapi/v1.0/account/~/call-log",
    "https://evil.example.invalid/restapi/v1.0/account/~/call-log",
    "restapi/v1.0/account/~/call-log", "",
])
def test_get_outside_allowlist_raises_before_any_request(path):
    rc = FakeRC()
    c = make_client(rc)
    with pytest.raises(ReadOnlyViolation):
        c.get(path)
    assert rc.requests == []  # not even a login


def test_allowlisted_gets_pass():
    rc = FakeRC()
    c = make_client(rc)
    for p in (endpoints.ACCOUNT_INFO_PATH, endpoints.CALL_LOG_PATH, endpoints.EXT_CALL_LOG_PATH,
              endpoints.MESSAGE_STORE_PATH, endpoints.recording_path("R1"), endpoints.recording_content_path("R1")):
        assert c.get(p, {"page": 1}) is not None
    assert {m for m, _, _ in rc.requests} == {"GET", "POST"}
    assert sum(1 for m, _, _ in rc.requests if m == "POST") == 1


def test_redirect_is_not_followed():
    def h(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "FAKE_ACCESS_TOKEN_R", "expires_in": 3600, "scope": "ReadAccounts"})
        return httpx.Response(302, headers={"location": "https://evil.example.invalid/steal"})
    c = ReadOnlyClient(creds(), transport=httpx.MockTransport(h))
    with pytest.raises(TelusHTTPError) as e:
        c.get(endpoints.CALL_LOG_PATH)
    assert e.value.status == 302


# ------------------------------------------------------------------ credentials file
def test_credentials_roundtrip_and_repr_hides_values(troot):
    cr = tc.load_credentials(troot)
    assert cr.env == "sandbox" and cr.host == HOST
    assert_clean(repr(cr) + str(cr))


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660, 0o700, 0o400, 0o666])
def test_wrong_mode_refused(troot, mode):
    os.chmod(tc.credentials_path(troot), mode)
    with pytest.raises(tc.TelusConfigError) as e:
        tc.load_credentials(troot)
    assert_clean(str(e.value))


def test_wrong_owner_refused(troot, monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: os.stat(tc.credentials_path(troot)).st_uid + 1)
    with pytest.raises(tc.TelusConfigError, match="owned"):
        tc.load_credentials(troot)


def test_symlink_refused(troot, tmp_path):
    p = tc.credentials_path(troot)
    real = tmp_path / "elsewhere.env"
    os.replace(p, real)
    os.symlink(real, p)
    with pytest.raises(tc.TelusConfigError):
        tc.load_credentials(troot)


def test_missing_file_and_bad_contents(tmp_path):
    with pytest.raises(tc.TelusConfigError):
        tc.load_credentials(str(tmp_path))
    p = tc.credentials_path(str(tmp_path))
    os.makedirs(os.path.dirname(p))
    for text in ("TELUS_ENV=sandbox\n",
                 f"TELUS_ENV=staging\nTELUS_CLIENT_ID={FAKE_ID}\nTELUS_CLIENT_SECRET={FAKE_SECRET}\nTELUS_JWT={FAKE_JWT}\n",
                 f"TELUS_ENV=sandbox\nTELUS_SERVER_URL=http://insecure.example\nTELUS_CLIENT_ID={FAKE_ID}\n"
                 f"TELUS_CLIENT_SECRET={FAKE_SECRET}\nTELUS_JWT={FAKE_JWT}\n"):
        with open(p, "w") as f:
            f.write(text)
        os.chmod(p, 0o600)
        with pytest.raises(tc.TelusConfigError) as e:
            tc.load_credentials(str(tmp_path))
        assert_clean(str(e.value))


def test_default_server_url_by_env(tmp_path):
    for env, url in endpoints.SERVER_URLS.items():
        root = str(tmp_path / env)
        tc.write_credentials(root, {"TELUS_ENV": env, "TELUS_SERVER_URL": "", "TELUS_CLIENT_ID": FAKE_ID,
                                    "TELUS_CLIENT_SECRET": FAKE_SECRET, "TELUS_JWT": FAKE_JWT})
        assert tc.load_credentials(root).server_url == url


# ------------------------------------------------------------------ telus-setup
def feed(monkeypatch, answers):
    it = iter(answers)
    prompts = []

    def fake_getpass(prompt=""):
        prompts.append(prompt)
        return next(it)
    monkeypatch.setattr("getpass.getpass", fake_getpass)
    return prompts


def test_setup_writes_600_atomic_and_prints_only_keys(tmp_path, monkeypatch, capsys):
    root = str(tmp_path / "r")
    prompts = feed(monkeypatch, ["sandbox", "", FAKE_ID, FAKE_SECRET, FAKE_JWT])
    assert cli.main(["telus-setup", "--root", root]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines()[0].startswith("saved as the read-only login") and "TELUS_CLIENT_SECRET" in out.out
    assert_clean(out.out + out.err + "".join(prompts))
    p = tc.credentials_path(root)
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"
    assert oct(os.stat(os.path.dirname(p)).st_mode & 0o777) == "0o700"
    assert os.listdir(os.path.dirname(p)) == ["credentials.env"]  # no tmp file left
    assert tc.load_credentials(root).server_url == endpoints.SERVER_URLS["sandbox"]


def test_setup_refuses_overwrite_without_force(troot, monkeypatch, capsys):
    feed(monkeypatch, ["production", "", "X1", "X2", "X3"])
    before = open(tc.credentials_path(troot)).read()
    assert cli.main(["telus-setup", "--root", troot]) == 2
    assert open(tc.credentials_path(troot)).read() == before
    feed(monkeypatch, ["production", "", "FAKE_NEW_ID", "FAKE_NEW_SECRET", "FAKE_NEW_JWT"])
    assert cli.main(["telus-setup", "--root", troot, "--force"]) == 0
    assert tc.load_credentials(troot).env == "production"
    assert oct(os.stat(tc.credentials_path(troot)).st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("answers", [
    ["sandbox", "http://platform.devtest.ringcentral.com", "a", "b", "c"],
    ["sandbox", "https://host.example/with/path", "a", "b", "c"],
    ["moon", "", "a", "b", "c"],
    ["sandbox", "", "", "b", "c"],
])
def test_setup_rejects_bad_input_and_writes_nothing(tmp_path, monkeypatch, capsys, answers):
    root = str(tmp_path / "r")
    feed(monkeypatch, answers)
    assert cli.main(["telus-setup", "--root", root]) == 2
    assert not os.path.exists(tc.credentials_path(root))


def test_no_from_env_mode_exists():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["telus-setup", "--from-env"])


# ------------------------------------------------------------------ telus-check
def test_check_good_token_exit_zero_and_output_is_clean(troot, mock_http, capsys):
    rc = mock_http(FakeRC([rec(i, frm=FAKE_NUMBER) for i in range(3)], per_page=10))
    assert cli.main(["telus-check", "--root", troot]) == 0
    out = capsys.readouterr()
    assert_clean(out.out + out.err)
    assert "environment: sandbox" in out.out and f"server_host: {HOST}" in out.out
    for s in endpoints.REQUIRED_READ_SCOPES:
        assert f"{s}: present" in out.out
    assert "token_expires_in_seconds: 3600" in out.out and "calls_last_7_days: 3" in out.out
    assert "WRITE_CAPABLE" not in out.out
    gets = [r for r in rc.requests if r[0] == "GET"]
    assert len(gets) == 1  # exactly one GET


@pytest.mark.parametrize("extra", ["EditMessages", "SMS", "Admin", "ReadAdminSettings", "RingOut", "ManageSomething"])
def test_check_write_capable_scope_exits_nonzero(troot, mock_http, capsys, extra):
    mock_http(FakeRC([], scope=GOOD_SCOPE + " " + extra))
    assert cli.main(["telus-check", "--root", troot]) == 1
    out = capsys.readouterr().out
    assert f"WRITE_CAPABLE: {extra}" in out and "WARNING" in out


def test_check_missing_required_scope_exits_nonzero(troot, mock_http, capsys):
    mock_http(FakeRC([], scope="ReadAccounts ReadCallLog"))
    assert cli.main(["telus-check", "--root", troot]) == 1
    out = capsys.readouterr().out
    assert "ReadMessages: MISSING" in out and "ReadCallRecording: MISSING" in out


def test_check_bad_credentials_file_exits_2(troot, capsys):
    os.chmod(tc.credentials_path(troot), 0o644)
    assert cli.main(["telus-check", "--root", troot]) == 2
    assert_clean(capsys.readouterr().err)


def test_pull_refuses_write_capable_token(troot, mock_http, capsys):
    rc = mock_http(FakeRC([rec(1)], scope=GOOD_SCOPE + " EditMessages"))
    assert cli.main(["telus-pull-calllog", "--root", troot]) == 1
    assert not [r for r in rc.requests if r[0] == "GET"]


# ------------------------------------------------------------------ retries, expiry, pagination
def test_429_retry_then_success_uses_retry_after():
    rc = FakeRC([rec(1)], per_page=10)
    c = ReadOnlyClient(creds(), transport=rc.transport(), sleep=(slept := []).append)
    c.login()
    rc.script = [httpx.Response(429, headers={"Retry-After": "7"}),
                 httpx.Response(429, headers={"X-Rate-Limit-Window": "3"})]
    body = c.get(endpoints.CALL_LOG_PATH, {"page": 1})
    assert len(body["records"]) == 1 and slept == [7.0, 3.0]


def test_429_is_bounded():
    rc = FakeRC()
    c = make_client(rc)
    c.login()
    rc.script = [httpx.Response(429, headers={"Retry-After": "1"}) for _ in range(20)]
    with pytest.raises(TelusHTTPError) as e:
        c.get(endpoints.CALL_LOG_PATH)
    assert e.value.status == 429
    assert sum(1 for m, _, _ in rc.requests if m == "GET") == MAX_RETRIES + 1


def test_timeout_retries_then_gives_up_with_redacted_message():
    rc = FakeRC()
    c = make_client(rc)
    c.login()
    rc.script = [httpx.ReadTimeout(f"timed out talking with {FAKE_JWT} {c._token}") for _ in range(10)]
    with pytest.raises(TelusHTTPError) as e:
        c.get(endpoints.CALL_LOG_PATH)
    assert_clean(str(e.value) + repr(e.value))
    assert sum(1 for m, _, _ in rc.requests if m == "GET") == MAX_RETRIES + 1


def test_timeout_then_success():
    rc = FakeRC([rec(1)], per_page=10)
    c = make_client(rc)
    c.login()
    rc.script = [httpx.ConnectTimeout("x")]
    assert c.get(endpoints.CALL_LOG_PATH)["records"]


def test_token_expiry_triggers_exactly_one_relogin():
    now = [1000.0]
    rc = FakeRC([rec(1)], per_page=10, expires_in=3600)
    c = make_client(rc, clock=lambda: now[0])
    c.get(endpoints.CALL_LOG_PATH)
    c.get(endpoints.CALL_LOG_PATH)
    assert rc.logins == 1
    now[0] += 3600  # expired (inside the safety margin counts too)
    c.get(endpoints.CALL_LOG_PATH)
    c.get(endpoints.CALL_LOG_PATH)
    assert rc.logins == 2


def test_401_triggers_one_relogin_then_fails_if_still_401():
    rc = FakeRC([rec(1)], per_page=10)
    c = make_client(rc)
    c.login()
    rc.script = [httpx.Response(401)]
    assert c.get(endpoints.CALL_LOG_PATH)["records"] and rc.logins == 2
    rc.script = [httpx.Response(401), httpx.Response(401)]
    with pytest.raises(TelusHTTPError) as e:
        c.get(endpoints.CALL_LOG_PATH)
    assert e.value.status == 401 and rc.logins == 3


def test_login_rejected_message_is_clean():
    def h(request):
        return httpx.Response(401, json={"error_description": f"bad {FAKE_JWT} {FAKE_SECRET}"})
    c = ReadOnlyClient(creds(), transport=httpx.MockTransport(h))
    with pytest.raises(TelusAuthError) as e:
        c.login()
    assert_clean(str(e.value))
    assert "401" in str(e.value)


def test_pagination_three_pages_and_never_follows_next_uri():
    rc = FakeRC([rec(i) for i in range(5)], per_page=2)
    c = make_client(rc)
    pages = list(c.iter_pages(endpoints.CALL_LOG_PATH, {"perPage": 2}))
    assert [p for p, _ in pages] == [1, 2, 3]
    assert [len(b["records"]) for _, b in pages] == [2, 2, 1]
    assert {h for _, h, _ in rc.requests} == {HOST}  # nextPage pointed at another host: ignored
    assert list(c.iter_pages(endpoints.CALL_LOG_PATH, start_page=3))[0][0] == 3


# ------------------------------------------------------------------ pull
def pull_records():
    return [
        rec(1, "2026-08-04T17:05:00.000Z", "Inbound", "Accepted", FAKE_NUMBER, recording=True),
        rec(2, "2026-08-04T17:30:00.000Z", "Inbound", "Missed", FAKE_NUMBER2),
        rec(3, "2026-08-04T18:00:00.000Z", "Outbound", "Accepted", "+16045550000", to=FAKE_NUMBER),
        rec(4, "2026-08-04T19:00:00.000Z", "Inbound", "Voicemail", FAKE_NUMBER),
        rec(5, "2025-01-16T01:00:00.000Z", "Inbound", "Some Weird Result " + FAKE_NAME, FAKE_NUMBER2),
    ]


def db_bytes(root):
    blob = b""
    for dp, _, fs in os.walk(root):
        for f in fs:
            if f.startswith("private.db"):
                blob += open(os.path.join(dp, f), "rb").read()
    return blob


def test_pull_stores_no_raw_numbers_or_names_and_is_idempotent(troot, mock_http, capsys):
    mock_http(FakeRC(pull_records(), per_page=2))
    assert cli.main(["telus-pull-calllog", "--root", troot, "--days", "10"]) == 0
    out = capsys.readouterr()
    assert "records=5" in out.out and "total=5" in out.out
    assert_clean(out.out + out.err)
    blob = db_bytes(troot)
    assert len(blob) > 0
    for needle in (FAKE_DIGITS, "6045550142", "604) 555", "778-555-0199", "7785550199", FAKE_NAME, "Fake Person",
                   "Some Weird Result"):
        assert needle.encode() not in blob, needle
    conn = sqlite3.connect(os.path.join(troot, "private.db"))
    conn.row_factory = sqlite3.Row
    rows = {r["telus_id"]: dict(r) for r in conn.execute("SELECT * FROM telus_calls")}
    salt = open(os.path.join(troot, "salt.bin"), "rb").read()
    # same hash as the existing load code would give for the same caller
    assert rows["FAKEID1"]["from_hash"] == load.hmac_hex(salt, FAKE_DIGITS)
    assert rows["FAKEID3"]["to_hash"] == load.hmac_hex(salt, FAKE_DIGITS)
    assert rows["FAKEID1"]["has_recording"] == 1 and rows["FAKEID2"]["has_recording"] == 0
    assert rows["FAKEID1"]["start_local"] == "2026-08-04T10:05:00-07:00" and rows["FAKEID1"]["hour"] == 10
    assert rows["FAKEID1"]["weekday"] == 1 and rows["FAKEID1"]["ext_id"] == "1234"
    assert rows["FAKEID5"]["start_local"].endswith("-08:00")  # winter, PST
    assert {r["result"] for r in rows.values()} == {"answered", "missed", "voicemail", "other"}
    assert rows["FAKEID3"]["direction"] == "outbound"
    cols = {r[1] for r in conn.execute("PRAGMA table_info(telus_calls)")}
    assert not cols & {"name", "number", "phone", "text", "from_number", "to_number"}
    conn.close()
    # second run: nothing new, same rows
    assert cli.main(["telus-pull-calllog", "--root", troot, "--days", "10"]) == 0
    assert "new=0 total=5" in capsys.readouterr().out
    conn = sqlite3.connect(os.path.join(troot, "private.db"))
    assert conn.execute("SELECT COUNT(*) FROM telus_calls").fetchone()[0] == 5
    conn.close()


def test_pull_upserts_changed_record(troot, mock_http, capsys):
    rc = mock_http(FakeRC([rec(1, result="Accepted")], per_page=5))
    assert cli.main(["telus-pull-calllog", "--root", troot]) == 0
    rc.records = [rec(1, result="Missed")]
    assert cli.main(["telus-pull-calllog", "--root", troot]) == 0
    conn = sqlite3.connect(os.path.join(troot, "private.db"))
    assert conn.execute("SELECT result FROM telus_calls").fetchall() == [("missed",)]
    conn.close()


def test_pull_resumes_after_failure(troot, mock_http, capsys):
    rc = mock_http(FakeRC([rec(i, "2026-08-04T17:%02d:00.000Z" % i) for i in range(1, 8)], per_page=2))
    rc.fail_page = 3
    assert cli.main(["telus-pull-calllog", "--root", troot, "--days", "5"]) == 1
    out = capsys.readouterr()
    assert_clean(out.out + out.err)
    conn = sqlite3.connect(os.path.join(troot, "private.db"))
    assert conn.execute("SELECT COUNT(*) FROM telus_calls").fetchone()[0] == 4  # pages 1 and 2 committed
    st = dict(conn.execute("SELECT key, value FROM telus_sync_state").fetchall())
    assert json.loads(st["inflight"])["next_page"] == 3 and "synced_through" not in st
    conn.close()
    rc.fail_page = None
    rc.requests.clear()
    assert cli.main(["telus-pull-calllog", "--root", troot]) == 0
    assert "resumed=1" in capsys.readouterr().out
    pages_asked = [1 for m, _, _ in rc.requests if m == "GET"]
    assert len(pages_asked) == 2  # pages 3 and 4 only, not 1 and 2 again
    conn = sqlite3.connect(os.path.join(troot, "private.db"))
    assert conn.execute("SELECT COUNT(*) FROM telus_calls").fetchone()[0] == 7
    st = dict(conn.execute("SELECT key, value FROM telus_sync_state").fetchall())
    assert "inflight" not in st and "synced_through" in st
    conn.close()


def test_pull_uses_cursor_with_overlap_on_next_run(troot, mock_http, capsys):
    seen = []
    rc = FakeRC([rec(1)], per_page=5)
    base = rc.handler

    def spy(request):
        if request.method == "GET":
            seen.append(dict(request.url.params))
        return base(request)
    rc.transport = lambda: httpx.MockTransport(spy)
    mock_http(rc)
    assert cli.main(["telus-pull-calllog", "--root", troot, "--days", "3"]) == 0
    assert cli.main(["telus-pull-calllog", "--root", troot]) == 0
    first, second = seen
    assert first["perPage"] == "1000" and first["view"] == "Simple"
    assert second["dateFrom"] < first["dateTo"] and second["dateFrom"] > first["dateFrom"]


def test_pull_bad_since(troot, mock_http, capsys):
    mock_http(FakeRC())
    assert cli.main(["telus-pull-calllog", "--root", troot, "--since", "last tuesday"]) == 2
    assert cli.main(["telus-pull-calllog", "--root", troot, "--days", "0"]) == 2


def test_pull_since_naive_is_vancouver_local(troot, mock_http):
    from pipeline.telus.calllog import parse_since
    assert parse_since("2026-08-01").isoformat() == "2026-08-01T07:00:00+00:00"
    assert parse_since("2026-08-01T00:00:00Z").isoformat() == "2026-08-01T00:00:00+00:00"


def test_pull_skips_junk_records(troot, mock_http, capsys):
    junk = [{"id": "../../etc", "startTime": "2026-08-04T17:00:00Z"}, {"id": "OK1", "startTime": "garbage"},
            "string", {"startTime": "2026-08-04T17:00:00Z"}, rec(9)]
    mock_http(FakeRC(junk, per_page=10))
    assert cli.main(["telus-pull-calllog", "--root", troot]) == 0
    assert "records=1 skipped=4" in capsys.readouterr().out


# ------------------------------------------------------------------ secrets never leak
def test_secrets_never_in_logs_exceptions_or_stdout(troot, mock_http, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)
    rc = mock_http(FakeRC(pull_records(), per_page=2))
    texts = []
    # happy paths
    for argv in (["telus-check"], ["telus-pull-calllog"]):
        cli.main(argv + ["--root", troot])
        o = capsys.readouterr()
        texts += [o.out, o.err]
    # failure paths that echo secrets in the server body or in the exception text
    rc.script = [httpx.Response(429, headers={"Retry-After": "0"}, text=FAKE_JWT),
                 httpx.Response(500, text=f"{FAKE_SECRET} {rc.token}"),
                 httpx.ReadTimeout(f"{FAKE_JWT}")]
    c = ReadOnlyClient(creds(), transport=rc.transport(), sleep=lambda s: None)
    for _ in range(3):
        try:
            c.get(endpoints.CALL_LOG_PATH)
        except TelusError as e:
            texts += [str(e), repr(e)]
    rc.fail_page = 1
    rc.script = []
    assert cli.main(["telus-pull-calllog", "--root", troot, "--days", "1"]) == 1
    o = capsys.readouterr()
    texts += [o.out, o.err]
    texts.append(caplog.text)
    texts += [r.getMessage() for r in caplog.records]
    for t in texts:
        assert_clean(t)
    assert any("telus" in r.name for r in caplog.records)  # we do log, just never values


def test_client_repr_hides_everything():
    c = ReadOnlyClient(creds(), transport=FakeRC().transport())
    assert_clean(repr(c))


# ------------------------------------------------------------------ analytics
def analytics_records():
    out = []
    n = 0

    def add(start, direction, result, count):
        nonlocal n
        for _ in range(count):
            n += 1
            out.append(rec(n, start, direction, result, FAKE_NUMBER))
    add("2026-08-04T17:10:00.000Z", "Inbound", "Missed", 6)       # Tue 10h, 6 missed
    add("2026-08-11T17:10:00.000Z", "Inbound", "Accepted", 6)     # next Tue 10h, answered
    add("2026-08-05T21:10:00.000Z", "Inbound", "Voicemail", 2)    # Wed 14h, 2 voicemail (small)
    add("2026-08-05T22:10:00.000Z", "Outbound", "Accepted", 6)    # Wed 15h, outbound
    add("2026-08-08T17:10:00.000Z", "Inbound", "Accepted", 7)     # Sat, not a weekday hour
    return out


def test_analytics_telus_metrics_suppression_and_no_leak(env, mock_http, capsys):
    root = env["root"]
    tc.write_credentials(root, {"TELUS_ENV": "sandbox", "TELUS_SERVER_URL": endpoints.SERVER_URLS["sandbox"],
                                "TELUS_CLIENT_ID": FAKE_ID, "TELUS_CLIENT_SECRET": FAKE_SECRET, "TELUS_JWT": FAKE_JWT})
    mock_http(FakeRC(analytics_records(), per_page=7))
    assert cli.main(["telus-pull-calllog", "--root", root]) == 0
    assert cli.main(["analytics", "--root", root]) == 0
    res = json.load(open(f"{root}/out/analytics.json"))
    rows = res["rows"]
    for r in rows:
        assert set(r) == {"source", "metric", "dim1", "dim2", "value", "n", "suppressed"}
        if r["suppressed"]:
            assert r["value"] is None and r["n"] is None
        else:
            assert r["n"] >= 5
    get = lambda m, d1=None: [r for r in rows if r["metric"] == m and r["dim1"] == d1]  # noqa: E731
    by = get("telus_calls_by_hour", "10")[0]
    assert by["value"] == 2.0 and by["n"] == 12  # 12 calls over 6 weekdays (Aug 4 to Aug 11 minus weekend)
    assert get("telus_calls_by_hour", "14")[0]["suppressed"]            # 2 voicemails only
    assert get("telus_calls_by_hour", "15")[0]["n"] == 6
    assert len(get("telus_calls_by_hour", "7")) == 0 and len(get("telus_calls_by_hour", "18")) == 0
    assert len([r for r in rows if r["metric"] == "telus_calls_by_hour"]) == 10
    assert get("telus_missed_by_hour", "10")[0]["value"] == 1.0
    assert get("telus_missed_by_hour", "11")[0]["suppressed"]
    ms, vs = get("telus_missed_share")[0], get("telus_voicemail_share")[0]
    assert ms["value"] == round(6 / 21, 4) and not ms["suppressed"]
    assert vs["suppressed"]  # only 2 voicemails
    raw = open(f"{root}/out/analytics.json").read()
    assert FAKE_DIGITS not in raw and "FAKEID" not in raw and not re.search(r"[0-9a-f]{64}", raw)
    assert cli.main(["verify-no-leak", "--root", root, "--paths", f"{root}/out"]) == 0
    # existing metrics are still all present
    assert any(r["metric"] == "calls_total_by_weekday_hour" for r in rows)


def test_analytics_without_telus_rows_is_unchanged(env, capsys):
    root = env["root"]
    assert cli.main(["analytics", "--root", root]) == 0
    res = json.load(open(f"{root}/out/analytics.json"))
    assert not [r for r in res["rows"] if r["metric"].startswith("telus_")]


def test_analytics_telus_min_cell_parameter(tmp_path):
    root = tmp_path / "r"
    conn = dbm.connect(str(root / "p.db"), create=True)
    for i in range(5):
        conn.execute("INSERT INTO telus_calls(telus_id, start_local, date, hour, weekday, duration, direction, "
                     "result, ext_id, has_recording, from_hash, to_hash, synced_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"A{i}", "2026-08-04T10:00:00-07:00", "2026-08-04", 10, 1, 5, "inbound", "missed", None, 0,
                      None, None, "x"))
    rows = analytics.build(conn, "rules-v1")["rows"]
    m = [r for r in rows if r["metric"] == "telus_missed_share"][0]
    assert not m["suppressed"] and m["value"] == 1.0
    rows4 = analytics.build(conn, "rules-v1", min_cell=6)["rows"]
    assert [r for r in rows4 if r["metric"] == "telus_missed_share"][0]["suppressed"]
