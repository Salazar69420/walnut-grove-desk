"""TELUS recordings worker tests. All HTTP is mocked (httpx.MockTransport). No network, no real root, no real ids."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import stat
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from pipeline import cli, db as dbm
from pipeline.telus import calllog, endpoints, recordings, worker
from pipeline.telus import config as tc
from pipeline.telus.client import (ReadOnlyClient, ReadOnlyViolation, TelusDownloadError, TelusHTTPError)

FAKE_ID = "FAKE_CLIENT_ID"
FAKE_SECRET = "FAKE_CLIENT_SECRET"
FAKE_JWT = "FAKE_JWT.FAKE_PAYLOAD.FAKE_SIGNATURE"
FAKE_DIGITS = "6045550142"
FAKE_NAME = "Fake Person Zed"
GOOD_SCOPE = "ReadAccounts ReadCallLog ReadCallRecording ReadMessages"
SECRETS = [FAKE_ID, FAKE_SECRET, FAKE_JWT, "FAKE_JWT", "FAKE_SIGNATURE", "FAKE_ACCESS_TOKEN", "FAKE_REFRESH"]
PLATFORM = "platform.devtest.ringcentral.com"
MEDIA = "media.devtest.ringcentral.com"
NOW = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)
HEX_A = "a" * 32
HEX_B = "b" * 32
CONTENT_RE = re.compile(r"^/restapi/v1\.0/account/~/recording/([A-Za-z0-9_-]+)/content$")


def body_for(rid: str) -> bytes:
    return (b"FAKE-AUDIO-" + rid.encode()) * 20


def audio(rid: str, **kw) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": kw.pop("ctype", "audio/mpeg")}, content=body_for(rid))


class FakeTelus:
    """Pretend TELUS: token, a one-page call log, recording content (optionally via a redirect to a media host)."""

    def __init__(self, n=3, scope=GOOD_SCOPE):
        self.scope = scope
        self.records = [self.call(i) for i in range(1, n + 1)]
        self.content: dict[str, list] = {}   # recording id -> list of callables (request) -> Response; last one repeats
        self.media: dict[str, list] = {}     # media path -> same
        self.log: list[tuple[str, str, str, str | None]] = []  # method, host, path, authorization
        self.logins = 0
        self.token = None

    @staticmethod
    def call(i, recording=True):
        r = {"id": f"FAKECALL{i}", "startTime": f"2026-08-09T17:{i:02d}:00.000Z", "duration": 30 + i,
             "direction": "Inbound", "result": "Accepted", "type": "Voice", "extension": {"id": "1234"},
             "from": {"phoneNumber": "+1 (604) 555-0142", "name": FAKE_NAME}, "to": {"phoneNumber": "+16045550000"}}
        if recording:
            r["recording"] = {"id": f"FAKEREC{i}", "uri": "https://example.invalid/x", "type": "Automatic"}
        return r

    def _next(self, table, key, request):
        items = table.get(key)
        if not items:
            return None
        item = items.pop(0) if len(items) > 1 else items[0]
        return item(request) if callable(item) else item

    def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        auth = request.headers.get("authorization")
        self.log.append((request.method, host, path, auth))
        if request.method == "POST":
            self.logins += 1
            self.token = f"FAKE_ACCESS_TOKEN_{self.logins}"
            return httpx.Response(200, json={"access_token": self.token, "expires_in": 3600, "scope": self.scope,
                                             "refresh_token": "FAKE_REFRESH_TOKEN"})
        if auth != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "token_invalid"})
        if host != PLATFORM:
            r = self._next(self.media, path, request)
            return r if r is not None else httpx.Response(404)
        if path == endpoints.CALL_LOG_PATH:
            return httpx.Response(200, json={"records": self.records, "paging": {"page": 1, "totalPages": 1,
                                                                                  "totalElements": len(self.records)},
                                             "navigation": {}})
        m = CONTENT_RE.match(path)
        if m:
            r = self._next(self.content, m.group(1), request)
            return r if r is not None else audio(m.group(1))
        return httpx.Response(404)

    def transport(self):
        return httpx.MockTransport(self.handler)

    def gets(self, host=None):
        return [x for x in self.log if x[0] == "GET" and (host is None or x[1] == host)]

    def content_gets(self):
        return [x for x in self.log if x[0] == "GET" and CONTENT_RE.match(x[2])]


@pytest.fixture(autouse=True)
def _lots_of_disk(monkeypatch):
    monkeypatch.setattr(worker, "free_bytes", lambda p: 10 ** 13)


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "walnut"
    root.mkdir()
    tc.write_credentials(str(root), {"TELUS_ENV": "sandbox", "TELUS_SERVER_URL": endpoints.SERVER_URLS["sandbox"],
                                     "TELUS_CLIENT_ID": FAKE_ID, "TELUS_CLIENT_SECRET": FAKE_SECRET,
                                     "TELUS_JWT": FAKE_JWT})
    bridge = tmp_path / "bridge"
    (bridge / "requests").mkdir(parents=True)
    e = SimpleNamespace(root=str(root), bridge=str(bridge), tmp=tmp_path, sleeps=[], writes=[])
    return e


def creds(env_name="sandbox"):
    return tc.Credentials(env=env_name, server_url=endpoints.SERVER_URLS[env_name], client_id=FAKE_ID,
                          client_secret=FAKE_SECRET, jwt=FAKE_JWT)


def write_cfg(e, **over):
    cfg = {"v": 1, "recordings_enabled": True, "max_gb": 10, "keep_days": 30, "max_per_run_cap": 200,
           "updated_by": "Fake Admin", "updated_at": "2026-08-01T00:00:00Z"}
    cfg.update(over)
    with open(os.path.join(e.bridge, "config.json"), "w") as f:
        json.dump(cfg, f)


def write_req(e, job_id=HEX_A, mode="recordings", days=5, max_items=50, by="Fake Admin", mtime=None, **over):
    req = {"v": 1, "id": job_id, "mode": mode, "days": days, "max_items": max_items, "requested_by": by,
           "requested_at": "2026-08-10T17:59:00Z"}
    req.update(over)
    path = os.path.join(e.bridge, "requests", f"{job_id}.json")
    with open(path, "w") as f:
        json.dump(req, f)
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


_REAL_WRITE = worker.write_status


def make_worker(e, rc, **kw):
    real_write = _REAL_WRITE

    def spy(bridge, status):  # remember every status document written, for the leak scan
        e.writes.append(json.dumps(status))
        real_write(bridge, status)
    worker.write_status = spy
    kw.setdefault("file_sleep", 0)
    w = worker.Worker(e.root, e.bridge, os.path.join(e.root, "private.db"), os.path.join(e.root, "salt.bin"),
                      sleep=e.sleeps.append, now=lambda: NOW,
                      client_factory=lambda c, sleep: ReadOnlyClient(c, transport=rc.transport(), sleep=sleep), **kw)
    return w


@pytest.fixture(autouse=True)
def _restore_write_status():
    yield
    worker.write_status = _REAL_WRITE


def run(e, rc, **kw):
    code = make_worker(e, rc, **kw).run()
    return code, read_status(e)


def read_status(e):
    with open(os.path.join(e.bridge, "status.json")) as f:
        return json.load(f)


def rec_files(e):
    out = []
    for dp, _, fs in os.walk(os.path.join(e.root, "recordings")):
        out += [os.path.join(dp, f) for f in fs]
    return sorted(out)


def db_rows(e, sql):
    conn = sqlite3.connect(os.path.join(e.root, "private.db"))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


STATUS_KEYS = {"v", "updated_at", "connection", "job", "storage", "rejected_requests"}
CONN_KEYS = {"state", "code", "checked_at", "environment", "write_capable_scopes", "calls_last_7_days"}
JOB_KEYS = {"id", "mode", "state", "requested_by", "requested_at", "started_at", "finished_at", "code", "found",
            "downloaded", "already_had", "skipped", "failed", "remaining", "deleted_old"}
STORAGE_KEYS = {"recordings_count", "bytes", "free_bytes", "max_bytes"}


def assert_status_clean(e):
    """Every status document ever written has exactly the contract keys and nothing derived from TELUS."""
    assert e.writes
    for text in e.writes + [open(os.path.join(e.bridge, "status.json")).read()]:
        d = json.loads(text)
        assert set(d) == STATUS_KEYS
        assert set(d["connection"]) == CONN_KEYS and set(d["job"]) == JOB_KEYS and set(d["storage"]) == STORAGE_KEYS
        assert d["job"]["code"] in worker.CODES and d["connection"]["code"] in worker.CODES
        for needle in ("FAKEREC", "FAKECALL", FAKE_DIGITS, FAKE_NAME, "Traceback", "http", "ringcentral",
                       "Error", *SECRETS):
            assert needle not in text, needle


# ------------------------------------------------------------------ happy path and bookkeeping
def test_happy_path_files_mode_hash_and_status(env):
    rc = FakeTelus(3)
    write_cfg(env)
    write_req(env)
    code, st = run(env, rc)
    assert code == 0
    files = rec_files(env)
    assert [os.path.relpath(f, env.root) for f in files] == [
        "recordings/2026/08/FAKEREC1.mp3", "recordings/2026/08/FAKEREC2.mp3", "recordings/2026/08/FAKEREC3.mp3"]
    for f in files:
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
        rid = os.path.basename(f).split(".")[0]
        assert open(f, "rb").read() == body_for(rid)
    for d in ("recordings", "recordings/2026", "recordings/2026/08"):
        assert stat.S_IMODE(os.stat(os.path.join(env.root, d)).st_mode) == 0o700
    rows = {r["recording_id"]: r for r in db_rows(env, "SELECT * FROM telus_recordings")}
    assert set(rows) == {"FAKEREC1", "FAKEREC2", "FAKEREC3"}
    for rid, r in rows.items():
        assert r["status"] == "downloaded" and r["ext"] == "mp3" and r["bytes"] == len(body_for(rid))
        assert r["sha256"] == hashlib.sha256(body_for(rid)).hexdigest() and r["telus_id"].startswith("FAKECALL")
        assert r["downloaded_at"] and r["error_code"] is None
    job = st["job"]
    assert (job["state"], job["code"], job["mode"], job["id"]) == ("done", "ok", "recordings", HEX_A)
    assert (job["found"], job["downloaded"], job["already_had"], job["skipped"], job["failed"], job["remaining"],
            job["deleted_old"]) == (3, 3, 0, 0, 0, 0, 0)
    assert st["storage"]["recordings_count"] == 3 and st["storage"]["bytes"] == sum(len(body_for(f"FAKEREC{i}")) for i in (1, 2, 3))
    assert st["storage"]["max_bytes"] == 10 * worker.GB and st["connection"]["state"] == "ok"
    assert st["connection"]["environment"] == "sandbox" and st["connection"]["write_capable_scopes"] is False
    assert stat.S_IMODE(os.stat(os.path.join(env.bridge, "status.json")).st_mode) == 0o660
    assert os.listdir(os.path.join(env.bridge, "requests")) == []          # handled request deleted
    assert [n for n in os.listdir(env.bridge) if n.endswith(".tmp")] == []  # atomic write left no temp file
    # the pull stored the recording id on the call rows
    calls = {r["telus_id"]: r["recording_id"] for r in db_rows(env, "SELECT telus_id, recording_id FROM telus_calls")}
    assert calls == {"FAKECALL1": "FAKEREC1", "FAKECALL2": "FAKEREC2", "FAKECALL3": "FAKEREC3"}
    # only GETs, only the allowlisted content path, and only to the platform host
    assert {m for m, _, _, _ in rc.log} == {"GET", "POST"} and {h for _, h, _, _ in rc.log} == {PLATFORM}
    assert_status_clean(env)


def test_already_downloaded_is_skipped(env):
    rc = FakeTelus(3)
    write_cfg(env)
    write_req(env)
    run(env, rc)
    first = len(rc.content_gets())
    write_req(env, HEX_B)
    _, st = run(env, rc)
    assert len(rc.content_gets()) == first                      # nothing fetched again
    j = st["job"]
    assert (j["found"], j["already_had"], j["downloaded"], j["remaining"], j["code"]) == (3, 3, 0, 0, "ok")
    assert len(rec_files(env)) == 3


def test_max_items_and_cap_clamp_remaining(env):
    rc = FakeTelus(5)
    write_cfg(env, max_per_run_cap=2)
    write_req(env, max_items=1000)
    _, st = run(env, rc)
    assert st["job"]["downloaded"] == 2 and st["job"]["remaining"] == 3 and st["job"]["code"] == "ok"
    newest = sorted(os.path.basename(f) for f in rec_files(env))
    assert newest == ["FAKEREC4.mp3", "FAKEREC5.mp3"]           # newest first


def test_no_recordings_code(env):
    rc = FakeTelus(2)
    rc.records = [FakeTelus.call(1, recording=False)]
    write_cfg(env)
    write_req(env)
    _, st = run(env, rc)
    assert st["job"]["code"] == "no_recordings" and st["job"]["state"] == "done" and rec_files(env) == []


# ------------------------------------------------------------------ download rules
def test_redirect_to_allowed_host_works_and_sends_token_only_there(env):
    rc = FakeTelus(1)
    rc.content["FAKEREC1"] = [lambda r: httpx.Response(302, headers={"location": f"https://{MEDIA}/dl/FAKEFILE?sig=FAKESIG"})]
    rc.media["/dl/FAKEFILE"] = [lambda r: audio("FAKEREC1", ctype="audio/wav")]
    write_cfg(env)
    write_req(env)
    _, st = run(env, rc)
    assert st["job"]["downloaded"] == 1
    assert [os.path.basename(f) for f in rec_files(env)] == ["FAKEREC1.wav"]
    media_calls = [x for x in rc.log if x[1] == MEDIA]
    assert len(media_calls) == 1 and media_calls[0][3] == f"Bearer {rc.token}"
    assert_status_clean(env)


def test_redirect_to_other_host_fails_item_and_sends_nothing_there(env):
    rc = FakeTelus(1)
    rc.content["FAKEREC1"] = [lambda r: httpx.Response(302, headers={"location": "https://evil.example.invalid/steal"})]
    write_cfg(env)
    write_req(env)
    _, st = run(env, rc)
    assert st["job"]["downloaded"] == 0 and st["job"]["failed"] == 1 and st["job"]["code"] == "error"
    assert [x for x in rc.log if x[1] not in (PLATFORM,)] == []
    assert rec_files(env) == []
    assert db_rows(env, "SELECT error_code FROM telus_recordings") == [{"error_code": "redirect_blocked"}]


def test_non_audio_content_type_fails(env):
    rc = FakeTelus(1)
    rc.content["FAKEREC1"] = [lambda r: httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")]
    write_cfg(env)
    write_req(env)
    _, st = run(env, rc)
    assert (st["job"]["failed"], st["job"]["downloaded"], st["job"]["code"]) == (1, 0, "error")
    assert rec_files(env) == []
    assert db_rows(env, "SELECT status, error_code FROM telus_recordings") == [{"status": "failed", "error_code": "not_audio"}]


def test_oversize_by_content_length_skips_and_leaves_nothing(env, monkeypatch):
    monkeypatch.setattr(worker, "MAX_FILE_BYTES", 100)
    rc = FakeTelus(1)
    write_cfg(env)
    write_req(env)
    _, st = run(env, rc)
    assert (st["job"]["skipped"], st["job"]["downloaded"], st["job"]["failed"]) == (1, 0, 0)
    assert rec_files(env) == []
    assert db_rows(env, "SELECT status, error_code FROM telus_recordings") == [{"status": "skipped", "error_code": "too_large"}]


def test_oversize_mid_stream_aborts_and_leaves_no_partial_file(env, monkeypatch):
    monkeypatch.setattr(worker, "MAX_FILE_BYTES", 250)

    def streamed(request):
        def gen():
            for _ in range(10):
                yield b"x" * 100
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=gen())  # no content-length
    rc = FakeTelus(1)
    rc.content["FAKEREC1"] = [streamed]
    write_cfg(env)
    write_req(env)
    _, st = run(env, rc)
    assert st["job"]["skipped"] == 1 and st["job"]["downloaded"] == 0
    assert rec_files(env) == []                       # not even a hidden .part file
    assert st["storage"]["recordings_count"] == 0


def test_empty_body_fails_without_file(env):
    rc = FakeTelus(1)
    rc.content["FAKEREC1"] = [lambda r: httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"")]
    write_cfg(env)
    write_req(env)
    _, st = run(env, rc)
    assert st["job"]["failed"] == 1 and rec_files(env) == []


def test_429_then_success_uses_retry_after_and_bounded_sleeps(env):
    rc = FakeTelus(1)
    rc.content["FAKEREC1"] = [lambda r: httpx.Response(429, headers={"Retry-After": "7"}), lambda r: audio("FAKEREC1")]
    write_cfg(env)
    write_req(env)
    _, st = run(env, rc)
    assert st["job"]["downloaded"] == 1 and st["job"]["code"] == "ok"
    assert 7.0 in env.sleeps and max(env.sleeps) <= 90


def test_429_forever_stops_with_rate_limited(env):
    rc = FakeTelus(3)
    rc.content["FAKEREC3"] = [lambda r: httpx.Response(429, headers={"Retry-After": "500"})]
    write_cfg(env)
    write_req(env)
    _, st = run(env, rc)
    assert st["job"]["code"] == "rate_limited" and st["job"]["state"] == "failed"
    assert st["job"]["remaining"] == 3 and st["job"]["downloaded"] == 0
    assert len(rc.content_gets()) == 4                          # first try plus 3 bounded retries, then stop
    assert all(s <= 90 for s in env.sleeps)
    assert_status_clean(env)


def test_rate_header_zero_remaining_sleeps_window(env):
    rc = FakeTelus(2)
    rc.content["FAKEREC2"] = [lambda r: httpx.Response(200, headers={"content-type": "audio/mpeg", "X-Rate-Limit-Remaining": "0",
                                                                    "X-Rate-Limit-Window": "12"}, content=b"abc")]
    write_cfg(env)
    write_req(env)
    _, st = run(env, rc)
    assert st["job"]["downloaded"] == 2 and 12.0 in env.sleeps


def test_404_is_skipped_and_other_errors_fail_without_leaking(env):
    rc = FakeTelus(3)
    rc.content["FAKEREC1"] = [lambda r: httpx.Response(404, json={"message": f"gone {FAKE_JWT}"})]
    rc.content["FAKEREC2"] = [lambda r: httpx.Response(500, json={"message": f"boom {FAKE_JWT}"})]
    write_cfg(env)
    write_req(env)
    _, st = run(env, rc)
    j = st["job"]
    assert (j["downloaded"], j["skipped"], j["failed"], j["code"]) == (1, 1, 1, "partial")
    assert {r["recording_id"]: r["error_code"] for r in db_rows(env, "SELECT recording_id, error_code FROM telus_recordings")
            if r["error_code"]} == {"FAKEREC1": "gone", "FAKEREC2": "http_500"}
    assert_status_clean(env)
    # a failed item is retried on the next run, a skipped one is not
    rc.content["FAKEREC2"] = [lambda r: audio("FAKEREC2")]
    write_req(env, HEX_B)
    _, st = run(env, rc)
    assert st["job"]["downloaded"] == 1 and st["job"]["skipped"] == 1 and st["job"]["failed"] == 0


# ------------------------------------------------------------------ disk rules
def test_disk_cap_reached_stops(env, monkeypatch):
    monkeypatch.setattr(worker, "GB", 1000)                    # max_gb=1 means 1000 bytes
    rc = FakeTelus(3)
    for i in (1, 2, 3):
        rc.content[f"FAKEREC{i}"] = [lambda r: httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"y" * 600)]
    write_cfg(env, max_gb=1)
    write_req(env)
    _, st = run(env, rc)
    j = st["job"]
    assert j["code"] == "disk_cap_reached" and j["downloaded"] == 1 and j["remaining"] == 2 and j["state"] == "done"
    assert len(rec_files(env)) == 1 and st["storage"]["bytes"] == 600 and st["storage"]["max_bytes"] == 1000
    assert [r["recording_id"] for r in db_rows(env, "SELECT recording_id FROM telus_recordings")] == ["FAKEREC3"]


def test_disk_cap_already_full_fetches_nothing(env, monkeypatch):
    monkeypatch.setattr(worker, "GB", 100)
    os.makedirs(os.path.join(env.root, "recordings", "2026", "01"))
    with open(os.path.join(env.root, "recordings", "2026", "01", "OLD.mp3"), "wb") as f:
        f.write(b"z" * 150)
    rc = FakeTelus(2)
    write_cfg(env, max_gb=1)
    write_req(env)
    _, st = run(env, rc)
    assert st["job"]["code"] == "disk_cap_reached" and rc.content_gets() == []


def test_disk_low_stops(env, monkeypatch):
    monkeypatch.setattr(worker, "free_bytes", lambda p: 1024 ** 3)   # 1 GiB free, under the 5 GiB floor
    rc = FakeTelus(2)
    write_cfg(env)
    write_req(env)
    _, st = run(env, rc)
    assert st["job"]["code"] == "disk_low" and rc.content_gets() == [] and st["storage"]["free_bytes"] == 1024 ** 3


def test_disk_low_binds_the_allowance(env, monkeypatch):
    monkeypatch.setattr(worker, "free_bytes", lambda p: worker.LOW_DISK_BYTES + 50)   # 50 bytes of room
    rc = FakeTelus(1)
    write_cfg(env)
    write_req(env)
    _, st = run(env, rc)
    assert st["job"]["code"] == "disk_low" and rec_files(env) == []


# ------------------------------------------------------------------ refusals
def test_recordings_disabled_refuses_without_touching_telus(env):
    rc = FakeTelus(2)
    write_cfg(env, recordings_enabled=False)
    write_req(env)
    _, st = run(env, rc)
    assert st["job"]["code"] == "recordings_disabled" and st["job"]["state"] == "failed"
    assert rc.log == [] and rec_files(env) == []
    assert not os.path.exists(os.path.join(env.root, "private.db"))


def test_missing_or_invalid_config_means_disabled(env):
    rc = FakeTelus(1)
    write_req(env)                                        # no config.json at all
    assert run(env, rc)[1]["job"]["code"] == "recordings_disabled"
    for bad in ({"max_gb": 99}, {"extra": 1}, {"recordings_enabled": "yes"}, {"keep_days": 0}):
        write_cfg(env, **bad)
        write_req(env, HEX_B)
        assert run(env, rc)[1]["job"]["code"] == "recordings_disabled", bad
    assert rc.log == []


def test_write_capable_scope_refuses(env):
    rc = FakeTelus(2, scope=GOOD_SCOPE + " EditExtensions")
    write_cfg(env)
    write_req(env)
    _, st = run(env, rc)
    assert st["job"]["code"] == "write_scope_present" and st["connection"]["write_capable_scopes"] is True
    assert st["connection"]["state"] == "failed"
    assert rc.gets() == [] and rec_files(env) == []        # nothing but the login was sent
    assert_status_clean(env)


def test_login_failed_and_not_configured(env):
    def reject(request):
        return httpx.Response(401, json={"error": "invalid_client", "error_description": FAKE_SECRET})
    rc = FakeTelus(1)
    rc.handler_orig = rc.handler
    rc.handler = lambda r: reject(r) if r.method == "POST" else rc.handler_orig(r)
    write_cfg(env)
    write_req(env)
    assert run(env, rc)[1]["job"]["code"] == "login_failed"
    os.unlink(tc.credentials_path(env.root))
    write_req(env, HEX_B)
    _, st = run(env, FakeTelus(1))
    assert st["job"]["code"] == "not_configured" and st["connection"]["code"] == "not_configured"
    assert_status_clean(env)


def test_persistent_401_on_content_stops_with_login_failed(env):
    rc = FakeTelus(2)
    rc.content["FAKEREC2"] = [lambda r: httpx.Response(401)]
    rc.content["FAKEREC1"] = [lambda r: httpx.Response(401)]
    write_cfg(env)
    write_req(env)
    _, st = run(env, rc)
    assert st["job"]["code"] == "login_failed" and st["job"]["downloaded"] == 0


# ------------------------------------------------------------------ check mode
def test_check_mode_writes_connection_only(env):
    rc = FakeTelus(4)
    write_req(env, mode="check", days=None, max_items=None)
    _, st = run(env, rc)
    assert st["connection"]["state"] == "ok" and st["connection"]["calls_last_7_days"] == 4
    assert st["connection"]["environment"] == "sandbox" and st["connection"]["checked_at"]
    assert st["job"]["mode"] == "check" and st["job"]["state"] == "done" and st["job"]["code"] == "ok"
    assert rec_files(env) == [] and not os.path.exists(os.path.join(env.root, "private.db"))
    assert os.listdir(os.path.join(env.bridge, "requests")) == []
    assert_status_clean(env)


def test_check_mode_write_scope_and_missing_scope(env):
    write_req(env, mode="check", days=1, max_items=1)
    _, st = run(env, FakeTelus(1, scope=GOOD_SCOPE + " SMS"))
    assert st["job"]["code"] == "write_scope_present" and st["job"]["state"] == "failed"
    write_req(env, HEX_B, mode="check", days=1, max_items=1)
    _, st = run(env, FakeTelus(1, scope="ReadAccounts"))
    assert st["job"]["code"] == "error" and st["connection"]["state"] == "failed"


# ------------------------------------------------------------------ request files
def test_bad_request_files_are_rejected_deleted_and_counted(env):
    rdir = os.path.join(env.bridge, "requests")
    outside = env.tmp / "outside.json"
    good = {"v": 1, "id": HEX_B, "mode": "check", "requested_by": "Fake Admin", "requested_at": "2026-08-10T17:59:00Z"}
    outside.write_text(json.dumps(good))
    open(os.path.join(rdir, "not-a-job-id.json"), "w").write(json.dumps(good))           # bad name
    open(os.path.join(rdir, "c" * 32 + ".json"), "w").write("{this is not json")           # malformed
    open(os.path.join(rdir, "d" * 32 + ".json"), "w").write(json.dumps({**good, "id": "d" * 32}) + " " * 5000)  # too big
    os.symlink(str(outside), os.path.join(rdir, "e" * 32 + ".json"))                      # symlink
    open(os.path.join(rdir, "f" * 32 + ".json"), "w").write(json.dumps({**good, "id": "f" * 32, "extra": 1}))   # unknown key
    open(os.path.join(rdir, "1" * 32 + ".json"), "w").write(json.dumps(good))              # id differs from the name
    os.mkdir(os.path.join(rdir, "2" * 32 + ".json"))                                       # not a regular file
    open(os.path.join(rdir, "3" * 32 + ".json"), "w").write(json.dumps({**good, "id": "3" * 32, "days": 31, "mode": "recordings", "max_items": 5}))
    write_req(env, HEX_A, mode="check", days=None, max_items=None)                         # one good request
    rc = FakeTelus(2)
    _, st = run(env, rc)
    assert st["rejected_requests"] == 8
    assert os.listdir(rdir) == []
    assert outside.exists()                                  # the symlink was removed, not its target
    assert st["job"]["id"] == HEX_A and st["job"]["state"] == "done"
    assert_status_clean(env)
    # the counter is cumulative across runs
    open(os.path.join(rdir, "not-a-job-id.json"), "w").write("x")
    _, st = run(env, FakeTelus(1))
    assert st["rejected_requests"] == 9


@pytest.mark.parametrize("over", [
    {"v": 2}, {"v": True}, {"mode": "delete"}, {"days": 0}, {"days": 31}, {"days": "5"}, {"days": True},
    {"max_items": 0}, {"max_items": 1001}, {"requested_by": ""}, {"requested_by": "x" * 61},
    {"requested_by": "line\nbreak"}, {"requested_at": "yesterday"}, {"requested_at": "2026-08-10T17:59:00"},
    {"unknown": 1}, {"id": "A" * 32},
])
def test_request_schema_rejects(env, over):
    base = {"v": 1, "id": HEX_A, "mode": "recordings", "days": 3, "max_items": 5, "requested_by": "Fake Admin",
            "requested_at": "2026-08-10T17:59:00Z"}
    base.update(over)
    path = os.path.join(env.bridge, "requests", f"{HEX_A}.json")
    open(path, "w").write(json.dumps(base))
    rc = FakeTelus(1)
    code, st = run(env, rc)
    assert code == 0 and st["rejected_requests"] == 1 and st["job"]["state"] == "idle"
    assert not os.path.exists(path) and rc.log == []


def test_requests_dir_symlink_is_ignored(env):
    real = env.tmp / "elsewhere"
    real.mkdir()
    os.rmdir(os.path.join(env.bridge, "requests"))
    os.symlink(str(real), os.path.join(env.bridge, "requests"))
    (real / f"{HEX_A}.json").write_text(json.dumps({"v": 1, "id": HEX_A, "mode": "check", "requested_by": "x",
                                                   "requested_at": "2026-08-10T17:59:00Z"}))
    rc = FakeTelus(1)
    assert worker.Worker(env.root, env.bridge, "", "", sleep=lambda s: None).run() == 0
    assert rc.log == [] and (real / f"{HEX_A}.json").exists()


def test_two_requests_processed_oldest_first_one_at_a_time(env, monkeypatch):
    rc = FakeTelus(2)
    write_cfg(env)
    write_req(env, HEX_B, by="Second Person", mtime=2_000_000_000)
    write_req(env, HEX_A, by="First Person", mtime=1_900_000_000)
    order = []
    real = worker.Worker._run_job

    def spy(self, req):
        order.append((req.id, req.requested_by))
        return real(self, req)
    monkeypatch.setattr(worker.Worker, "_run_job", spy)
    _, st = run(env, rc)
    assert order == [(HEX_A, "First Person"), (HEX_B, "Second Person")]
    assert st["job"]["id"] == HEX_B and st["job"]["state"] == "done"
    assert os.listdir(os.path.join(env.bridge, "requests")) == []
    states = [json.loads(w)["job"]["state"] for w in env.writes]
    assert "queued" in states and "running" in states and states[-1] == "done"


def test_no_valid_requests_and_no_bad_ones_writes_nothing(env):
    assert make_worker(env, FakeTelus(1)).run() == 0
    assert not os.path.exists(os.path.join(env.bridge, "status.json"))


def test_progress_status_every_ten_items(env):
    rc = FakeTelus(25)
    write_cfg(env)
    write_req(env, max_items=1000)
    _, st = run(env, rc)
    assert st["job"]["downloaded"] == 25
    mids = [json.loads(w)["job"] for w in env.writes if json.loads(w)["job"]["state"] == "running"]
    assert [m["downloaded"] for m in mids if m["downloaded"] in (10, 20)] == [10, 20]


# ------------------------------------------------------------------ cleanup
def test_keep_days_cleanup_deletes_only_old_own_files(env, monkeypatch):
    rc = FakeTelus(2)
    write_cfg(env, keep_days=30)
    write_req(env)
    run(env, rc)
    d = os.path.join(env.root, "recordings", "2026", "08")
    decoy = os.path.join(d, "NOTMINE.mp3")
    precious = os.path.join(env.root, "private-notes.txt")
    open(decoy, "wb").write(b"not ours")
    open(precious, "wb").write(b"keep me")
    conn = sqlite3.connect(os.path.join(env.root, "private.db"))
    conn.execute("UPDATE telus_recordings SET downloaded_at='2026-06-01T00:00:00Z' WHERE recording_id='FAKEREC1'")
    conn.commit()
    conn.close()
    seen = {}
    real_unlink = os.unlink

    def spy_unlink(path, *a, **k):
        if str(path).endswith("FAKEREC1.mp3"):
            seen["content"] = open(path, "rb").read()
        return real_unlink(path, *a, **k)
    write_req(env, HEX_B)
    with monkeypatch.context() as m:
        m.setattr(os, "unlink", spy_unlink)
        _, st = run(env, rc)
    assert st["job"]["deleted_old"] == 1
    assert sorted(os.listdir(d)) == ["FAKEREC2.mp3", "NOTMINE.mp3"] and os.path.exists(precious)
    assert seen["content"] == b"\x00" * len(body_for("FAKEREC1"))      # overwritten before unlink
    assert db_rows(env, "SELECT status, error_code FROM telus_recordings WHERE recording_id='FAKEREC1'") == [
        {"status": "skipped", "error_code": "expired"}]
    assert st["job"]["downloaded"] == 0                                 # not fetched again
    assert_status_clean(env)


def test_cleanup_ignores_symlinks_and_removes_empty_folders(env):
    conn = dbm.connect(os.path.join(env.root, "private.db"), create=True)
    d = os.path.join(env.root, "recordings", "2025", "01")
    os.makedirs(d)
    target = env.tmp / "victim.mp3"
    target.write_bytes(b"outside")
    os.symlink(str(target), os.path.join(d, "LINKED.mp3"))
    recordings.upsert(conn, "LINKED", "FAKECALL9", "downloaded", nbytes=7, sha256="0" * 64, ext="mp3",
                      downloaded_at="2020-01-01T00:00:00Z")
    open(os.path.join(d, "GONE1.mp3"), "wb").write(b"abc")
    recordings.upsert(conn, "GONE1", "FAKECALL8", "downloaded", nbytes=3, sha256="0" * 64, ext="mp3",
                      downloaded_at="2020-01-01T00:00:00Z")
    assert recordings.cleanup(env.root, conn, 30, NOW) == 1
    assert target.read_bytes() == b"outside"
    conn.close()


def test_secure_delete_overwrites_then_unlinks(tmp_path, monkeypatch):
    p = tmp_path / "x.mp3"
    p.write_bytes(b"SECRET" * 1000)
    seen = {}
    real_unlink = os.unlink

    def spy(path, *a, **k):
        seen["data"] = open(path, "rb").read()
        return real_unlink(path, *a, **k)
    monkeypatch.setattr(os, "unlink", spy)
    assert recordings.secure_delete(str(p)) is True
    assert seen["data"] == b"\x00" * 6000 and not p.exists()
    assert recordings.secure_delete(str(tmp_path)) is False             # a directory is never touched


# ------------------------------------------------------------------ secrets and leaks
def test_secrets_ids_and_urls_never_in_logs_stdout_or_status(env, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    rc = FakeTelus(4)
    rc.content["FAKEREC1"] = [lambda r: httpx.Response(302, headers={"location": f"https://{MEDIA}/dl/FAKEFILE?sig=FAKESIG"})]
    rc.media["/dl/FAKEFILE"] = [lambda r: httpx.Response(500, json={"message": FAKE_JWT})]
    rc.content["FAKEREC2"] = [lambda r: httpx.Response(500, json={"message": f"boom {FAKE_SECRET}"})]
    rc.content["FAKEREC3"] = [lambda r: httpx.Response(302, headers={"location": "https://evil.example.invalid/x?t=FAKESIG"})]
    write_cfg(env)
    write_req(env)
    run(env, rc)
    out = capsys.readouterr()
    blob = caplog.text + out.out + out.err
    for needle in (*SECRETS, "FAKEREC", "FAKECALL", "FAKEFILE", "FAKESIG", MEDIA, "evil.example", FAKE_DIGITS, FAKE_NAME):
        assert needle not in blob, needle
    assert_status_clean(env)


def test_unexpected_exception_text_never_reaches_status(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError(f"secret detail {FAKE_JWT} FAKEREC1")
    monkeypatch.setattr(calllog, "pull", boom)
    write_cfg(env)
    write_req(env)
    _, st = run(env, FakeTelus(1))
    assert st["job"]["code"] == "error" and st["job"]["state"] == "failed"
    assert_status_clean(env)


# ------------------------------------------------------------------ client: redirect and read-only rules
def client_with(handler, env_name="sandbox"):
    return ReadOnlyClient(creds(env_name), transport=httpx.MockTransport(handler), sleep=lambda s: None)


def login_ok(request):
    return httpx.Response(200, json={"access_token": "FAKE_ACCESS_TOKEN_C", "expires_in": 3600, "scope": GOOD_SCOPE})


@pytest.mark.parametrize("location", [
    "https://evil.example.invalid/steal",
    "http://media.devtest.ringcentral.com/dl",                       # not https
    "https://media.devtest.ringcentral.com.evil.example.invalid/dl",  # look-alike host
    "https://evil.example.invalid@media.devtest.ringcentral.com/dl",  # user info
    "https://media.devtest.ringcentral.com:8443/dl",                  # odd port
    "https://media.ringcentral.com/dl",                               # production host while in sandbox
    "//evil.example.invalid/dl",
    "ftp://media.devtest.ringcentral.com/dl",
    "",
])
def test_redirect_to_non_allowlisted_target_raises_and_authorization_never_sent(tmp_path, location):
    hosts_seen = []

    def h(request):
        if request.method == "POST":
            return login_ok(request)
        hosts_seen.append((request.url.host, request.headers.get("authorization")))
        return httpx.Response(302, headers={"location": location})
    c = client_with(h)
    dest = str(tmp_path / "d.part")
    with pytest.raises(ReadOnlyViolation):
        c.download_recording("FAKEREC1", dest, 1000)
    assert hosts_seen == [(PLATFORM, "Bearer FAKE_ACCESS_TOKEN_C")]   # the one allowed request only
    assert not os.path.exists(dest)


def test_second_redirect_is_not_followed(tmp_path):
    seen = []

    def h(request):
        if request.method == "POST":
            return login_ok(request)
        seen.append(request.url.host)
        return httpx.Response(302, headers={"location": f"https://{MEDIA}/dl"})
    with pytest.raises(TelusDownloadError) as e:
        client_with(h).download_recording("FAKEREC1", str(tmp_path / "d.part"), 1000)
    assert e.value.reason == "too_many_redirects" and seen == [PLATFORM, MEDIA]


def test_production_env_allows_production_media_host_only(tmp_path):
    def h(request):
        if request.method == "POST":
            return login_ok(request)
        if request.url.host == "platform.ringcentral.com":
            return httpx.Response(302, headers={"location": "https://media.ringcentral.com/dl"})
        assert request.url.host == "media.ringcentral.com" and request.method == "GET"
        return httpx.Response(200, headers={"content-type": "application/octet-stream"}, content=b"abc")
    dest = str(tmp_path / "d.part")
    res = client_with(h, "production").download_recording("FAKEREC1", dest, 1000)
    assert res.bytes == 3 and res.sha256 == hashlib.sha256(b"abc").hexdigest()
    assert stat.S_IMODE(os.stat(dest).st_mode) == 0o600


@pytest.mark.parametrize("rid", ["../x", "a/b", "a b", "", "x" * 65, "a?b=1", "a%2fb"])
def test_download_recording_rejects_odd_ids_before_any_request(tmp_path, rid):
    sent = []
    c = client_with(lambda r: sent.append(r) or login_ok(r))
    with pytest.raises(ReadOnlyViolation):
        c.download_recording(rid, str(tmp_path / "d.part"), 10)
    assert sent == []


def test_download_requires_http_200_and_cleans_up(tmp_path):
    def h(request):
        if request.method == "POST":
            return login_ok(request)
        return httpx.Response(206, headers={"content-type": "audio/mpeg"}, content=b"abc")
    dest = str(tmp_path / "d.part")
    with pytest.raises(TelusDownloadError) as e:
        client_with(h).download_recording("FAKEREC1", dest, 100)
    assert e.value.reason == "bad_status" and not os.path.exists(dest)


def test_download_never_overwrites_an_existing_temp_file(tmp_path):
    dest = tmp_path / "d.part"
    dest.write_bytes(b"precious")
    c = client_with(lambda r: login_ok(r) if r.method == "POST" else httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"abc"))
    with pytest.raises(FileExistsError):
        c.download_recording("FAKEREC1", str(dest), 100)
    assert dest.read_bytes() == b"precious"


def test_download_error_messages_are_clean(tmp_path):
    def h(request):
        if request.method == "POST":
            return login_ok(request)
        return httpx.Response(500, json={"message": FAKE_JWT})
    with pytest.raises(TelusHTTPError) as e:
        client_with(h).download_recording("FAKEREC1", str(tmp_path / "d.part"), 100)
    msg = str(e.value)
    assert e.value.status == 500 and "FAKEREC1" not in msg and FAKE_JWT not in msg and "http" not in msg.lower().replace("http 500", "")


# ------------------------------------------------------------------ database
def test_migration_adds_recording_id_idempotently_and_pull_backfills(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute("""CREATE TABLE telus_calls (telus_id TEXT PRIMARY KEY, start_local TEXT NOT NULL, date TEXT NOT NULL,
        hour INTEGER NOT NULL, weekday INTEGER NOT NULL, duration INTEGER NOT NULL DEFAULT 0, direction TEXT NOT NULL,
        result TEXT NOT NULL, ext_id TEXT, has_recording INTEGER NOT NULL DEFAULT 0, from_hash TEXT, to_hash TEXT,
        synced_at TEXT NOT NULL)""")
    old.execute("INSERT INTO telus_calls VALUES ('FAKECALL1','2026-08-09T10:01:00-07:00','2026-08-09',10,6,31,'inbound',"
                "'answered','1234',1,NULL,NULL,'x')")
    old.commit()
    old.close()
    for _ in range(2):                                      # twice: must be idempotent
        conn = dbm.connect(path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(telus_calls)")}
        assert "recording_id" in cols
        assert conn.execute("SELECT recording_id FROM telus_calls").fetchone()[0] is None   # existing row kept, no id yet
        assert conn.execute("SELECT COUNT(*) FROM telus_recordings").fetchone()[0] == 0
        conn.close()
    conn = dbm.connect(path)
    rc = FakeTelus(1)
    with ReadOnlyClient(creds(), transport=rc.transport(), sleep=lambda s: None) as client:
        client.login()
        calllog.pull(client, conn, b"s" * 32, days=30, now=NOW)
    assert conn.execute("SELECT recording_id FROM telus_calls WHERE telus_id='FAKECALL1'").fetchone()[0] == "FAKEREC1"
    assert conn.execute("SELECT COUNT(*) FROM telus_calls").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO telus_recordings(recording_id, status) VALUES ('X','weird')")
    conn.close()


def test_worker_pull_does_not_move_the_incremental_sync_cursor(env):
    rc = FakeTelus(1)
    write_cfg(env)
    write_req(env)
    run(env, rc)
    assert db_rows(env, "SELECT * FROM telus_sync_state") == []


# ------------------------------------------------------------------ CLI
def test_cli_telus_worker_end_to_end(env, monkeypatch, capsys):
    rc = FakeTelus(2)
    real = httpx.Client

    class Patched(real):
        def __init__(self, *a, **kw):
            kw["transport"] = rc.transport()
            super().__init__(*a, **kw)
    monkeypatch.setattr(httpx, "Client", Patched)

    class FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(worker, "datetime", FrozenDT)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    monkeypatch.setattr(worker, "FILE_SLEEP", 0)
    write_cfg(env)
    write_req(env)
    assert cli.main(["telus-worker", "--root", env.root, "--bridge", env.bridge]) == 0
    out = capsys.readouterr()
    assert "jobs=1" in out.out and "FAKE" not in out.out + out.err
    st = read_status(env)
    assert st["job"]["downloaded"] == 2 and len(rec_files(env)) == 2
    assert cli.main(["telus-worker", "--root", env.root, "--bridge", str(env.tmp / "nope")]) == 2
