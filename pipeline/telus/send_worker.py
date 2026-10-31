"""The send worker: texts and RingOut calls requested by the web app, through the SEPARATE send credentials.

    python -m pipeline telus-send-worker [--bridge /var/lib/walnut-desk-telus]

Bridge layout (group walnut-telus):
    send_config.json          app writes: {"v":1,"mode":"practice"|"test_numbers_only"|"live","test_numbers":["+1..."],...}
    send/requests/<id>.json   app writes one per send; this worker reads, acts, deletes
    send/results/<id>.json    this worker writes: state sent|calling|failed|blocked and a fixed code. Never a number or text.
    send/inbound/<id>.json    this worker writes one per incoming text (with --inbound); the app reads it into the
                              conversation and deletes it. In test mode only texts FROM a test number are handed over.

Defence in depth: the app checks consent and the mode, and THIS worker checks them again, because it is the one that can really send.
* mode "practice" sends nothing. "test_numbers_only" sends only to numbers in the list. "live" sends to any valid Canadian number.
* At most MAX_PER_HOUR sends an hour, counted in the private store (timestamps only).
* Nothing from a request (number, text) or from TELUS is written to a log or a result file.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
import sys
import time
from datetime import datetime, timezone
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import config as tcfg
from . import endpoints
from .client import ReadOnlyViolation, TelusError, TelusHTTPError
from .send_client import SendClient

log = logging.getLogger("walnut.telus.send")

E164 = r"^\+1[2-9][0-9]{2}[2-9][0-9]{6}$"
REQUEST_MAX_BYTES = 4096
MAX_PER_HOUR = 30
MAX_JOBS_PER_RUN = 20
TEXT_MAX = 1000
CODES = ("sent", "calling", "practice_mode", "not_allowed_number", "rate_limited_local", "not_configured", "login_failed", "no_permission",
         "no_sms_number", "rejected", "rate_limited", "error")
SAFE_STATUS = {"Queued", "Sent", "Delivered", "DeliveryFailed", "SendingFailed", "Received"}
_NAME = re.compile(r"^[0-9a-f]{32}\.json$")


class SendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    v: int
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    kind: Literal["sms", "ringout"]
    to: str = Field(pattern=E164)
    from_number: Optional[str] = Field(default=None, pattern=E164)  # RingOut: the staff member's own phone, rung first
    text: Optional[str] = Field(default=None, min_length=1, max_length=TEXT_MAX)
    requested_by: str = Field(min_length=1, max_length=60)
    requested_at: str = Field(min_length=10, max_length=40)

    @field_validator("v")
    @classmethod
    def _v(cls, v: int) -> int:
        if v != 1:
            raise ValueError("unsupported version")
        return v

    @field_validator("text")
    @classmethod
    def _text(cls, t: Optional[str]) -> Optional[str]:
        if t is not None and any(ord(c) < 32 and c not in "\n\t" for c in t):
            raise ValueError("control characters")
        return t

    def check(self) -> None:
        if self.kind == "sms" and (self.text is None or self.from_number is not None):
            raise ValueError("sms needs text only")
        if self.kind == "ringout" and (self.from_number is None or self.text is not None):
            raise ValueError("ringout needs from_number only")


class Config(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    v: int = 1
    mode: Literal["practice", "test_numbers_only", "live"] = "practice"
    test_numbers: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("test_numbers")
    @classmethod
    def _nums(cls, v: list[str]) -> list[str]:
        for n in v:
            if not re.fullmatch(E164, n):
                raise ValueError("bad number")
        return v


def _read(path: str, limit: int) -> bytes | None:
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            return None
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path, flags)
        try:
            return os.read(fd, limit + 1)
        finally:
            os.close(fd)
    except OSError:
        return None


def load_config(bridge: str) -> Config:
    raw = _read(os.path.join(bridge, "send_config.json"), 16 * 1024)
    try:
        return Config.model_validate(json.loads(raw.decode("utf-8"))) if raw else Config()
    except (ValueError, ValidationError):
        return Config()  # anything unreadable means practice mode: nothing is sent


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SendWorker:
    def __init__(self, root: str, bridge: str, *, client_factory: Optional[Callable] = None, now: Callable[[], float] = time.time):
        self.root, self.bridge, self.now = root, bridge, now
        self.client_factory = client_factory or (lambda creds: SendClient(creds, token_dir=os.path.join(root, "telus")))

    # ------------------------------------------------------------ files
    def _dir(self, name: str) -> str:
        d = os.path.join(self.bridge, "send", name)
        os.makedirs(d, mode=0o770, exist_ok=True)
        os.chmod(d, 0o770)
        os.chown(d, -1, os.stat(self.bridge).st_gid)
        return d

    def _result(self, job: str, state: str, code: str, status: str | None = None) -> None:
        d = self._dir("results")
        body = {"v": 1, "id": job, "state": state, "code": code if code in CODES else "error",
                "telus_status": status if status in SAFE_STATUS else None, "at": _iso()}
        tmp = os.path.join(d, f".{job}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o660)
        with os.fdopen(fd, "w") as fh:
            json.dump(body, fh, separators=(",", ":"))
        os.chmod(tmp, 0o660)
        os.chown(tmp, -1, os.stat(self.bridge).st_gid)
        os.replace(tmp, os.path.join(d, f"{job}.json"))

    def _rate_ok(self) -> bool:
        path = os.path.join(self.root, "telus", "send_log.json")
        raw = _read(path, 64 * 1024)
        try:
            stamps = [float(x) for x in json.loads(raw.decode()) ] if raw else []
        except (ValueError, TypeError):
            stamps = []
        t = self.now()
        stamps = [s for s in stamps if t - s < 3600]
        if len(stamps) >= MAX_PER_HOUR:
            return False
        stamps.append(t)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = path + f".{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(stamps[-1000:], fh)
        os.replace(tmp, path)
        return True

    # ------------------------------------------------------------ the work
    def run(self) -> int:
        if not os.path.isdir(self.bridge) or os.path.islink(self.bridge):
            print("error: bridge folder not found", file=sys.stderr)
            return 2
        rdir = os.path.join(self.bridge, "send", "requests")
        try:
            names = sorted(n for n in os.listdir(rdir) if _NAME.fullmatch(n))
        except OSError:
            names = []
        done = bad = 0
        cfg = load_config(self.bridge)
        for name in names[:MAX_JOBS_PER_RUN]:
            path = os.path.join(rdir, name)
            raw = _read(path, REQUEST_MAX_BYTES)
            req = None
            try:
                if raw:
                    req = SendRequest.model_validate(json.loads(raw.decode("utf-8")))
                    req.check()
                    if req.id != name[:-5]:
                        req = None
            except (ValueError, ValidationError):
                req = None
            if req is None:
                bad += 1
                self._unlink(path)
                continue
            try:
                state, code, status = self._process(req, cfg)
            except Exception as e:  # nothing from TELUS or the system may reach a log or a result
                log.error("telus send failed (%s)", type(e).__name__)
                state, code, status = "failed", "error", None
            self._result(req.id, state, code, status)
            self._unlink(path)
            done += 1
        print(f"telus-send-worker done={done} rejected={bad}")
        return 0

    @staticmethod
    def _unlink(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass

    def _process(self, req: SendRequest, cfg: Config) -> tuple[str, str, str | None]:
        if cfg.mode == "practice":
            return "blocked", "practice_mode", None
        if cfg.mode == "test_numbers_only" and req.to not in cfg.test_numbers:
            return "blocked", "not_allowed_number", None
        if req.kind == "ringout" and req.from_number and cfg.mode == "test_numbers_only" and req.from_number not in cfg.test_numbers and req.to not in cfg.test_numbers:
            return "blocked", "not_allowed_number", None
        if not self._rate_ok():
            return "blocked", "rate_limited_local", None
        try:
            creds = tcfg.load_send_credentials(self.root)
        except tcfg.TelusConfigError:
            return "failed", "not_configured", None
        try:
            with self.client_factory(creds) as client:
                try:
                    client.login()
                except TelusHTTPError as e:
                    return "failed", "login_failed" if e.status in (400, 401, 403) else "error", None
                except TelusError:
                    return "failed", "login_failed", None
                need = "SMS" if req.kind == "sms" else "RingOut"
                if need not in client.scopes:
                    client._drop_token()  # a saved login can predate a permission change in the TELUS console: log in fresh once
                    try:
                        client.login()
                    except TelusError:
                        return "failed", "login_failed", None
                if need not in client.scopes:
                    return "failed", "no_permission", None
                if req.kind == "sms":
                    return self._sms(client, req)
                return self._ringout(client, req)
        except ReadOnlyViolation:
            return "failed", "error", None

    def _sms(self, client, req: SendRequest) -> tuple[str, str, str | None]:
        sender = None
        try:
            body = client.get(endpoints.PHONE_NUMBERS_PATH, {"perPage": 100})
            recs = [r for r in (body.get("records") or []) if "SmsSender" in (r.get("features") or []) and re.fullmatch(E164, str(r.get("phoneNumber") or ""))]
            recs.sort(key=lambda r: 0 if r.get("usageType") == "DirectNumber" else 1)
            sender = recs[0]["phoneNumber"] if recs else None
        except TelusError:
            return "failed", "error", None
        if sender is None:
            return "failed", "no_sms_number", None
        try:
            out = client.post_json(endpoints.SMS_PATH, {"from": {"phoneNumber": sender}, "to": [{"phoneNumber": req.to}], "text": req.text})
        except TelusHTTPError as e:
            return "failed", {400: "rejected", 422: "rejected", 401: "no_permission", 403: "no_permission", 429: "rate_limited"}.get(e.status or 0, "error"), None
        except TelusError:
            return "failed", "error", None
        st = out.get("messageStatus")
        return ("failed", "rejected", st) if st in ("SendingFailed", "DeliveryFailed") else ("sent", "sent", st)

    def _ringout(self, client, req: SendRequest) -> tuple[str, str, str | None]:
        try:
            client.post_json(endpoints.RINGOUT_PATH, {"from": {"phoneNumber": req.from_number}, "to": {"phoneNumber": req.to}, "playPrompt": False})
        except TelusHTTPError as e:
            return "failed", {400: "rejected", 422: "rejected", 401: "no_permission", 403: "no_permission", 429: "rate_limited"}.get(e.status or 0, "error"), None
        except TelusError:
            return "failed", "error", None
        return "calling", "calling", None


def _iso_ms(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def poll_inbound(w: "SendWorker", first_minutes: int = 10) -> int:
    """Hand new incoming texts to the app. Returns how many were handed over. Nothing in practice mode."""
    cfg = load_config(w.bridge)
    if cfg.mode == "practice":
        return 0
    state_path = os.path.join(w.root, "telus", "inbound_state.json")
    raw = _read(state_path, 64 * 1024)
    try:
        state = json.loads(raw.decode()) if raw else {}
    except ValueError:
        state = {}
    since = float(state.get("since") or (w.now() - first_minutes * 60))
    seen = [str(x) for x in (state.get("seen") or [])][-500:]
    try:
        creds = tcfg.load_send_credentials(w.root)
    except tcfg.TelusConfigError:
        return 0
    out = 0
    newest = since
    with w.client_factory(creds) as client:
        client.login()
        if "ReadMessages" not in client.scopes:
            log.error("telus inbound: login cannot read messages")
            return 0
        d = w._dir("inbound")
        for _page, body in client.iter_pages(endpoints.MESSAGE_STORE_PATH, {"messageType": "SMS", "direction": "Inbound",
                                                                            "dateFrom": _iso_ms(since - 120), "perPage": 100}, max_pages=5):
            for r in body.get("records") or []:
                mid = str(r.get("id") or "")
                frm = str((r.get("from") or {}).get("phoneNumber") or "")
                text = r.get("subject")
                when = str(r.get("creationTime") or "")
                if not re.fullmatch(r"[0-9]{1,24}", mid) or mid in seen or r.get("direction") != "Inbound":
                    continue
                seen.append(mid)
                try:
                    t = datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp()
                    newest = max(newest, t)
                except ValueError:
                    t = w.now()
                if not re.fullmatch(E164, frm) or not isinstance(text, str) or not text.strip():
                    continue
                if cfg.mode == "test_numbers_only" and frm not in cfg.test_numbers:
                    continue  # pilot: only the test numbers reach the app
                body_out = {"v": 1, "id": mid, "from": frm, "text": text[:1600], "at": _iso_ms(t)}
                tmp = os.path.join(d, f".{mid}.{os.getpid()}.tmp")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o660)
                with os.fdopen(fd, "w") as fh:
                    json.dump(body_out, fh, separators=(",", ":"))
                os.chmod(tmp, 0o660)
                os.chown(tmp, -1, os.stat(w.bridge).st_gid)
                os.replace(tmp, os.path.join(d, f"{mid}.json"))
                out += 1
    os.makedirs(os.path.dirname(state_path), mode=0o700, exist_ok=True)
    tmp = state_path + f".{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"since": newest, "seen": seen[-500:]}, fh)
    os.replace(tmp, state_path)
    return out


def run(root: str, bridge: str, inbound: bool = False, **kw) -> int:
    w = SendWorker(root, bridge, **kw)
    if inbound:
        try:
            n = poll_inbound(w)
            print(f"telus-inbound handed_over={n}")
        except Exception as e:  # never let text from TELUS reach the logs
            log.error("telus inbound failed (%s)", type(e).__name__)
    return w.run()
