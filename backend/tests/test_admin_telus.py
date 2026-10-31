"""Admin TELUS bridge: permissions, request files, settings, and hostile status files."""
from __future__ import annotations

import dataclasses
import json
import os
import re
import stat
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import config
from app.api import admin_telus
from app.models import Event
from app.services import timeutil

HEX32 = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture()
def bridge(env, tmp_path):
    d = tmp_path / "telus"
    d.mkdir()
    (d / "requests").mkdir()
    config.set_settings(dataclasses.replace(env.settings, telus_dir=str(d)))
    yield d
    config.set_settings(env.settings)


def good_status(**job):
    now = timeutil.now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    j = {"id": None, "mode": None, "state": "idle", "requested_by": None, "requested_at": None, "started_at": None,
         "finished_at": None, "code": "ok", "found": 0, "downloaded": 0, "already_had": 0, "skipped": 0, "failed": 0,
         "remaining": 0, "deleted_old": 0}
    j.update(job)
    return {"v": 1, "updated_at": now,
            "connection": {"state": "ok", "code": "ok", "checked_at": now, "environment": "production",
                           "write_capable_scopes": False, "calls_last_7_days": 12},
            "job": j, "storage": {"recordings_count": 3, "bytes": 3000, "free_bytes": 10**10, "max_bytes": 10**10},
            "rejected_requests": 0}


def put_status(d, obj):
    (d / "status.json").write_text(json.dumps(obj))


def enable(garg, **kw):
    body = {"recordings_enabled": True, "max_gb": 10, "keep_days": 30, "max_per_run_cap": 200, **kw}
    r = garg.put("/api/admin/telus/settings", body)
    assert r.status_code == 200, r.text


def request_files(d):
    return sorted((d / "requests").iterdir())


def leftovers(d):
    return [p.name for p in d.iterdir() if p.name.startswith(".tmp-")]


# ---------------------------------------------------------------- permissions
ROUTES = [("GET", "/api/admin/telus", None), ("PUT", "/api/admin/telus/settings",
          {"recordings_enabled": False, "max_gb": 10, "keep_days": 30, "max_per_run_cap": 200}),
          ("POST", "/api/admin/telus/check", None), ("POST", "/api/admin/telus/recordings", {"days": 7, "max_items": 10})]


@pytest.mark.parametrize("username", ["priya", "anita"])
def test_desk_and_nurse_get_403(env, bridge, username):
    api = env.client()
    assert api.login(username).status_code == 200
    for method, path, body in ROUTES:
        r = api.c.request(method, path, json=body or {}, headers={"X-CSRF-Token": api.csrf})
        assert r.status_code == 403, (method, path)
    assert request_files(bridge) == []


def test_owner_without_admin_flag_gets_403(env, bridge, garg):
    users = {u["username"]: u for u in garg.get("/api/admin/users").json()["items"]}
    assert garg.patch(f"/api/admin/users/{users['krish']['id']}", {"is_admin": False}).status_code == 200
    k = env.client()
    k.login("krish")
    for method, path, body in ROUTES:
        r = k.c.request(method, path, json=body or {}, headers={"X-CSRF-Token": k.csrf})
        assert r.status_code == 403, (method, path)


def test_admin_owner_ok_and_anonymous_401(env, bridge, garg):
    assert garg.get("/api/admin/telus").status_code == 200
    anon = env.client()
    for method, path, body in ROUTES:
        assert anon.c.request(method, path, json=body or {}).status_code == 401


def test_permission_listed_for_admins_only(garg, priya):
    assert "admin.telus" in garg.get("/api/auth/me").json()["perms"]
    assert "admin.telus" not in priya.get("/api/auth/me").json()["perms"]


def test_csrf_required_on_writes(env, bridge, garg):
    for method, path, body in ROUTES[1:]:
        r = garg.c.request(method, path, json=body or {})
        assert r.status_code == 403 and r.json() == {"detail": "csrf"}, path
        r = garg.c.request(method, path, json=body or {}, headers={"X-CSRF-Token": "wrong"})
        assert r.status_code == 403, path
    assert request_files(bridge) == []
    assert not (bridge / "config.json").exists()


# ---------------------------------------------------------------- GET
def test_get_defaults_when_nothing_written(bridge, garg):
    j = garg.get("/api/admin/telus").json()
    assert j["status"] is None and j["bridge_ok"] is True and j["busy"] is False
    assert j["settings"]["recordings_enabled"] is False
    assert (j["settings"]["max_gb"], j["settings"]["keep_days"], j["settings"]["max_per_run_cap"]) == (10, 30, 200)
    assert set(j["messages"]) == {"ok", "partial", "recordings_disabled", "not_configured", "login_failed", "write_scope_present",
                                  "disk_cap_reached", "disk_low", "rate_limited", "no_recordings", "error"}
    assert all(isinstance(t, str) and t and "\u2014" not in t and "\u2013" not in t for t in j["messages"].values())


