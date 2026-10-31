"""The TELUS client that may SEND. A different object from the read-only client, with its own allowlist.

It can POST to exactly two paths (endpoints.SEND_POST_PATHS: send a text, start a RingOut call) and the token path, and GET a few
status paths. Anything else raises before a request is built. It is used only by the send worker, with its own credentials file.
"""
from __future__ import annotations

import httpx

from . import endpoints
from .client import MAX_RETRIES, MAX_SLEEP, ReadOnlyClient, ReadOnlyViolation, TelusHTTPError


class SendClient(ReadOnlyClient):
    def _check_get_path(self, path: str) -> None:
        if (not isinstance(path, str) or not path.startswith("/") or "?" in path or "#" in path or ".." in path or "//" in path
                or not any(p.fullmatch(path) for p in endpoints.SEND_GET_ALLOWLIST)):
            raise self._err(ReadOnlyViolation, f"GET path is not allowed for the send client: {str(path)[:80]}")

    def _send(self, method: str, path: str, **kw) -> httpx.Response:
        if method == "GET":
            self._check_get_path(path)
        elif method == "POST" and (path == endpoints.TOKEN_PATH or path in endpoints.SEND_POST_PATHS):
            pass
        else:
            raise self._err(ReadOnlyViolation, f"{str(method)[:10]} {str(path)[:60]} is not allowed for the send client")
        req = self._http.build_request(method, self._base + path, **kw)
        return self._http.send(req)

    def post_json(self, path: str, body: dict) -> dict:
        """POST a JSON body to one of the two send paths. Returns the parsed JSON. Raises TelusHTTPError with the status."""
        if path not in endpoints.SEND_POST_PATHS:
            raise self._err(ReadOnlyViolation, f"POST path is not allowed: {str(path)[:60]}")
        relogged = False
        attempts = 0
        while True:
            self._ensure_token()
            try:
                resp = self._send("POST", path, json=body, headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"})
            except httpx.TimeoutException:
                raise self._err(TelusHTTPError, "POST timed out") from None  # never retry a send: it could be sent twice
            except httpx.HTTPError as e:
                raise self._err(TelusHTTPError, f"POST failed: {type(e).__name__}") from None
            code = resp.status_code
            if code == 401 and not relogged:
                relogged, self._token = True, None
                continue
            if code == 429:
                attempts += 1
                if attempts > 1:
                    raise self._err(TelusHTTPError, "POST rate limited", status=429)
                self._sleep(min(self._retry_wait(resp), MAX_SLEEP))
                continue
            if 200 <= code < 300:
                try:
                    data = resp.json()
                except ValueError:
                    data = {}
                return data if isinstance(data, dict) else {}
            raise self._err(TelusHTTPError, f"POST HTTP {code}", status=code)
