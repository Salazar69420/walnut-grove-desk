from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import SessionRow, User
from app.services import timeutil
from tests.conftest import PASSWORD


def test_login_me_logout(env):
    api = env.client()
    r = api.login("priya")
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["username"] == "priya"
    assert body["user"]["role"] == "desk"
    assert "callbacks.work" in body["perms"] and "brief.view" not in body["perms"]
    assert body["settings"]["banner_on"] is True and body["settings"]["clinic_name"]
    me = api.get("/api/auth/me").json()
    assert me["csrf_token"] == body["csrf_token"]
    assert api.post("/api/auth/logout").status_code == 200
    assert api.get("/api/auth/me").status_code == 401


def test_cookie_flags_dev(env):
    api = env.client()
    r = api.login("priya")
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("wgd=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie


def test_wrong_password_and_unknown_user_look_the_same(env):
    api = env.client()
    a = api.login("priya", "wrong password here")
    b = api.login("nobody-here", "wrong password here")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() == {"detail": "Wrong username or password"}


def test_inactive_user_is_generic_401(env):
    r = env.client().login("gone")
    assert r.status_code == 401
    assert r.json() == {"detail": "Wrong username or password"}


def test_lockout_after_five_failures(env):
    api = env.client()
    codes = [api.login("priya", "bad password number %d" % i).status_code for i in range(5)]
    assert codes == [401, 401, 401, 401, 423]
    # the right password is refused while locked
    r = api.login("priya")
    assert r.status_code == 423
    assert r.json() == {"detail": "Too many tries. Wait 15 minutes."}
    # lock ends after 15 minutes
    timeutil.set_clock(timeutil.now_utc() + timedelta(minutes=16))
    assert api.login("priya").status_code == 200


def test_unknown_username_locks_the_same_way(env):
    api = env.client()
    codes = [api.login("ghost", "bad password number %d" % i).status_code for i in range(5)]
    assert codes == [401, 401, 401, 401, 423]


def test_success_resets_failure_count(env):
    api = env.client()
    for i in range(3):
        assert api.login("priya", "nope nope nope %d" % i).status_code == 401
    assert api.login("priya").status_code == 200
    with env.session() as s:
        assert s.scalar(select(User).where(User.username == "priya")).failed_count == 0


def test_per_ip_limit(env):
    api = env.client()
    # 20 attempts from one address, spread over names so no single account locks
    for i in range(20):
        assert api.login("who%d" % i, "bad password").status_code == 401
    r = api.login("priya")
    assert r.status_code == 429
    # window passes
    timeutil.set_clock(timeutil.now_utc() + timedelta(minutes=11))
    assert api.login("priya").status_code == 200


def test_forced_password_change_gate(env):
    api = env.client()
    assert api.login("newbie").status_code == 200
    assert api.get("/api/auth/me").json()["user"]["must_change_password"] is True
    for path in ("/api/callbacks", "/api/results", "/api/shifts", "/api/tools", "/api/events", "/api/export/callbacks.csv"):
        r = api.get(path)
        assert r.status_code == 403 and r.json() == {"detail": "password_change_required"}, path
    assert api.post("/api/callbacks/1/claim").json() == {"detail": "password_change_required"}
    # a weak or wrong change is refused
    assert api.post("/api/auth/change-password", {"current_password": PASSWORD, "new_password": "short"}).status_code == 400
    assert api.post("/api/auth/change-password", {"current_password": "nope", "new_password": "a brand new long password"}).status_code == 400
    r = api.post("/api/auth/change-password", {"current_password": PASSWORD, "new_password": "a brand new long password"})
    assert r.status_code == 200 and r.json()["user"]["must_change_password"] is False
    assert api.get("/api/callbacks").status_code == 200
    # old password no longer works, new one does
    other = env.client()
    assert other.login("newbie", PASSWORD).status_code == 401
    assert other.login("newbie", "a brand new long password").status_code == 200


def test_password_policy(env):
    api = env.client()
    api.login("newbie")

    def change(new):
        return api.post("/api/auth/change-password", {"current_password": PASSWORD, "new_password": new})

    assert change("newbie").status_code == 400            # too short and equals username
    assert change("password1234").status_code == 400      # common
    assert change("aaaaaaaaaaaaaaaa").status_code == 400  # no variety
    assert change(PASSWORD).status_code == 400            # same as current
    assert change("x" * 300).status_code == 422           # over the max length


def test_change_password_logs_out_other_sessions(env):
    a = env.client()
    a.login("priya")
    b = env.client()
    b.login("priya")
    assert a.post("/api/auth/change-password", {"current_password": PASSWORD, "new_password": "another long password!"}).status_code == 200
    assert b.get("/api/auth/me").status_code == 401
    assert a.get("/api/auth/me").status_code == 200


def test_csrf_missing_and_wrong(env, priya):
    c = priya.c
    r = c.post("/api/callbacks/1/claim")
    assert r.status_code == 403 and r.json() == {"detail": "csrf"}
    r = c.post("/api/callbacks/1/claim", headers={"X-CSRF-Token": "not-the-token"})
    assert r.status_code == 403 and r.json() == {"detail": "csrf"}
    r = c.post("/api/callbacks/1/claim", headers={"X-CSRF-Token": priya.csrf})
    assert r.status_code == 200
    # logout needs it too; GETs never do
    assert c.post("/api/auth/logout").status_code == 403
    assert c.get("/api/callbacks").status_code == 200


def test_csrf_exempt_login_only(env):
    c = env.client().c
    assert c.post("/api/auth/login", json={"username": "priya", "password": PASSWORD}).status_code == 200


def test_session_idle_and_absolute_expiry(env):
    api = env.client()
    api.login("priya")
    now = timeutil.now_utc()
    # idle: 13 hours without a request
    timeutil.set_clock(now + timedelta(hours=13))
    assert api.get("/api/auth/me").status_code == 401
    with env.session() as s:
        assert s.scalar(select(SessionRow).where(SessionRow.user_id == 2)) is None
    timeutil.set_clock(None)

    # absolute: active use cannot go past 7 days
    api = env.client()
    api.login("priya")
    start = timeutil.now_utc()
    for hours in range(10, 7 * 24, 10):
        timeutil.set_clock(start + timedelta(hours=hours))
        assert api.get("/api/auth/me").status_code == 200
    timeutil.set_clock(start + timedelta(days=7, minutes=1))
    assert api.get("/api/auth/me").status_code == 401


def test_deactivated_user_loses_session(env, garg):
    api = env.client()
    api.login("marcus")
    uid = [u for u in garg.get("/api/admin/users").json()["items"] if u["username"] == "marcus"][0]["id"]
    assert garg.patch(f"/api/admin/users/{uid}", {"active": False}).status_code == 200
    assert api.get("/api/auth/me").status_code == 401


def test_security_headers_everywhere(env, priya):
    for r in (env.client().get("/healthz"), priya.get("/api/callbacks"), env.client().get("/api/auth/me")):
        h = r.headers
        assert h["content-security-policy"].startswith("default-src 'self'")
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "same-origin"
        assert h["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


def test_login_events_in_audit(env, garg):
    env.client().login("priya", "wrong password here")
    items = garg.get("/api/admin/audit?tool=Sign-in").json()["items"]
    actions = {i["action"] for i in items}
    assert {"login", "login_failed"} <= actions


def test_validation_limits(env):
    c = env.client()
    assert c.c.post("/api/auth/login", json={"username": "x" * 200, "password": "p"}).status_code == 422
    assert c.c.post("/api/auth/login", json={"username": "a", "password": "p", "extra": 1}).status_code == 422
    assert c.c.post("/api/auth/login", content=b"not json", headers={"content-type": "application/json"}).status_code == 422
