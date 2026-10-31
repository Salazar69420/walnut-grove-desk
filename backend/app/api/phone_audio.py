"""Play a call recording from the Phone page (owners only).

The web app never holds TELUS credentials and never reads the private store. Pressing play leaves a "play" request in the bridge
folder; the download helper (pipeline worker, which holds the TELUS login) fetches that one recording, read-only, and leaves a short-lived
copy in <bridge>/play/. This module only writes the request and streams the copy. Each play is written to the access log.
"""
from __future__ import annotations

import logging
import os
import stat
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api import admin_telus as bridge
from app.db import get_db
from app.models import PhoneCall, User
from app.security.auth import require
from app.services import crm
from app.services import events as ev
from app.services import timeutil

log = logging.getLogger("walnut.listen")
router = APIRouter(prefix="/api/phone/calls", tags=["phone"])

PLAY_DIR = "play"
MAX_AUDIO_BYTES = 200 * 1024 * 1024
AUDIO_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg", "m4a": "audio/mp4", "mp4": "audio/mp4", "webm": "audio/webm", "audio": "application/octet-stream"}
REQUEST_AGAIN_AFTER = 45  # seconds before another request for the same recording is allowed
_asked: dict[str, float] = {}

MESSAGES = {
    "recordings_disabled": "Recordings are switched off. An admin turns them on in Admin, TELUS.",
    "not_configured": "The TELUS login is not set up on the server yet.",
    "login_failed": "TELUS did not accept the login. An admin should check the connection in Admin, TELUS.",
    "write_scope_present": "The TELUS login can change things, so the helper refused to use it. Use a read-only login.",
    "disk_cap_reached": "The recordings storage limit is full. An admin can raise it in Admin, TELUS.",
    "disk_low": "The server is low on disk space.",
    "rate_limited": "TELUS asked us to slow down. Try again in a minute.",
    "not_found": "TELUS no longer has this recording.",
    "error": "The recording could not be fetched. Try again in a minute.",
}


def _call(db: Session, call_id: int) -> PhoneCall:
    c = db.get(PhoneCall, call_id)
    if c is None:
        raise HTTPException(status_code=404, detail="No such call")
    if not c.recording_id or not c.recording_id.isdigit():
        raise HTTPException(status_code=400, detail="This call has no recording.")
    return c


def _play_dir() -> str:
    return os.path.join(bridge._bridge(), PLAY_DIR)


def _ready(rid: str) -> tuple[str, str] | None:
    """(path, content type) when a finished copy sits in the play folder. Never follows links."""
    meta = bridge.read_json_file(_play_dir(), f"{rid}.json")
    if not meta or meta.get("state") != "ready":
        return None
    ext = str(meta.get("ext") or "")
    if ext not in AUDIO_TYPES:
        return None
    path = os.path.join(_play_dir(), f"{rid}.{ext}")
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_size == 0 or st.st_size > MAX_AUDIO_BYTES:
        return None
    return path, AUDIO_TYPES[ext]


def _failed(rid: str) -> str | None:
    meta = bridge.read_json_file(_play_dir(), f"{rid}.json")
    if meta and meta.get("state") == "failed":
        code = str(meta.get("code") or "error")
        return MESSAGES.get(code, MESSAGES["error"])
    return None


def _log_play(db: Session, user: User, c: PhoneCall) -> None:
    crm.log_access(db, user, c.contact_id, "view", detail="phone recording", dedupe_seconds=30)
    ev.record(db, ev.PHONE, "recording_played", f"{user.display_name} played a call recording", user, "phone_call", c.id, publish=False)
    db.commit()


@router.post("/{call_id}/listen")
def start_listening(call_id: int, user: User = Depends(require("phone.listen")), db: Session = Depends(get_db)) -> dict[str, Any]:
    c = _call(db, call_id)
    rid = c.recording_id
    if not bridge.read_settings().get("recordings_enabled"):
        raise HTTPException(status_code=409, detail=MESSAGES["recordings_disabled"])
    bridge._need_bridge()
    if _ready(rid):
        _log_play(db, user, c)
        return {"state": "ready"}
    now = time.monotonic()
    if now - _asked.get(rid, -1e9) < REQUEST_AGAIN_AFTER:
        return {"state": "waiting"}
    # an old failure must not be shown as the answer to this new request
    try:
        os.unlink(os.path.join(_play_dir(), f"{rid}.json"))
    except OSError:
        pass
    req_dir = os.path.join(bridge._bridge(), "requests")
    try:
        if not bridge._real_dir(req_dir):
            os.mkdir(req_dir, 0o770)
            os.chmod(req_dir, 0o770)
        job_id = uuid.uuid4().hex
        body = {"v": 1, "id": job_id, "mode": "play", "recording_id": rid, "call_date": timeutil.local(c.started_at).date().isoformat(),
                "requested_by": bridge._who(user), "requested_at": bridge._now_iso()}
        bridge._atomic_write(bridge._bridge(), os.path.join(req_dir, job_id + ".json"), body)
    except OSError as e:
        log.warning("could not write a play request: %s", e.__class__.__name__)
        raise HTTPException(status_code=503, detail="Could not hand the request to the download helper. Try again.")
    _asked[rid] = now
    if len(_asked) > 500:
        for k in sorted(_asked, key=_asked.get)[:250]:
            _asked.pop(k, None)
    return {"state": "queued"}


@router.get("/{call_id}/listen")
def listen_state(call_id: int, user: User = Depends(require("phone.listen")), db: Session = Depends(get_db)) -> dict[str, Any]:
    c = _call(db, call_id)
    if _ready(c.recording_id):
        return {"state": "ready"}
    msg = _failed(c.recording_id)
    if msg:
        _asked.pop(c.recording_id, None)
        return {"state": "failed", "message": msg}
    meta = bridge.read_json_file(_play_dir(), f"{c.recording_id}.json")
    return {"state": "working" if meta and meta.get("state") == "working" else "waiting"}


@router.get("/{call_id}/audio")
def audio(call_id: int, user: User = Depends(require("phone.listen")), db: Session = Depends(get_db)):
    c = _call(db, call_id)
    found = _ready(c.recording_id)
    if not found:
        raise HTTPException(status_code=404, detail="The recording is not ready. Press play again.")
    path, ctype = found
    return FileResponse(path, media_type=ctype, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                                                        "Content-Security-Policy": "sandbox", "Content-Disposition": "inline"})