def test_get_returns_valid_status(bridge, garg):
    put_status(bridge, good_status())
    j = garg.get("/api/admin/telus").json()
    assert j["status"]["connection"]["calls_last_7_days"] == 12
    assert j["status"]["storage"]["recordings_count"] == 3
    assert j["status"]["job"]["state"] == "idle"
    assert "v" not in j["status"]


def test_missing_bridge_dir(env, tmp_path, garg):
    config.set_settings(dataclasses.replace(env.settings, telus_dir=str(tmp_path / "nope" / "deeper")))
    j = garg.get("/api/admin/telus").json()
    assert j["bridge_ok"] is False and j["status"] is None
    r = garg.post("/api/admin/telus/check")
    assert r.status_code == 503
    assert garg.put("/api/admin/telus/settings", {"recordings_enabled": False, "max_gb": 1, "keep_days": 1, "max_per_run_cap": 1}).status_code == 503
    assert not (tmp_path / "nope").exists()


def test_bridge_dir_that_is_a_symlink_is_not_used(env, tmp_path, garg):
    real = tmp_path / "real"
    (real / "requests").mkdir(parents=True)
    put_status(real, good_status())
    link = tmp_path / "link"
    link.symlink_to(real)
    config.set_settings(dataclasses.replace(env.settings, telus_dir=str(link)))
    j = garg.get("/api/admin/telus").json()
    assert j["bridge_ok"] is False and j["status"] is None
    assert garg.post("/api/admin/telus/check").status_code == 503
    assert list((real / "requests").iterdir()) == []


# ---------------------------------------------------------------- hostile status files
def _hostile_cases(d):
    big = json.dumps({"pad": "x" * 70000})
    cases = {
        "huge_garbage": lambda: (d / "status.json").write_text(big),
        "huge_valid_json_over_limit": lambda: (d / "status.json").write_text(json.dumps({**good_status(), "pad": "x" * 70000})),
        "not_json": lambda: (d / "status.json").write_text("this is not json {{{"),
        "binary": lambda: (d / "status.json").write_bytes(os.urandom(2000)),
        "empty": lambda: (d / "status.json").write_text(""),
        "json_list": lambda: (d / "status.json").write_text("[1,2,3]"),
        "json_string": lambda: (d / "status.json").write_text('"hello"'),
        "deep_nesting": lambda: (d / "status.json").write_text("[" * 30000 + "]" * 30000),
        "wrong_version": lambda: (d / "status.json").write_text(json.dumps({**good_status(), "v": 2})),
        "bad_state": lambda: (d / "status.json").write_text(json.dumps(good_status(state="exploding"))),
        "bad_code": lambda: (d / "status.json").write_text(json.dumps(good_status(code="free text from TELUS"))),
        "string_count": lambda: (d / "status.json").write_text(json.dumps(good_status(found="12"))),
        "bool_count": lambda: (d / "status.json").write_text(json.dumps(good_status(found=True))),
        "negative_count": lambda: (d / "status.json").write_text(json.dumps(good_status(found=-5))),
        "bad_job_id": lambda: (d / "status.json").write_text(json.dumps(good_status(id="../../etc/passwd"))),
        "bad_time": lambda: (d / "status.json").write_text(json.dumps({**good_status(), "updated_at": "yesterday-ish"})),
        "missing_sections": lambda: (d / "status.json").write_text(json.dumps({"v": 1, "updated_at": "2026-01-01T00:00:00Z"})),
        "directory": lambda: (d / "status.json").mkdir(),
    }
    return cases


def test_hostile_status_files_never_raise(bridge, garg):
    for name, make in _hostile_cases(bridge).items():
        p = bridge / "status.json"
        if p.is_dir():
            p.rmdir()
        elif p.exists() or p.is_symlink():
            p.unlink()
        make()
        r = garg.get("/api/admin/telus")
        assert r.status_code == 200, name
        assert r.json()["status"] is None, name
        assert r.json()["busy"] is False, name
    # and a bad one never blocks a request
    p = bridge / "status.json"
    p.rmdir()
    p.write_text("garbage")
    assert garg.post("/api/admin/telus/check").status_code == 200


