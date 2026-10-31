"""ReadOnlyClient: the only HTTP code in the TELUS connector.

Guarantees (enforced in `_send`, the single place a request is built):
  * GET only, and only to paths that fully match endpoints.GET_ALLOWLIST;
  * the single exception is the JWT login POST to exactly endpoints.TOKEN_PATH, made by `_login`;
  * any other method or path raises ReadOnlyViolation before a request object exists;
  * no redirects are followed, with one exception: download_recording follows at most ONE redirect, and only
    to an https host on endpoints.REDIRECT_HOSTS (checked before anything is sent to it);
  * headers, tokens, query strings and response bodies are never logged, and every exception message is
    passed through redaction of the known secret values.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import httpx

from . import endpoints
from .config import Credentials

log = logging.getLogger("pipeline.telus")

MAX_RETRIES = 3          # 429 and timeout retries per request
MAX_SLEEP = 90.0         # seconds, cap on one wait
MAX_PAGES = 2000
EXPIRY_MARGIN = 60.0     # re-login this many seconds before the token expires
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
_REC_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CHUNK = 64 * 1024
for _name in ("httpx", "httpcore"):  # their INFO lines carry full URLs
    logging.getLogger(_name).setLevel(logging.WARNING)


class TelusError(Exception):
    """Base error. Messages are redacted and contain no response bodies."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ReadOnlyViolation(TelusError):
    """A request that is not an allowlisted GET (or the login POST) was attempted."""


class TelusAuthError(TelusError):
    pass


class TelusDownloadError(TelusError):
    """A recording download that was refused or aborted. `reason` is one of a fixed set of words."""

    def __init__(self, message: str, reason: str, status: int | None = None):
        super().__init__(message, status)
        self.reason = reason  # not_audio, too_large, empty, bad_status, too_many_redirects


@dataclass(frozen=True)
class DownloadResult:
    bytes: int
    sha256: str
    content_type: str
    rate_remaining: int | None
    rate_window: float | None


class TelusHTTPError(TelusError):
    pass


def redact(text: str, secrets: list[str]) -> str:
    for s in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(s, "[REDACTED]")
    return text


