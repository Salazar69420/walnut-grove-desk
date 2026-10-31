"""Play mode of the TELUS worker: one recording is fetched (GET only) and left in the bridge's play folder for an owner to hear."""
from __future__ import annotations

import json
import os
import stat
import time

import pytest

from test_telus_recordings import (  # noqa: F401  (fixtures and helpers shared with the recordings tests)
    FakeTelus, _lots_of_disk, _restore_write_status, body_for, db_rows, env, make_worker, read_status, rec_files, run, write_cfg, write_req,
)
import httpx

RID = "3198355066024"


def play_req(e, rid=RID, job="c" * 32, **over):
    return write_req(e, job_id=job, mode="play", days=None, max_items=None, recording_id=rid, call_date="2026-08-01", **over)


def result(e, rid=RID):
    with open(os.path.join(e.bridge, "play", f"{rid}.json")) as f:
        return json.load(f)


def test_play_fetches_one_recording_and_leaves_it_for_the_app(env):
    rc = FakeTelus(1)
    write_cfg(env)
    play_req(env)
    make_worker(env, rc).run()
    r = result(env)
    assert r["state"] == "ready" and r["code"] == "ready" and r["ext"] == "mp3" and r["bytes"] == len(body_for(RID))
    audio = os.path.join(env.bridge, "play", f"{RID}.mp3")
    assert open(audio, "rb").read() == body_for(RID)
    assert stat.S_IMODE(os.stat(audio).st_mode) == 0o660
    assert len(rc.content_gets()) == 1 and all(m == "GET" or m == "POST" for m, *_ in rc.log)
    assert rec_files(env) == []  # nothing is kept in the private store: only the short-lived playback copy exists
    assert db_rows(env, "select * from telus_recordings") == []
    assert not os.path.exists(os.path.join(env.bridge, "requests", "c" * 32 + ".json"))
    assert all(RID not in w for w in env.writes)  # recording ids never reach status.json


def test_play_is_refused_when_recordings_are_off_and_telus_is_not_touched(env):
    rc = FakeTelus(1)
    write_cfg(env, recordings_enabled=False)
    play_req(env)
    make_worker(env, rc).run()
    r = result(env)
    assert r["state"] == "failed" and r["code"] == "recordings_disabled"
    assert rc.log == [] and not os.path.exists(os.path.join(env.bridge, "play", f"{RID}.mp3"))


def test_a_second_play_downloads_again_because_nothing_is_kept(env):
    rc = FakeTelus(1)
    write_cfg(env)
    play_req(env)
    make_worker(env, rc).run()
    os.unlink(os.path.join(env.bridge, "play", f"{RID}.mp3"))
    play_req(env, job="d" * 32)
    make_worker(env, rc).run()
    assert result(env)["state"] == "ready" and len(rc.content_gets()) == 2 and rec_files(env) == []


def test_a_recording_already_downloaded_in_bulk_is_reused_and_kept(env):
    rc = FakeTelus(3)
    write_cfg(env)
    write_req(env, job_id="a" * 32, mode="recordings")
    make_worker(env, rc).run()
    first = rec_files(env)[0]
    rid = "4455667788"
    os.rename(first, os.path.join(os.path.dirname(first), rid + ".mp3"))  # pretend this one was downloaded under a numeric id
    kept = rec_files(env)
    before = len(rc.content_gets())
    play_req(env, rid=rid, job="b" * 32)
    make_worker(env, rc).run()
    assert result(env, rid)["state"] == "ready" and len(rc.content_gets()) == before  # no new download
    assert rec_files(env) == kept  # the admin's bulk download is not deleted


def test_a_recording_that_is_gone_reports_not_found(env):
    rc = FakeTelus(1)
    rc.content[RID] = [httpx.Response(404)]
    write_cfg(env)
    play_req(env)
    make_worker(env, rc).run()
    r = result(env)
    assert r["state"] == "failed" and r["code"] == "not_found"
    assert not os.path.exists(os.path.join(env.bridge, "play", f"{RID}.mp3")) and rec_files(env) == []


def test_old_playback_files_are_deleted_and_fresh_ones_kept(env):
    rc = FakeTelus(1)
    write_cfg(env)
    play = os.path.join(env.bridge, "play")
    os.makedirs(play)
    old, fresh = os.path.join(play, "111111.mp3"), os.path.join(play, "222222.mp3")
    for p in (old, fresh):
        open(p, "wb").write(b"x")
    past = time.time() - 3600
    os.utime(old, (past, past))
    play_req(env)
    make_worker(env, rc).run()
    assert not os.path.exists(old) and os.path.exists(fresh)


@pytest.mark.parametrize("over", [{"recording_id": "12ab"}, {"recording_id": None}, {"call_date": "yesterday"}, {"days": 3}])
def test_bad_play_requests_are_rejected_and_deleted(env, over):
    rc = FakeTelus(1)
    write_cfg(env)
    kw = dict(job_id="e" * 32, mode="play", days=None, max_items=None, recording_id=RID, call_date="2026-08-01")
    kw.update(over)
    path = write_req(env, **kw)
    make_worker(env, rc).run()
    assert not os.path.exists(path) and rc.log == []


def test_recording_id_is_not_allowed_on_other_modes(env):
    rc = FakeTelus(1)
    write_cfg(env)
    path = write_req(env, job_id="f" * 32, mode="check", days=None, max_items=None, recording_id=RID)
    make_worker(env, rc).run()
    assert not os.path.exists(path) and rc.log == []


def test_a_waiting_listener_goes_before_a_big_download_job(env):
    rc = FakeTelus(3)
    write_cfg(env)
    write_req(env, job_id="a" * 32, mode="recordings", mtime=time.time() - 100)
    play_req(env)
    make_worker(env, rc).run()
    gets = [p for m, h, p, a in rc.log if m == "GET" and "/recording/" in p]
    assert gets and gets[0].endswith(f"/{RID}/content")