def test_symlinked_status_is_not_followed(bridge, tmp_path, garg):
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(good_status()))
    (bridge / "status.json").symlink_to(target)
    assert garg.get("/api/admin/telus").json()["status"] is None
    # same for a symlinked config
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"v": 1, "recordings_enabled": True, "max_gb": 5, "keep_days": 5, "max_per_run_cap": 5}))
    (bridge / "config.json").symlink_to(cfg)
    assert garg.get("/api/admin/telus").json()["settings"]["recordings_enabled"] is False


def test_unknown_keys_ignored_and_names_sanitised(bridge, garg):
    st = good_status(requested_by="Ann\x00\x07 Lee")
    st["extra_thing"] = "ignored"
    st["connection"]["note"] = "ignored"
    put_status(bridge, st)
    j = garg.get("/api/admin/telus").json()
    assert j["status"]["job"]["requested_by"] == "Ann Lee"
    assert "extra_thing" not in j["status"] and "note" not in j["status"]["connection"]


def test_stale_running_status_shows_failed(bridge, garg):
    st = good_status(state="running", mode="recordings", id="a" * 32)
    old = (timeutil.now_utc() - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    st["updated_at"] = old
    put_status(bridge, st)
    j = garg.get("/api/admin/telus").json()
    assert j["status"]["job"]["state"] == "failed" and j["status"]["job"]["code"] == "error"
    assert j["busy"] is False
    assert garg.post("/api/admin/telus/check").status_code == 200  # stale does not block


def test_fresh_running_status_is_kept(bridge, garg):
    put_status(bridge, good_status(state="running", mode="check", id="b" * 32))
    j = garg.get("/api/admin/telus").json()
    assert j["status"]["job"]["state"] == "running" and j["busy"] is True


def test_invalid_config_file_gives_defaults(bridge, garg):
    for bad in ({"v": 1, "recordings_enabled": True, "max_gb": 999, "keep_days": 30, "max_per_run_cap": 200},
                {"v": 1, "recordings_enabled": "yes", "max_gb": 10, "keep_days": 30, "max_per_run_cap": 200},
                {"v": 1, "recordings_enabled": True, "max_gb": 10, "keep_days": 0, "max_per_run_cap": 200},
                {"recordings_enabled": True}):
        (bridge / "config.json").write_text(json.dumps(bad))
        s = garg.get("/api/admin/telus").json()["settings"]
        assert s["recordings_enabled"] is False and s["max_gb"] == 10
    (bridge / "config.json").write_text("x" * 100000)
    assert garg.get("/api/admin/telus").json()["settings"]["recordings_enabled"] is False


# ---------------------------------------------------------------- settings
def test_settings_write_atomic_mode_and_event(env, bridge, garg):
    r = garg.put("/api/admin/telus/settings", {"recordings_enabled": True, "max_gb": 12, "keep_days": 14, "max_per_run_cap": 300})
    assert r.status_code == 200
    p = bridge / "config.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o660
    data = json.loads(p.read_text())
    assert data["v"] == 1 and data["recordings_enabled"] is True and data["max_gb"] == 12
    assert data["keep_days"] == 14 and data["max_per_run_cap"] == 300
    assert data["updated_by"] == "Dr. Garg" or data["updated_by"]
    assert data["updated_at"].endswith("Z")
    assert leftovers(bridge) == []
    assert garg.get("/api/admin/telus").json()["settings"]["max_gb"] == 12
    with env.session() as s:
        evs = s.scalars(select(Event).where(Event.action == "telus_settings_changed")).all()
    assert len(evs) == 1 and evs[0].tool == "Admin"


@pytest.mark.parametrize("body", [
    {"recordings_enabled": True, "max_gb": 0, "keep_days": 30, "max_per_run_cap": 200},
    {"recordings_enabled": True, "max_gb": 51, "keep_days": 30, "max_per_run_cap": 200},
    {"recordings_enabled": True, "max_gb": 10, "keep_days": 0, "max_per_run_cap": 200},
    {"recordings_enabled": True, "max_gb": 10, "keep_days": 91, "max_per_run_cap": 200},
    {"recordings_enabled": True, "max_gb": 10, "keep_days": 30, "max_per_run_cap": 0},
    {"recordings_enabled": True, "max_gb": 10, "keep_days": 30, "max_per_run_cap": 1001},
    {"recordings_enabled": "maybe", "max_gb": 10, "keep_days": 30, "max_per_run_cap": 200},
    {"recordings_enabled": True, "max_gb": "ten", "keep_days": 30, "max_per_run_cap": 200},
    {"recordings_enabled": True, "max_gb": 10, "keep_days": 30},
    {"recordings_enabled": True, "max_gb": 10, "keep_days": 30, "max_per_run_cap": 200, "path": "/srv/x"},
])
def test_settings_validation(bridge, garg, body):
    assert garg.put("/api/admin/telus/settings", body).status_code == 422
    assert not (bridge / "config.json").exists()
    assert leftovers(bridge) == []


def test_settings_range_edges_accepted(bridge, garg):
    assert garg.put("/api/admin/telus/settings", {"recordings_enabled": False, "max_gb": 1, "keep_days": 1, "max_per_run_cap": 1}).status_code == 200
    assert garg.put("/api/admin/telus/settings", {"recordings_enabled": True, "max_gb": 50, "keep_days": 90, "max_per_run_cap": 1000}).status_code == 200
    s = garg.get("/api/admin/telus").json()["settings"]
    assert (s["max_gb"], s["keep_days"], s["max_per_run_cap"]) == (50, 90, 1000)


def test_settings_overwrite_replaces_planted_symlink_without_following(bridge, tmp_path, garg):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    (bridge / "config.json").symlink_to(victim)
    enable(garg)
    assert victim.read_text() == "keep me"
    assert not (bridge / "config.json").is_symlink()


# ---------------------------------------------------------------- requests
def test_check_request_file(env, bridge, garg):
    r = garg.post("/api/admin/telus/check")
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert HEX32.match(job_id)
    files = request_files(bridge)
    assert [f.name for f in files] == [job_id + ".json"]
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o660
    assert files[0].stat().st_size <= 4096
    body = json.loads(files[0].read_text())
    assert set(body) == {"v", "id", "mode", "days", "max_items", "requested_by", "requested_at"}
    assert body["v"] == 1 and body["id"] == job_id and body["mode"] == "check"
    assert 1 <= body["days"] <= 30 and 1 <= body["max_items"] <= 1000
    assert 0 < len(body["requested_by"]) <= 60 and body["requested_by"].isprintable()
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", body["requested_at"])
    assert leftovers(bridge) == []
    with env.session() as s:
        evs = s.scalars(select(Event).where(Event.action == "telus_check_requested")).all()
    assert len(evs) == 1 and evs[0].tool == "Admin"


def test_recordings_refused_while_disabled(env, bridge, garg):
    r = garg.post("/api/admin/telus/recordings", {"days": 7, "max_items": 50})
    assert r.status_code == 400
    assert "turned off" in r.json()["detail"]
    assert request_files(bridge) == []
    with env.session() as s:
        assert s.scalars(select(Event).where(Event.action == "telus_recordings_requested")).all() == []


def test_recordings_request_file_and_event(env, bridge, garg):
    enable(garg)
    r = garg.post("/api/admin/telus/recordings", {"days": 5, "max_items": 40})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    f = bridge / "requests" / (job_id + ".json")
    assert stat.S_IMODE(f.stat().st_mode) == 0o660
    body = json.loads(f.read_text())
    assert body["mode"] == "recordings" and body["days"] == 5 and body["max_items"] == 40 and body["id"] == job_id
    with env.session() as s:
        evs = s.scalars(select(Event).where(Event.action == "telus_recordings_requested")).all()
    assert len(evs) == 1 and evs[0].tool == "Admin"


@pytest.mark.parametrize("body", [
    {"days": 0, "max_items": 10}, {"days": 31, "max_items": 10}, {"days": 7, "max_items": 0}, {"days": 7, "max_items": 1001},
    {"days": "7", "max_items": 10}, {"days": 7}, {"max_items": 10}, {"days": 7, "max_items": 10, "mode": "check"},
    {"days": 7, "max_items": 10, "id": "../../x"}, {},
])
def test_recordings_validation(bridge, garg, body):
    enable(garg)
    assert garg.post("/api/admin/telus/recordings", body).status_code == 422
    assert request_files(bridge) == []


def test_recordings_range_edges(bridge, garg):
    enable(garg)
    assert garg.post("/api/admin/telus/recordings", {"days": 30, "max_items": 1000}).status_code == 200


def test_409_while_queued_by_request_file(bridge, garg):
    assert garg.post("/api/admin/telus/check").status_code == 200
    assert garg.post("/api/admin/telus/check").status_code == 409
    enable(garg)
    assert garg.post("/api/admin/telus/recordings", {"days": 7, "max_items": 10}).status_code == 409
    assert len(request_files(bridge)) == 1
    j = garg.get("/api/admin/telus").json()
    assert j["busy"] is True


def test_queued_overlay_in_response_only(bridge, garg):
    put_status(bridge, good_status())
    before = (bridge / "status.json").read_text()
    job_id = garg.post("/api/admin/telus/check").json()["job_id"]
    j = garg.get("/api/admin/telus").json()
    assert j["status"]["job"]["state"] == "queued" and j["status"]["job"]["id"] == job_id
    assert (bridge / "status.json").read_text() == before  # the worker owns the file


def test_409_while_running_and_queued_in_status(bridge, garg):
    enable(garg)
    for state in ("running", "queued"):
        put_status(bridge, good_status(state=state, mode="check", id="c" * 32))
        assert garg.post("/api/admin/telus/check").status_code == 409
        assert garg.post("/api/admin/telus/recordings", {"days": 7, "max_items": 10}).status_code == 409
    assert request_files(bridge) == []
    put_status(bridge, good_status(state="done", mode="check", id="c" * 32))
    assert garg.post("/api/admin/telus/check").status_code == 200


def test_old_request_file_does_not_block_forever(bridge, garg):
    f = bridge / "requests" / ("d" * 32 + ".json")
    f.write_text("{}")
    old = timeutil.now_utc().timestamp() - 3 * 86400
    os.utime(f, (old, old))
    assert garg.post("/api/admin/telus/check").status_code == 200


def test_junk_in_requests_dir_is_ignored(bridge, garg):
    (bridge / "requests" / "notes.txt").write_text("x")
    (bridge / "requests" / ("E" * 32 + ".json")).write_text("{}")
    assert garg.get("/api/admin/telus").json()["busy"] is False
    assert garg.post("/api/admin/telus/check").status_code == 200


def test_requests_dir_created_when_missing(bridge, garg):
    (bridge / "requests").rmdir()
    assert garg.post("/api/admin/telus/check").status_code == 200
    assert len(request_files(bridge)) == 1


def test_requested_by_is_cleaned(env, bridge, garg):
    from app.models import User
    with env.session() as s:
        u = s.scalars(select(User).where(User.username == "garg")).one()
        u.display_name = "Dr\x07. " + "G" * 100
        s.commit()
    api = env.client()
    api.login("garg")
    job_id = api.post("/api/admin/telus/check").json()["job_id"]
    body = json.loads((bridge / "requests" / (job_id + ".json")).read_text())
    assert len(body["requested_by"]) == 60 and body["requested_by"].isprintable()


# ---------------------------------------------------------------- safety
def test_no_route_takes_a_path_and_traversal_is_impossible(env, bridge, garg):
    paths = [r.path for r in admin_telus.router.routes]
    assert all("{" not in p for p in paths)
    for p in ("/api/admin/telus/..%2f..%2fetc", "/api/admin/telus/settings/../../users", "/api/admin/telus/%2e%2e/status"):
        assert garg.get(p).status_code in (404, 405, 200)
    assert garg.get("/api/admin/telus/../../../etc/passwd").status_code in (404, 200)
    # every file the app made sits directly inside the bridge folder
    garg.post("/api/admin/telus/check")
    enable(garg)
    names = {p.relative_to(bridge).as_posix() for p in bridge.rglob("*")}
    assert all(re.match(r"^(config\.json|requests|requests/[0-9a-f]{32}\.json)$", n) for n in names), names


def test_never_touches_srv_or_real_bridge(env, bridge, garg, monkeypatch):
    seen: list[str] = []
    real = {name: getattr(os, name) for name in ("open", "lstat", "scandir", "mkdir", "replace", "stat")}

    def spy(name):
        def inner(path, *a, **kw):
            seen.append(os.fspath(path))
            return real[name](path, *a, **kw)
        return inner

    for name in real:
        monkeypatch.setattr(os, name, spy(name))
    garg.get("/api/admin/telus")
    enable(garg)
    garg.post("/api/admin/telus/check")
    monkeypatch.undo()
    app_paths = [p for p in seen if "telus" in p or p.startswith("/srv") or "walnut-desk-telus" in p]
    assert not any(p.startswith("/srv") for p in seen)
    assert not any(p.startswith("/var/lib/walnut-desk-telus") for p in seen)
    assert app_paths  # the spy did observe the temp bridge


def test_default_bridge_setting(monkeypatch):
    monkeypatch.delenv("WALNUT_TELUS_DIR", raising=False)
    assert config.load_settings().telus_dir == "/var/lib/walnut-desk-telus"
    monkeypatch.setenv("WALNUT_TELUS_DIR", "/tmp/somewhere")
    assert config.load_settings().telus_dir == "/tmp/somewhere"


def test_no_em_dashes_in_module():
    src = open(admin_telus.__file__, encoding="utf-8").read()
    assert "\u2014" not in src and "\u2013" not in src
