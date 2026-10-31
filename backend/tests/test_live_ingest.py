"""Live call numbers: reading analytics.json from the bridge folder, validation, replacement, wiring."""
from __future__ import annotations

import copy
import dataclasses
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app import config
from app.models import CallAnalytic, Event
from app.services import live_ingest, settings_store, timeutil
from app.services.broadcaster import broadcaster

BACKEND = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- builders (shared with test_live_brief.py)
def _r(metric, dim1=None, dim2=None, value=0, n=None):
    return {"metric": metric, "dim1": dim1, "dim2": dim2, "value": value, "n": int(value) if n is None else n}


def make_rows(*, skip: tuple[str, ...] = ()) -> list[dict]:
    rows: list[dict] = []
    for period, k in (("7d", 1), ("prev7d", 0)):
        rows += [_r("live_calls", period, "all", 120 - 20 * (1 - k)), _r("live_calls", period, "inbound", 80 - 10 * (1 - k)),
                 _r("live_calls", period, "outbound", 40 - 10 * (1 - k))]
        rows += [_r("live_inbound_result", period, "answered", 50 - 2 * (1 - k)),
                 _r("live_inbound_result", period, "missed", 18 - 6 * (1 - k)),
                 _r("live_inbound_result", period, "voicemail", 8 - 2 * (1 - k)),
                 _r("live_inbound_result", period, "hangup", 4 - 2 * (1 - k))]
        rows += [_r("live_after_hours", period, "inbound", 14 - 4 * (1 - k)), _r("live_after_hours", period, "voicemail", 6 - 2 * (1 - k))]
    rows += [_r("live_missed_followup", "7d", "rang_again_1h", 8), _r("live_missed_followup", "7d", "called_back_4h", 6),
             _r("live_missed_followup", "7d", "called_back_24h", 4), _r("live_missed_followup", "7d", "no_contact_24h", 9),
             _r("live_missed_followup", "7d", "too_recent", 3)]
    avg = [9.0, 20.5, 14.0, 8.0, 6.0, 5.0, 6.0, 7.0, 6.0, 4.0]
    for h, v in zip(range(8, 18), avg):
        rows.append(_r("live_inbound_by_hour", str(h), None, v, n=int(v * 20)))
        rows.append(_r("live_missed_by_hour", str(h), None, round(v / 4, 2), n=int(v * 5)))
    rows.append(_r("live_busiest_hour", "9", None, 20.5, n=410))
    today = timeutil.today_local()
    for i in range(14):
        rows.append(_r("live_daily_inbound", (today - timedelta(days=14 - i)).isoformat(), None, 10 + i))
    rows.append(_r("live_past_followup_share", "past_recordings", None, 0.62, n=40))
    return [r for r in rows if r["metric"] not in skip]


def make_payload(rows=None, **over) -> dict:
    today = timeutil.today_local()
    p = {"v": 1, "generated_at": timeutil.now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"), "timezone": "America/Vancouver",
         "window": {"from": (today - timedelta(days=35)).isoformat(), "to": (today - timedelta(days=1)).isoformat()},
         "rows": make_rows() if rows is None else rows}
    p.update(over)
    return p


def write_live(d: Path, payload) -> Path:
    p = d / "analytics.json"
    if p.is_symlink() or p.exists():
        p.unlink()
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return p


def drain(q, wait=0.3):
    import time

    time.sleep(wait)
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.fixture()
def bridge(env, tmp_path):
    d = tmp_path / "telus"
    d.mkdir()
    (d / "requests").mkdir()
    config.set_settings(dataclasses.replace(env.settings, telus_dir=str(d)))
    yield d
    config.set_settings(env.settings)


def live_rows(env):
    with env.session() as s:
        return s.execute(select(CallAnalytic.metric, CallAnalytic.dim1, CallAnalytic.dim2, CallAnalytic.value, CallAnalytic.n)
                         .where(CallAnalytic.source == "telus_live").order_by(CallAnalytic.id)).all()


def ingest(env, d):
    with env.session() as s:
        return live_ingest.ingest(s, str(d))


