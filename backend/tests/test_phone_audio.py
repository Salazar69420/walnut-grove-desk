"""Play a call recording: owner only, through the bridge folder, logged. The worker is simulated by writing its output files."""
from __future__ import annotations

import dataclasses
import json
import os

import pytest
from sqlalchemy import select

from app import config
from app.api import phone_audio
from app.models import AccessLog, PhoneCall

RID = "3198355066024"
AUDIO = b"ID3FAKE-AUDIO" * 50


@pytest.fixture()
def bridge(env, tmp_path):
    d = tmp_path / "bridge"
    (d / "requests").mkdir(parents=True)
    config.set_settings(dataclasses.replace(env.settings, telus_dir=str(d)))
    phone_audio._asked.clear()
    yield d
    config.set_settings(env.settings)


def enable(bridge, on=True):
    (bridge / "config.json").write_text(json.dumps({"v": 1, "recordings_enabled": on, "max_gb": 10, "keep_days": 30, "max_per_run_cap": 200,
                                                    "updated_by": "x", "updated_at": "2026-08-01T00:00:00Z"}))


def a_call(env, recording=RID):
    with env.session() as s:
        c = s.scalar(select(PhoneCall).where(PhoneCall.is_demo.is_(True)).limit(1))
        c.recording_id = recording
        s.commit()
        return c.id


def worker_says_ready(bridge, rid=RID, ext="mp3"):
    play = bridge / "play"
    play.mkdir(exist_ok=True)
    (play / f"{rid}.{ext}").write_bytes(AUDIO)
    (play / f"{rid}.json").write_text(json.dumps({"v": 1, "state": "ready", "code": "ready", "ext": ext, "bytes": len(AUDIO), "at": "2026-08-01T00:00:00Z"}))


def test_only_the_owner_can_listen(env, bridge, priya, anita, garg):
    cid = a_call(env)
    enable(bridge)
    for who in (priya, anita):
        assert who.post(f"/api/phone/calls/{cid}/listen").status_code == 403
        assert who.get(f"/api/phone/calls/{cid}/listen").status_code == 403
        assert who.get(f"/api/phone/calls/{cid}/audio").status_code == 403
    assert garg.get("/api/phone/calls?per_page=10").json()["can_listen"] is True
    assert priya.get("/api/phone/calls?per_page=10").json()["can_listen"] is False


def test_recordings_must_be_switched_on_first(env, bridge, garg):
    cid = a_call(env)
    r = garg.post(f"/api/phone/calls/{cid}/listen")
    assert r.status_code == 409 and "switched off" in r.json()["detail"]
    assert os.listdir(bridge / "requests") == []


def test_a_call_without_a_recording_cannot_be_played(env, bridge, garg):
    cid = a_call(env, recording=None)
    enable(bridge)
    assert garg.post(f"/api/phone/calls/{cid}/listen").status_code == 400
    assert garg.get(f"/api/phone/calls/{cid}/audio").status_code == 400


def test_pressing_play_queues_exactly_one_play_request(env, bridge, garg):
    cid = a_call(env)
    enable(bridge)
    assert garg.post(f"/api/phone/calls/{cid}/listen").json() == {"state": "queued"}
    assert garg.post(f"/api/phone/calls/{cid}/listen").json() == {"state": "waiting"}  # pressed again: no duplicate request
    files = os.listdir(bridge / "requests")
    assert len(files) == 1
    req = json.loads((bridge / "requests" / files[0]).read_text())
    assert req["mode"] == "play" and req["recording_id"] == RID and "days" not in req and "max_items" not in req
    assert len(req["call_date"]) == 10 and req["requested_by"]
    assert garg.get(f"/api/phone/calls/{cid}/listen").json() == {"state": "waiting"}
    assert garg.get(f"/api/phone/calls/{cid}/audio").status_code == 404


def test_ready_audio_is_streamed_safely_and_logged(env, bridge, garg):
    cid = a_call(env)
    enable(bridge)
    worker_says_ready(bridge)
    assert garg.get(f"/api/phone/calls/{cid}/listen").json() == {"state": "ready"}
    assert garg.post(f"/api/phone/calls/{cid}/listen").json() == {"state": "ready"}
    r = garg.get(f"/api/phone/calls/{cid}/audio")
    assert r.status_code == 200 and r.content == AUDIO and r.headers["content-type"] == "audio/mpeg"
    assert r.headers["cache-control"] == "private, no-store" and r.headers["x-content-type-options"] == "nosniff"
    with env.session() as s:
        assert s.scalar(select(AccessLog).where(AccessLog.detail == "phone recording")) is not None
    assert os.listdir(bridge / "requests") == []  # nothing new was asked of the helper


def test_a_failed_fetch_shows_a_plain_sentence(env, bridge, garg):
    cid = a_call(env)
    enable(bridge)
    (bridge / "play").mkdir()
    (bridge / "play" / f"{RID}.json").write_text(json.dumps({"v": 1, "state": "failed", "code": "not_found", "ext": "", "bytes": 0, "at": "x"}))
    r = garg.get(f"/api/phone/calls/{cid}/listen").json()
    assert r["state"] == "failed" and "no longer has" in r["message"]
    # pressing play again clears the old failure and asks again
    assert garg.post(f"/api/phone/calls/{cid}/listen").json() == {"state": "queued"}
    assert not (bridge / "play" / f"{RID}.json").exists()


@pytest.mark.parametrize("bad", [{"state": "ready", "ext": "../../etc/passwd"}, {"state": "ready", "ext": "exe"}, {"state": "ready", "ext": ""}])
def test_hostile_result_files_are_ignored(env, bridge, garg, bad):
    cid = a_call(env)
    enable(bridge)
    (bridge / "play").mkdir()
    (bridge / "play" / f"{RID}.json").write_text(json.dumps(bad))
    assert garg.get(f"/api/phone/calls/{cid}/audio").status_code == 404


def test_a_link_in_the_play_folder_is_not_followed(env, bridge, garg, tmp_path):
    cid = a_call(env)
    enable(bridge)
    secret = tmp_path / "secret.mp3"
    secret.write_bytes(b"secret data")
    (bridge / "play").mkdir()
    os.symlink(secret, bridge / "play" / f"{RID}.mp3")
    (bridge / "play" / f"{RID}.json").write_text(json.dumps({"v": 1, "state": "ready", "code": "ready", "ext": "mp3", "bytes": 11, "at": "x"}))
    assert garg.get(f"/api/phone/calls/{cid}/audio").status_code == 404


def test_the_list_says_which_calls_have_a_recording(env, bridge, garg):
    a_call(env)
    items = garg.get("/api/phone/calls?per_page=100").json()["items"]
    assert sum(1 for i in items if i["has_recording"]) == 1
