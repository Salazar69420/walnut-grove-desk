"""One TELUS login is reused for its hour (TELUS limits logins); a rejected token is dropped and replaced."""
import os
import stat

import httpx

from pipeline.telus import endpoints
from pipeline.telus.client import ReadOnlyClient
from pipeline.telus.config import Credentials

CREDS = Credentials(env="sandbox", server_url=endpoints.SERVER_URLS["sandbox"], client_id="cid-test", client_secret="sec-test", jwt="jwt-test")


def router(state):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == endpoints.TOKEN_PATH:
            state["logins"] += 1
            return httpx.Response(200, json={"access_token": f"tok{state['logins']}", "expires_in": 3600, "scope": "ReadCallLog ReadAccounts"})
        if request.headers.get("authorization") in state.get("revoked", set()):
            return httpx.Response(401, json={})
        return httpx.Response(200, json={"records": []})
    return handler


def test_the_login_is_reused_across_runs_and_kept_private(tmp_path):
    state = {"logins": 0}
    for _ in range(3):
        with ReadOnlyClient(CREDS, transport=httpx.MockTransport(router(state)), token_dir=str(tmp_path)) as c:
            c.login()
            c.get(endpoints.CALL_LOG_PATH, {"perPage": 1})
            assert c.scopes == ["ReadCallLog", "ReadAccounts"]
    assert state["logins"] == 1
    (f,) = list(tmp_path.iterdir())
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600


def test_a_rejected_saved_login_is_replaced(tmp_path):
    state = {"logins": 0}
    with ReadOnlyClient(CREDS, transport=httpx.MockTransport(router(state)), token_dir=str(tmp_path)) as c:
        c.login()
    state["revoked"] = {"Bearer tok1"}
    with ReadOnlyClient(CREDS, transport=httpx.MockTransport(router(state)), token_dir=str(tmp_path)) as c:
        c.get(endpoints.CALL_LOG_PATH, {"perPage": 1})
    assert state["logins"] == 2


def test_without_a_token_dir_nothing_is_saved(tmp_path):
    state = {"logins": 0}
    for _ in range(2):
        with ReadOnlyClient(CREDS, transport=httpx.MockTransport(router(state))) as c:
            c.login()
    assert state["logins"] == 2