# ---------------------------------------------------------------- happy path
def test_valid_file_is_loaded_with_event_and_sse(env, bridge):
    payload = make_payload()
    write_live(bridge, payload)
    q = broadcaster.subscribe()
    res = ingest(env, bridge)
    assert res.status == "ok" and res.rows == len(payload["rows"])
    rows = live_rows(env)
    assert len(rows) == len(payload["rows"])
    by_dim = {(m, d1, d2): v for m, d1, d2, v, n in rows}
    assert by_dim[("live_calls", "7d", "all")] == 120.0
    assert by_dim[("live_inbound_by_hour", "9", "")] == 20.5  # null dims are stored as empty text
    with env.session() as s:
        ev = s.scalars(select(Event).where(Event.action == "live_calls_updated")).all()
        assert len(ev) == 1 and ev[0].tool == "Call logger"
        assert ev[0].text == f"Live call numbers updated: {len(payload['rows'])} rows"
        assert ev[0].data_json == json.dumps({"rows": len(payload["rows"])}, separators=(",", ":"), sort_keys=True)
        meta = live_ingest.meta(s)
        assert meta["generated_at"] == payload["generated_at"] and meta["rows"] == len(payload["rows"])
    msgs = drain(q)
    assert any(m["entity"] == "analytics" for m in msgs) and any(m["entity"] == "events" for m in msgs)


def test_same_file_again_changes_nothing(env, bridge):
    write_live(bridge, make_payload())
    assert ingest(env, bridge).status == "ok"
    first = live_rows(env)
    assert ingest(env, bridge).status == "unchanged"
    assert live_rows(env) == first
    with env.session() as s:
        assert len(s.scalars(select(Event).where(Event.action == "live_calls_updated")).all()) == 1


def test_new_file_replaces_every_live_row_and_leaves_other_sources(env, bridge):
    with env.session() as s:
        s.add(CallAnalytic(source="derived_from_real_calls", metric="weekday_avg_calls_by_hour", dim1="9", value=3.0, n=5))
        s.add(CallAnalytic(source="demo", metric="x", dim1="1", value=1.0, n=1))
        s.commit()
    write_live(bridge, make_payload())
    assert ingest(env, bridge).status == "ok"
    smaller = make_payload(rows=[_r("live_calls", "7d", "all", 7)])
    write_live(bridge, smaller)
    res = ingest(env, bridge)
    assert res.status == "ok" and res.rows == 1
    assert live_rows(env) == [("live_calls", "7d", "all", 7.0, 7)]
    with env.session() as s:
        others = s.scalars(select(CallAnalytic).where(CallAnalytic.source != "telus_live")).all()
        assert {o.source for o in others} == {"derived_from_real_calls", "demo"}


def test_replacement_is_atomic_when_the_write_fails_midway(env, bridge, monkeypatch):
    write_live(bridge, make_payload())
    assert ingest(env, bridge).status == "ok"
    before = live_rows(env)
    write_live(bridge, make_payload(rows=[_r("live_calls", "7d", "all", 5)]))

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(settings_store, "put_raw", boom)
    assert ingest(env, bridge).status == "error"
    monkeypatch.undo()
    assert live_rows(env) == before  # old rows are all still there
    with env.session() as s:
        assert len(s.scalars(select(Event).where(Event.action == "live_calls_updated")).all()) == 1


def test_dropping_a_metric_removes_it_next_time(env, bridge):
    write_live(bridge, make_payload())
    ingest(env, bridge)
    write_live(bridge, make_payload(rows=make_rows(skip=("live_daily_inbound",))))
    ingest(env, bridge)
    assert not any(r[0] == "live_daily_inbound" for r in live_rows(env))


# ---------------------------------------------------------------- hostile files
def _mut(fn):
    p = make_payload()
    fn(p)
    return p


