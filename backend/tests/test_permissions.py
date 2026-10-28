"""Walks every route in the app and checks the permission matrix."""
from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute

from app.security.permissions import ADMIN_PERMS, PERMS, has_perm, perms_for

# /api routes that are intentionally not behind require(perm)
UNTAGGED_OK = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/change-password"),
    ("GET", "/api/stream"),  # any signed-in user
}
# Retell routes use their own shared secret, not a session
RETELL_PREFIX = "/api/retell/"


def _perm_of(dependant) -> str | None:
    for dep in dependant.dependencies:
        perm = getattr(dep.call, "walnut_perm", None)
        if perm:
            return perm
        sub = _perm_of(dep)
        if sub:
            return sub
    return None


def iter_routes(routes):
    """Flatten routers. Newer FastAPI keeps included routers as lazy wrappers."""
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, "original_router"):
            yield from iter_routes(r.original_router.routes)
        elif hasattr(r, "routes"):
            yield from iter_routes(r.routes)


def api_routes(app):
    out = []
    for r in iter_routes(app.routes):
        if not isinstance(r, APIRoute) or not r.path.startswith("/api"):
            continue
        if r.path == "/api/{rest:path}":
            continue
        for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
            out.append((method, r.path, _perm_of(r.dependant)))
    return out


def fill(path: str) -> str:
    path = path.replace("{name}", "callbacks")
    return re.sub(r"\{[^}]+\}", "1", path)


def test_every_api_route_is_tagged_or_listed(env):
    for method, path, perm in api_routes(env.app):
        if perm is None:
            assert (method, path) in UNTAGGED_OK or path.startswith(RETELL_PREFIX), f"{method} {path} has no require(perm)"
        else:
            assert perm in PERMS or perm in ADMIN_PERMS


def test_there_are_routes_to_check(env):
    routes = api_routes(env.app)
    assert len([r for r in routes if r[2]]) >= 20
    assert {r[2] for r in routes if r[2]} >= {"callbacks.work", "drafts.approve", "results.advance", "admin.users", "admin.audit", "admin.settings", "admin.reset_demo"}


def test_permission_functions():
    class U:  # minimal user stand-in
        def __init__(self, role, is_admin=False):
            self.role, self.is_admin = role, is_admin

    assert has_perm(U("owner"), "brief.view") and not has_perm(U("desk"), "brief.view")
    assert has_perm(U("nurse"), "results.advance") and not has_perm(U("desk"), "results.advance")
    assert not has_perm(U("owner"), "admin.users") and has_perm(U("desk", True), "admin.users")
    assert "admin.audit" in perms_for(U("owner", True)) and "admin.audit" not in perms_for(U("owner"))


@pytest.mark.parametrize("username", ["priya", "anita"])
def test_desk_and_nurse_get_403_on_every_route_they_lack(env, username):
    api = env.client()
    assert api.login(username).status_code == 200
    role = api.get("/api/auth/me").json()["user"]["role"]
    checked = 0
    for method, path, perm in api_routes(env.app):
        if perm is None:
            continue
        lacks = (perm in ADMIN_PERMS) or role not in PERMS[perm]
        if not lacks:
            continue
        r = api.c.request(method, fill(path), json={}, headers={"X-CSRF-Token": api.csrf})
        assert r.status_code == 403, f"{username} {method} {path} ({perm}) -> {r.status_code}"
        assert r.json() == {"detail": "forbidden"}
        checked += 1
    assert checked >= 8


def test_owner_without_admin_flag_cannot_reach_admin(env, garg):
    # a plain owner account: make one by demoting krish
    users = {u["username"]: u for u in garg.get("/api/admin/users").json()["items"]}
    assert garg.patch(f"/api/admin/users/{users['krish']['id']}", {"is_admin": False}).status_code == 200
    k = env.client()
    k.login("krish")
    for path in ("/api/admin/users", "/api/admin/audit", "/api/admin/settings"):
        assert k.get(path).status_code == 403
    assert k.post("/api/admin/reset-demo").status_code == 403
    assert k.get("/api/callbacks").status_code == 200


def test_anonymous_gets_401_everywhere(env):
    api = env.client()
    for method, path, perm in api_routes(env.app):
        if (method, path) == ("POST", "/api/auth/login"):
            continue
        if (method, path) == ("POST", "/api/auth/logout"):
            continue  # logging out when not signed in is a harmless no-op
        if path.startswith(RETELL_PREFIX):
            continue  # own secret, 404 while disabled
        r = api.c.request(method, fill(path), json={})
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


def test_password_change_gate_on_every_route(env):
    api = env.client()
    assert api.login("newbie").status_code == 200
    for method, path, perm in api_routes(env.app):
        if perm is None and (method, path) != ("GET", "/api/stream"):
            continue
        r = api.c.request(method, fill(path), json={}, headers={"X-CSRF-Token": api.csrf})
        assert r.status_code == 403 and r.json() == {"detail": "password_change_required"}, f"{method} {path}"


def test_export_checks_the_lists_own_permission(env, anita, priya):
    assert anita.get("/api/export/drafts.csv").status_code == 403
    assert anita.get("/api/export/callbacks.csv").status_code == 200
    assert priya.get("/api/export/drafts.csv").status_code == 200
    assert priya.get("/api/export/audit.csv").status_code == 403
    assert priya.get("/api/export/nonsense.csv").status_code == 404


def test_role_specific_actions(env, priya, anita, garg):
    # desk cannot advance results, nurse cannot approve drafts or see them
    assert priya.post("/api/results/1/advance").status_code == 403
    assert anita.post("/api/results/1/advance").status_code == 200
    assert anita.get("/api/drafts").status_code == 403
    assert anita.post("/api/drafts/1/approve").status_code == 403
    assert garg.post("/api/results/2/advance").status_code == 200  # owner may