class ReadOnlyClient:
    def __init__(self, creds: Credentials, *, transport: httpx.BaseTransport | None = None,
                 timeout: float = 30.0, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, token_dir: str | None = None):
        self._creds = creds
        # Reuse one login for its whole hour instead of logging in on every run (TELUS limits logins). The token is kept in the
        # private store next to the credentials (mode 600), one file per app and JWT; it is never printed or sent anywhere else.
        self._token_file = (os.path.join(token_dir, "token-" + hashlib.sha256(f"{creds.client_id}:{creds.jwt}".encode()).hexdigest()[:16] + ".json")
                            if token_dir else None)
        self._secrets: list[str] = list(creds.secret_values())
        self._base = creds.server_url.rstrip("/")
        self._http = httpx.Client(transport=transport, timeout=httpx.Timeout(timeout),
                                  follow_redirects=False)
        self._sleep = sleep
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0
        self.scopes: list[str] = []
        self.expires_in: int = 0
        self.logins = 0

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ReadOnlyClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"ReadOnlyClient(host={self._creds.host!r})"

    # ------------------------------------------------------------------ errors
    def _err(self, cls, message: str, **kw) -> TelusError:
        return cls(redact(message, self._secrets), **kw)

    # ------------------------------------------------------------------ the guard
    def request(self, method: str, path: str, params: dict | None = None) -> httpx.Response:
        """Guarded entry for arbitrary verbs: anything but GET raises and sends nothing."""
        if str(method).upper() != "GET":
            raise self._err(ReadOnlyViolation, f"HTTP method {str(method)[:10]} is not allowed (read-only client)")
        return self._get_response(path, params)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        resp = self._get_response(path, params)
        try:
            data = resp.json()
        except ValueError:
            raise self._err(TelusHTTPError, f"GET {path}: response was not JSON", status=resp.status_code) from None
        if not isinstance(data, dict):
            raise self._err(TelusHTTPError, f"GET {path}: unexpected JSON shape", status=resp.status_code)
        return data

    def get_content(self, path: str, params: dict[str, Any] | None = None) -> bytes:
        """Raw bytes of an allowlisted GET (recording audio). Not used by the call-log pull."""
        return self._get_response(path, params).content

    # ------------------------------------------------------------------ recording download (streaming)
    def download_recording(self, recording_id: str, dest_tmp_path: str, max_bytes: int) -> DownloadResult:
        """Stream one recording's audio to `dest_tmp_path` (created new, mode 600, never overwritten).

        Uses only the allowlisted content path. If TELUS answers with a redirect, at most one is followed and
        only to an https host on endpoints.REDIRECT_HOSTS for this environment; any other target raises
        ReadOnlyViolation before a request is built. Requires HTTP 200, a non-empty body, Content-Type
        audio/* or application/octet-stream and at most `max_bytes`. On any failure the temp file is removed.
        Messages never contain URLs, ids, tokens or bodies."""
        if not isinstance(recording_id, str) or not _REC_ID.fullmatch(recording_id):
            raise self._err(ReadOnlyViolation, "recording id is not acceptable")
        path = endpoints.recording_content_path(recording_id)
        self._check_get_path(path)
        first_url = self._base + path
        url, hop, attempts, relogged = first_url, 0, 0, False
        while True:
            self._ensure_token()
            try:
                resp = self._send_stream(url)
            except httpx.TimeoutException:
                attempts += 1
                if attempts > MAX_RETRIES:
                    raise self._err(TelusHTTPError, "recording download: timed out") from None
                self._sleep(min(2.0 ** attempts, MAX_SLEEP))
                continue
            except httpx.HTTPError as e:
                raise self._err(TelusHTTPError, f"recording download: {type(e).__name__}") from None
            code = resp.status_code
            if code in REDIRECT_STATUSES:
                location = resp.headers.get("location")
                resp.close()
                if hop >= 1:
                    raise self._err(TelusDownloadError, "recording download: more than one redirect",
                                    reason="too_many_redirects", status=code)
                url, hop = self._redirect_target(location), 1
                continue
            if code == 401 and not relogged:
                resp.close()
                self._drop_token()
                relogged, url, hop = True, first_url, 0
                continue
            if code == 429:
                wait = self._retry_wait(resp)
                resp.close()
                attempts += 1
                if attempts > MAX_RETRIES:
                    raise self._err(TelusHTTPError, "recording download: rate limited (HTTP 429), gave up",
                                    status=429)
                log.warning("telus rate limited, waiting %.0fs (retry %d)", wait, attempts)
                self._sleep(wait)
                continue
            if code != 200:
                resp.close()
                if 200 <= code < 300:
                    raise self._err(TelusDownloadError, f"recording download: unexpected HTTP {code}",
                                    reason="bad_status", status=code)
                raise self._err(TelusHTTPError, f"recording download: HTTP {code}", status=code)
            break
        try:
            return self._save_stream(resp, dest_tmp_path, max_bytes)
        finally:
            resp.close()

    def _send_stream(self, url: str) -> httpx.Response:
        """GET `url` (already vetted: the allowlisted content path, or an allowlisted redirect host) with the
        bearer token, returning an open streaming response."""
        req = self._http.build_request(
            "GET", url, headers={"Authorization": f"Bearer {self._token}",
                                 "Accept": "audio/*, application/octet-stream;q=0.9"})
        return self._http.send(req, stream=True)

    def _redirect_target(self, location) -> str:
        """The vetted absolute URL for a redirect, or ReadOnlyViolation. Nothing has been sent to it yet."""
        bad = self._err(ReadOnlyViolation, "recording redirect target is not allowlisted")
        if (not isinstance(location, str) or not location or len(location) > 4096
                or re.search(r"[\x00-\x20\x7f]", location)):
            raise bad
        try:
            u = httpx.URL(self._base + "/").join(location)
        except (httpx.InvalidURL, ValueError):
            raise bad from None
        hosts = endpoints.REDIRECT_HOSTS.get(self._creds.env, ())
        if (u.scheme != "https" or u.host not in hosts or u.port not in (None, 443) or u.userinfo):
            raise bad
        return str(u)

    def _save_stream(self, resp: httpx.Response, dest: str, max_bytes: int) -> DownloadResult:
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if not (ctype.startswith("audio/") or ctype == "application/octet-stream"):
            raise self._err(TelusDownloadError, "recording download: content type is not audio",
                            reason="not_audio", status=200)
        declared = resp.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > max_bytes:
            raise self._err(TelusDownloadError, "recording download: larger than the size limit",
                            reason="too_large", status=200)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(dest, flags, 0o600)
        digest, total = hashlib.sha256(), 0
        try:
            with os.fdopen(fd, "wb") as f:
                for chunk in resp.iter_bytes(CHUNK):
                    total += len(chunk)
                    if total > max_bytes:
                        raise self._err(TelusDownloadError, "recording download: larger than the size limit",
                                        reason="too_large", status=200)
                    digest.update(chunk)
                    f.write(chunk)
                if total == 0:
                    raise self._err(TelusDownloadError, "recording download: empty body",
                                    reason="empty", status=200)
                f.flush()
                os.fsync(f.fileno())
        except httpx.HTTPError as e:
            self._unlink_quiet(dest)
            raise self._err(TelusHTTPError, f"recording download: {type(e).__name__}") from None
        except BaseException:
            self._unlink_quiet(dest)
            raise
        return DownloadResult(bytes=total, sha256=digest.hexdigest(), content_type=ctype,
                              rate_remaining=self._int_header(resp, "X-Rate-Limit-Remaining"),
                              rate_window=self._float_header(resp, "X-Rate-Limit-Window"))

    @staticmethod
    def _unlink_quiet(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass

    @staticmethod
    def _int_header(resp: httpx.Response, name: str) -> int | None:
        try:
            return max(0, int(resp.headers.get(name, "")))
        except ValueError:
            return None

    @staticmethod
    def _float_header(resp: httpx.Response, name: str) -> float | None:
        try:
            return max(0.0, min(float(resp.headers.get(name, "")), MAX_SLEEP))
        except ValueError:
            return None

    def _check_get_path(self, path: str) -> None:
        if (not isinstance(path, str) or not path.startswith("/") or "?" in path or "#" in path
                or ".." in path or "//" in path
                or not any(p.fullmatch(path) for p in endpoints.GET_ALLOWLIST)):
            raise self._err(ReadOnlyViolation, f"GET path is not on the allowlist: {str(path)[:80]}")

    def _send(self, method: str, path: str, **kw) -> httpx.Response:
        """The only place a request is built. Enforces the read-only rule."""
        if method == "GET":
            self._check_get_path(path)
        elif method == "POST" and path == endpoints.TOKEN_PATH:
            pass  # login, called only from _login
        else:
            raise self._err(ReadOnlyViolation, f"{str(method)[:10]} {str(path)[:60]} is not allowed")
        req = self._http.build_request(method, self._base + path, **kw)
        return self._http.send(req)

    # ------------------------------------------------------------------ login
    def _basic_header(self) -> str:
        raw = f"{self._creds.client_id}:{self._creds.client_secret}".encode("utf-8")
        enc = base64.b64encode(raw).decode("ascii")
        self._secrets.append(enc)  # redaction only
        return "Basic " + enc

    def _login(self) -> None:
        """JWT bearer login. The one non-GET request, to the exact token path only."""
        try:
            resp = self._send(
                "POST", endpoints.TOKEN_PATH,
                data={"grant_type": endpoints.JWT_GRANT_TYPE, "assertion": self._creds.jwt},
                headers={"Accept": "application/json", "Authorization": self._basic_header()})
        except httpx.TimeoutException:
            raise self._err(TelusHTTPError, "login timed out") from None
        except httpx.HTTPError as e:
            raise self._err(TelusHTTPError, f"login failed: {type(e).__name__}") from None
        if resp.status_code != 200:
            raise self._err(TelusAuthError, f"login rejected (HTTP {resp.status_code})", status=resp.status_code)
        try:
            body = resp.json()
            token = body["access_token"]
            expires_in = int(body.get("expires_in", 0))
            scope = str(body.get("scope", ""))
        except (ValueError, KeyError, TypeError):
            raise self._err(TelusAuthError, "login response was not understood") from None
        if not isinstance(token, str) or not token:
            raise self._err(TelusAuthError, "login response had no access token")
        self._token = token
        self._secrets.append(token)
        rt = body.get("refresh_token")
        if isinstance(rt, str) and rt:
            self._secrets.append(rt)  # redaction only; never stored or used
        self.expires_in = expires_in
        self._expires_at = self._clock() + expires_in
        self.scopes = scope.split()
        self.logins += 1
        self._save_token()
        log.info("telus login ok, scopes=%d", len(self.scopes))

    def _load_token(self) -> bool:
        if not self._token_file:
            return False
        try:
            import json
            with open(self._token_file, encoding="utf-8") as fh:
                d = json.load(fh)
            left = float(d["expires_at"]) - time.time()
            if left <= EXPIRY_MARGIN or not isinstance(d.get("token"), str) or not d["token"]:
                return False
        except (OSError, ValueError, KeyError, TypeError):
            return False
        self._token = d["token"]
        self._secrets.append(self._token)
        self._expires_at = self._clock() + left
        self.expires_in = int(left)
        self.scopes = [x for x in d.get("scopes", []) if isinstance(x, str)]
        return True

    def _save_token(self) -> None:
        if not self._token_file or not self._token:
            return
        try:
            import json
            os.makedirs(os.path.dirname(self._token_file), mode=0o700, exist_ok=True)
            tmp = f"{self._token_file}.{os.getpid()}.tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump({"token": self._token, "expires_at": time.time() + self.expires_in, "scopes": self.scopes}, fh)
            os.replace(tmp, self._token_file)
        except OSError:
            pass

    def _drop_token(self) -> None:
        self._token = None
        if self._token_file:
            try:
                os.unlink(self._token_file)
            except OSError:
                pass

    def login(self) -> None:
        """Log in, or reuse this app's still-valid login from the token file."""
        if self._token is not None and self._clock() < self._expires_at - EXPIRY_MARGIN:
            return
        if self._load_token():
            return
        self._login()

    def _ensure_token(self) -> None:
        if self._token is None or self._clock() >= self._expires_at - EXPIRY_MARGIN:
            self.login()

    # ------------------------------------------------------------------ GET with retries
    def _get_response(self, path: str, params: dict | None) -> httpx.Response:
        self._check_get_path(path)  # fail before logging in
        relogged = False
        attempts = 0
        while True:
            self._ensure_token()
            try:
                resp = self._send("GET", path, params=params,
                                  headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"})
            except httpx.TimeoutException:
                attempts += 1
                if attempts > MAX_RETRIES:
                    raise self._err(TelusHTTPError, f"GET {path}: timed out") from None
                log.warning("telus timeout, retry %d", attempts)
                self._sleep(min(2.0 ** attempts, MAX_SLEEP))
                continue
            except httpx.HTTPError as e:
                raise self._err(TelusHTTPError, f"GET {path}: {type(e).__name__}") from None
            code = resp.status_code
            if code == 401 and not relogged:
                relogged = True
                self._drop_token()
                continue
            if code == 429:
                attempts += 1
                if attempts > MAX_RETRIES:
                    raise self._err(TelusHTTPError, f"GET {path}: rate limited (HTTP 429), gave up", status=429)
                wait = self._retry_wait(resp)
                log.warning("telus rate limited, waiting %.0fs (retry %d)", wait, attempts)
                self._sleep(wait)
                continue
            if 200 <= code < 300:
                return resp
            raise self._err(TelusHTTPError, f"GET {path}: HTTP {code}", status=code)

    @staticmethod
    def _retry_wait(resp: httpx.Response) -> float:
        for h in ("Retry-After", "X-Rate-Limit-Window"):
            v = resp.headers.get(h)
            if v:
                try:
                    return max(1.0, min(float(v), MAX_SLEEP))
                except ValueError:
                    continue
        return 10.0

    # ------------------------------------------------------------------ pagination
    def iter_pages(self, path: str, params: dict[str, Any] | None = None, *, start_page: int = 1,
                   max_pages: int = MAX_PAGES) -> Iterator[tuple[int, dict]]:
        """Yield (page_number, body). Pages are requested by number; the server-provided nextPage URI is
        never followed, so a response cannot steer the client to another host or path."""
        page = start_page
        for _ in range(max_pages):
            q = dict(params or {})
            q["page"] = page
            body = self.get(path, q)
            yield page, body
            nav = body.get("navigation") or {}
            paging = body.get("paging") or {}
            more = bool(nav.get("nextPage"))
            if not more and isinstance(paging.get("totalPages"), int):
                more = page < paging["totalPages"]
            if not more:
                return
            page += 1
        raise self._err(TelusHTTPError, f"GET {path}: more than {max_pages} pages, stopping")