BAD = {
    "not_json": "this is not json {{{",
    "empty": "",
    "binary": None,
    "list": "[1,2,3]",
    "string": '"hello"',
    "nan": '{"v":1,"generated_at":"2026-01-01T00:00:00Z","timezone":"America/Vancouver","window":{"from":"2026-01-01","to":"2026-01-02"},"rows":[{"metric":"live_calls","dim1":"7d","dim2":"all","value":NaN,"n":1}]}',
    "infinity": '{"v":1,"rows":[{"value":Infinity}]}',
    "wrong_version": _mut(lambda p: p.update(v=2)),
    "string_version": _mut(lambda p: p.update(v="1")),
    "wrong_timezone": _mut(lambda p: p.update(timezone="UTC")),
    "extra_top_key": _mut(lambda p: p.update(caller="anything")),
    "extra_row_key": _mut(lambda p: p["rows"][0].update(phone="hidden")),
    "missing_rows": _mut(lambda p: p.pop("rows")),
    "rows_not_list": _mut(lambda p: p.update(rows={"a": 1})),
    "row_not_object": _mut(lambda p: p["rows"].append("x")),
    "string_value": _mut(lambda p: p["rows"][0].update(value="12")),
    "bool_value": _mut(lambda p: p["rows"][0].update(value=True)),
    "null_value": _mut(lambda p: p["rows"][0].update(value=None)),
    "negative_value": _mut(lambda p: p["rows"][0].update(value=-1)),
    "huge_value": _mut(lambda p: p["rows"][0].update(value=10**12)),
    "float_n": _mut(lambda p: p["rows"][0].update(n=1.5)),
    "string_n": _mut(lambda p: p["rows"][0].update(n="3")),
    "negative_n": _mut(lambda p: p["rows"][0].update(n=-3)),
    "unknown_metric": _mut(lambda p: p["rows"][0].update(metric="live_secret_thing")),
    "metric_space": _mut(lambda p: p["rows"][0].update(metric="live calls")),
    "metric_sql": _mut(lambda p: p["rows"][0].update(metric="x'; DROP TABLE events;--")),
    "metric_html": _mut(lambda p: p["rows"][0].update(dim1="<script>alert(1)</script>")),
    "dim_unicode": _mut(lambda p: p["rows"][0].update(dim1="7d‮")),
    "dim_newline": _mut(lambda p: p["rows"][0].update(dim2="all\nname")),
    "dim_path": _mut(lambda p: p["rows"][0].update(dim2="../../etc/passwd")),
    "dim_too_long": _mut(lambda p: p["rows"][0].update(dim2="a" * 41)),
    "dim_phone_like": _mut(lambda p: p["rows"][0].update(dim2="+16045550123")),
    "dim_empty": _mut(lambda p: p["rows"][0].update(dim2="")),
    "dim1_missing": _mut(lambda p: p["rows"][0].update(dim1=None)),
    "dim_not_in_list": _mut(lambda p: p["rows"][0].update(dim1="30d")),
    "dim2_not_in_list": _mut(lambda p: p["rows"][0].update(dim2="sideways")),
    "hour_out_of_range": _mut(lambda p: p["rows"].append(_r("live_inbound_by_hour", "7", None, 1))),
    "hour_with_dim2": _mut(lambda p: p["rows"].append(_r("live_busiest_hour", "9", "x", 1))),
    "bad_date": _mut(lambda p: p["rows"].append(_r("live_daily_inbound", "2026-13-40", None, 1))),
    "duplicate_row": _mut(lambda p: p["rows"].append(copy.deepcopy(p["rows"][0]))),
    "too_many_rows": _mut(lambda p: p.update(rows=[_r("live_daily_inbound", f"2026-01-{(i % 28) + 1:02d}", None, 1) for i in range(2001)])),
    "bad_generated_at": _mut(lambda p: p.update(generated_at="yesterday-ish")),
    "naive_generated_at": _mut(lambda p: p.update(generated_at="2026-10-01T10:00:00")),
    "future_generated_at": _mut(lambda p: p.update(generated_at=(timeutil.now_utc() + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))),
    "long_generated_at": _mut(lambda p: p.update(generated_at="2026-10-01T10:00:00Z" + " " * 40)),
    "bad_window": _mut(lambda p: p["window"].update(to="soon")),
    "window_extra": _mut(lambda p: p["window"].update(note="x")),
    "window_missing": _mut(lambda p: p.pop("window")),
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_hostile_files_are_rejected_and_old_numbers_survive(env, bridge, name):
    write_live(bridge, make_payload())
    assert ingest(env, bridge).status == "ok"
    before = live_rows(env)
    bad = BAD[name]
    p = bridge / "analytics.json"
    p.unlink()
    if bad is None:
        p.write_bytes(os.urandom(3000))
    elif isinstance(bad, str):
        p.write_text(bad)
    else:
        p.write_text(json.dumps(bad))
    res = ingest(env, bridge)  # never raises
    assert res.status == "invalid", name
    assert live_rows(env) == before
    with env.session() as s:
        assert len(s.scalars(select(Event).where(Event.action == "live_calls_updated")).all()) == 1


def test_deep_nesting_does_not_raise(env, bridge):
    write_live(bridge, "[" * 50000 + "]" * 50000)
    assert ingest(env, bridge).status in ("invalid", "error", "too_big")
    assert live_rows(env) == []


def test_injected_strings_never_reach_the_database_or_feed(env, bridge):
    marker = "Mrs_Marker_Name"
    bad = make_payload()
    bad["rows"][0]["dim2"] = marker + " 604-555-0147"
    write_live(bridge, bad)
    assert ingest(env, bridge).status == "invalid"
    with env.session() as s:
        blob = json.dumps([(r.metric, r.dim1, r.dim2) for r in s.scalars(select(CallAnalytic))] +
                          [(e.text, e.data_json) for e in s.scalars(select(Event))])
        assert marker not in blob and "604" not in blob


def test_oversized_file_is_refused_without_reading(env, bridge):
    big = make_payload()
    write_live(bridge, json.dumps(big) + " " * (live_ingest.MAX_BYTES + 10))
    assert (bridge / "analytics.json").stat().st_size > live_ingest.MAX_BYTES
    assert ingest(env, bridge).status == "too_big"
    assert live_rows(env) == []


def test_file_at_the_limit_is_still_checked(env, bridge):
    body = json.dumps(make_payload())
    write_live(bridge, body + " " * (live_ingest.MAX_BYTES - len(body)))
    assert (bridge / "analytics.json").stat().st_size == live_ingest.MAX_BYTES
    assert ingest(env, bridge).status == "ok"


def test_symlinked_file_is_not_followed(env, bridge, tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(make_payload()))
    (bridge / "analytics.json").symlink_to(target)
    assert ingest(env, bridge).status == "unsafe"
    assert live_rows(env) == []


def test_directory_fifo_and_missing_file(env, bridge):
    assert ingest(env, bridge).status == "no_file"
    (bridge / "analytics.json").mkdir()
    assert ingest(env, bridge).status == "unsafe"
    (bridge / "analytics.json").rmdir()
    os.mkfifo(bridge / "analytics.json")
    assert ingest(env, bridge).status == "unsafe"  # a pipe would block a reader forever
    assert live_rows(env) == []


def test_symlinked_bridge_folder_is_refused(env, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    write_live(real, make_payload())
    link = tmp_path / "link"
    link.symlink_to(real)
    assert ingest(env, link).status == "unsafe"
    assert ingest(env, tmp_path / "nope" / "deeper").status in ("unsafe", "no_file")
    assert live_rows(env) == []


def test_ingest_never_raises_even_with_a_broken_session(bridge):
    write_live(bridge, make_payload())

    class Broken:
        def __getattr__(self, name):
            raise RuntimeError("no database")

    assert live_ingest.ingest(Broken(), str(bridge)).status == "error"  # type: ignore[arg-type]


# ---------------------------------------------------------------- wiring: python -m app.rules
def test_rules_command_loads_live_numbers(env, bridge):
    write_live(bridge, make_payload())
    e = {**os.environ, "WALNUT_ENV": "test", "WALNUT_DB_PATH": str(env.path), "WALNUT_TELUS_DIR": str(bridge)}
    r = subprocess.run([sys.executable, "-m", "app.rules"], cwd=BACKEND, env=e, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    assert "live call numbers: ok" in r.stderr
    assert len(live_rows(env)) == len(make_rows())
    # a second run finds nothing new
    r = subprocess.run([sys.executable, "-m", "app.rules"], cwd=BACKEND, env=e, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "live call numbers: unchanged" in r.stderr
    # a bad file does not stop the rules and keeps the numbers
    write_live(bridge, "garbage")
    r = subprocess.run([sys.executable, "-m", "app.rules"], cwd=BACKEND, env=e, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "live call numbers: invalid" in r.stderr
    assert len(live_rows(env)) == len(make_rows())


# ---------------------------------------------------------------- admin routes
def good_status(sync=None, **job):
    now = timeutil.now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    j = {"id": None, "mode": None, "state": "idle", "requested_by": None, "requested_at": None, "started_at": None,
         "finished_at": None, "code": "ok", "found": 0, "downloaded": 0, "already_had": 0, "skipped": 0, "failed": 0,
         "remaining": 0, "deleted_old": 0}
    j.update(job)
    st = {"v": 1, "updated_at": now,
          "connection": {"state": "ok", "code": "ok", "checked_at": now, "environment": "production",
                         "write_capable_scopes": False, "calls_last_7_days": 12},
          "job": j, "storage": {"recordings_count": 3, "bytes": 3000, "free_bytes": 10**10, "max_bytes": 10**10},
          "rejected_requests": 0}
    if sync is not None:
        st["sync"] = sync
    return st


def put_status(d, obj):
    (d / "status.json").write_text(json.dumps(obj))


def request_files(d):
    return sorted((d / "requests").iterdir())


def test_sync_route_writes_a_sync_request(env, bridge, garg):
    r = garg.post("/api/admin/telus/sync")
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    files = request_files(bridge)
    assert [f.name for f in files] == [job_id + ".json"]
    assert oct(files[0].stat().st_mode & 0o777) == "0o660" and files[0].stat().st_size <= 4096
    body = json.loads(files[0].read_text())
    assert set(body) == {"v", "id", "mode", "days", "max_items", "requested_by", "requested_at"}
    assert body["mode"] == "sync" and body["id"] == job_id and 1 <= body["days"] <= 30 and 1 <= body["max_items"] <= 1000
    with env.session() as s:
        evs = s.scalars(select(Event).where(Event.action == "telus_sync_requested")).all()
    assert len(evs) == 1 and evs[0].tool == "Admin"


def test_sync_route_is_409_when_busy_and_503_without_a_bridge(env, bridge, garg, tmp_path):
    assert garg.post("/api/admin/telus/sync").status_code == 200
    assert garg.post("/api/admin/telus/sync").status_code == 409  # a request file is already waiting
    assert garg.post("/api/admin/telus/check").status_code == 409
    for f in request_files(bridge):
        f.unlink()
    put_status(bridge, good_status(state="running", mode="sync", id="c" * 32))
    assert garg.post("/api/admin/telus/sync").status_code == 409
    j = garg.get("/api/admin/telus").json()
    assert j["status"]["job"]["mode"] == "sync" and j["busy"] is True
    config.set_settings(dataclasses.replace(env.settings, telus_dir=str(tmp_path / "missing")))
    assert garg.post("/api/admin/telus/sync").status_code == 503


@pytest.mark.parametrize("username", ["priya", "anita"])
def test_sync_route_forbidden_for_desk_and_nurse_and_anonymous(env, bridge, username):
    api = env.client()
    assert api.login(username).status_code == 200
    assert api.post("/api/admin/telus/sync").status_code == 403
    assert env.client().c.post("/api/admin/telus/sync").status_code == 401
    assert request_files(bridge) == []


def test_sync_route_needs_csrf(env, bridge, garg):
    assert garg.c.post("/api/admin/telus/sync").status_code == 403
    assert request_files(bridge) == []


def test_status_accepts_a_sync_object_and_tolerates_its_absence(env, bridge, garg):
    put_status(bridge, good_status())
    assert garg.get("/api/admin/telus").json()["status"]["sync"] is None
    put_status(bridge, good_status(sync={"state": "ok", "code": "ok", "finished_at": "2026-10-30T14:00:00Z", "rows": 150}))
    s = garg.get("/api/admin/telus").json()["status"]["sync"]
    assert s == {"state": "ok", "code": "ok", "finished_at": "2026-10-30T14:00:00Z", "rows": 150}
    for bad in ({"state": "weird", "code": "ok"}, {"state": "ok", "code": "free text"}, "nope", {"state": "ok", "code": "ok", "rows": "9"}, 7):
        put_status(bridge, good_status(sync=bad))
        j = garg.get("/api/admin/telus").json()
        assert j["status"] is not None and j["status"]["sync"] is None  # the rest of the status is kept


def test_get_loads_the_file_after_the_worker_reports_a_finished_sync(env, bridge, garg):
    write_live(bridge, make_payload())
    j = garg.get("/api/admin/telus").json()
    assert j["live"] == {"as_of": None, "loaded_at": None, "rows": None} and live_rows(env) == []
    put_status(bridge, good_status(sync={"state": "ok", "code": "ok", "finished_at": "2026-10-30T14:00:00Z", "rows": 150}))
    j = garg.get("/api/admin/telus").json()
    assert len(live_rows(env)) == len(make_rows())
    assert j["live"]["rows"] == len(make_rows()) and j["live"]["as_of"].endswith("Z") and j["live"]["loaded_at"]
    n = len(live_rows(env))
    garg.get("/api/admin/telus")
    assert len(live_rows(env)) == n
    with env.session() as s:
        assert len(s.scalars(select(Event).where(Event.action == "live_calls_updated")).all()) == 1


def test_get_does_not_load_while_busy_or_after_a_failed_sync(env, bridge, garg):
    write_live(bridge, make_payload())
    put_status(bridge, good_status(sync={"state": "failed", "code": "error", "finished_at": None, "rows": None}))
    garg.get("/api/admin/telus")
    assert live_rows(env) == []
    put_status(bridge, good_status(state="running", mode="sync", id="d" * 32,
                                   sync={"state": "ok", "code": "ok", "finished_at": None, "rows": 1}))
    garg.get("/api/admin/telus")
    assert live_rows(env) == []


def test_bad_file_after_sync_does_not_break_the_telus_page(env, bridge, garg):
    write_live(bridge, "garbage")
    put_status(bridge, good_status(sync={"state": "ok", "code": "ok", "finished_at": None, "rows": 1}))
    r = garg.get("/api/admin/telus")
    assert r.status_code == 200 and r.json()["live"]["as_of"] is None
