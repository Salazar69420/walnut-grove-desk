"""ReadOnlyClient: the only HTTP code in the TELUS connector.

Guarantees (enforced in `_send`, the single place a request is built):
  * GET only, and only to paths that fully match endpoints.GET_ALLOWLIST;
  * the single exception is the JWT login POST to exactly endpoints.TOKEN_PATH, made by `_login`;
  * any other method or path raises ReadOnlyViolation before a request object exists;
  * no redirects are followed;
  * headers, tokens, query strings and response bodies are never logged, and every exception message is
    passed through redaction of the known secret values.
"""
from __future__ import annotations

import base64
import logging
import time
from typing import Any, Callable, Iterator

import httpx

from . import endpoints
from .config import Credentials

log = logging.getLogger("pipeline.telus")

MAX_RETRIES = 3          # 429 and timeout retries per request
MAX_SLEEP = 90.0         # seconds, cap on one wait
MAX_PAGES = 2000
EXPIRY_MARGIN = 60.0     # re-login this many seconds before the token expires
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


class TelusHTTPError(TelusError):
    pass


def redact(text: str, secrets: list[str]) -> str:
    for s in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(s, "[REDACTED]")
    return text


class ReadOnlyClient:
    def __init__(self, creds: Credentials, *, transport: httpx.BaseTransport | None = None,
                 timeout: float = 30.0, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self._creds = creds
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
        log.info("telus login ok, scopes=%d", len(self.scopes))

    def login(self) -> None:
        self._login()

    def _ensure_token(self) -> None:
        if self._token is None or self._clock() >= self._expires_at - EXPIRY_MARGIN:
            self._login()

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
                self._token = None
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
