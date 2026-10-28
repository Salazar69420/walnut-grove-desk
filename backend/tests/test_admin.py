from __future__ import annotations

from sqlalchemy import select

from app.models import CallAnalytic, ImportedCall, Setting
from tests.conftest import PASSWORD


def uid(garg, username):
    return [u for u in garg.get("/api/admin/users").json()["items"] if u["username"] == username][0]["id"]


def test_users_list(garg):
    items = garg.get("/api/admin/users").json()["items"]
    assert {u["username"] for u in items} >= {"garg", "priya", "marcus", "anita", "jordan", "krish"}
    assert "password_hash" not in items[0] and "password" not in items[0]
    assert {"id", "username", "role", "is_admin", "active", "locked", "last_login_at"} <= set(items[0])


def test_create_user_one_time_password_and_first_login(env, garg):
    r = garg.post("/api/admin/users", {"username": "Sam", "display_name": "Sam Lee", "title": "Front desk", "role": "desk"})
    assert r.status_code == 201
    body = r.json()
    pw = body["password"]
    assert len(pw) == 20 and body["item"]["username"] == "sam" and body["item"]["initials"] == "SL"
    assert body["item"]["must_change_password"] is True
    # the password is not retrievable later
    assert "password" not in garg.get("/api/admin/users").json()["items"][-1]
    sam = env.client()
    assert sam.login("sam", pw).status_code == 200
    assert sam.get("/api/callbacks").json() == {"detail": "password_change_required"}
    # duplicates and bad input
    assert garg.post("/api/admin/users", {"username": "sam", "display_name": "x", "role": "desk"}).status_code == 409
    assert garg.post("/api/admin/users", {"username": "9bad name", "display_name": "x", "role": "desk"}).status_code == 422
    assert garg.post("/api/admin/users", {"username": "okname", "display_name": "x", "role": "boss"}).status_code == 422
    assert garg.post("/api/admin/users", {"username": "okname", "display_name": "x" * 121, "role": "desk"}).status_code == 422


def test_patch_user_role_admin_active(env, garg):
    mid = uid(garg, "marcus")
    r = garg.patch(f"/api/admin/users/{mid}", {"role": "nurse", "is_admin": True})
    assert r.status_code == 200 and r.json()["item"]["role"] == "nurse" and r.json()["item"]["is_admin"] is True
    assert garg.patch(f"/api/admin/users/{mid}", {"role": "wizard"}).status_code == 422
    assert garg.patch("/api/admin/users/9999", {"active": False}).status_code == 404
    assert garg.patch(f"/api/admin/users/{mid}", {"active": False}).json()["item"]["active"] is False
    assert env.client().login("marcus").status_code == 401  # inactive looks like a wrong password


def test_admin_cannot_lock_everyone_out(env, garg):
    gid = uid(garg, "garg")
    r = garg.patch(f"/api/admin/users/{gid}", {"active": False})
    assert r.status_code == 409 and "own access" in r.json()["detail"]
    assert garg.patch(f"/api/admin/users/{gid}", {"is_admin": False}).status_code == 409
    # demote the other admin, then the last one cannot be removed by anyone
    kid = uid(garg, "krish")
    assert garg.patch(f"/api/admin/users/{kid}", {"is_admin": False}).status_code == 200
    mid = uid(garg, "marcus")
    garg.patch(f"/api/admin/users/{mid}", {"is_admin": True})
    m = env.client()
    m.login("marcus")
    assert m.patch(f"/api/admin/users/{gid}", {"is_admin": False}).status_code == 200
    assert m.patch(f"/api/admin/users/{mid}", {"active": False}).status_code == 409  # self


def test_reset_password(env, garg):
    pid = uid(garg, "priya")
    old = env.client()
    old.login("priya")
    r = garg.post(f"/api/admin/users/{pid}/reset-password")
    assert r.status_code == 200
    pw = r.json()["password"]
    assert len(pw) == 20 and r.json()["item"]["must_change_password"] is True
    assert old.get("/api/auth/me").status_code == 401  # old sessions ended
    fresh = env.client()
    assert fresh.login("priya", PASSWORD).status_code == 401
    assert fresh.login("priya", pw).status_code == 200
    assert garg.post("/api/admin/users/9999/reset-password").status_code == 404


def test_reset_password_clears_lockout(env, garg):
    api = env.client()
    for i in range(5):
        api.login("jordan", "wrong password %d" % i)
    assert api.login("jordan").status_code == 423
    jid = uid(garg, "jordan")
    assert [u for u in garg.get("/api/admin/users").json()["items"] if u["id"] == jid][0]["locked"] is True
    pw = garg.post(f"/api/admin/users/{jid}/reset-password").json()["password"]
    assert api.login("jordan", pw).status_code == 200


def test_audit_log_paginates_and_filters(env, garg, priya):
    for i in (1, 2, 3):
        priya.post(f"/api/callbacks/{i}/claim")
    page1 = garg.get("/api/admin/audit?limit=5").json()
    assert page1["limit"] == 5 and len(page1["items"]) == 5 and page1["total"] > 5
    ids1 = [i["id"] for i in page1["items"]]
    assert ids1 == sorted(ids1, reverse=True)
    page2 = garg.get("/api/admin/audit?limit=5&offset=5").json()
    assert not set(ids1) & {i["id"] for i in page2["items"]}
    by_tool = garg.get("/api/admin/audit?tool=Callback list").json()
    assert by_tool["total"] == 3 and all(i["tool"] == "Callback list" for i in by_tool["items"])
    by_actor = garg.get("/api/admin/audit?actor=Priya&action=callback_claimed").json()
    assert by_actor["total"] == 3
    by_text = garg.get("/api/admin/audit?q=claimed Walker").json()
    assert by_text["total"] == 1 and "Walker" in by_text["items"][0]["text"]
    assert by_text["items"][0]["ip"] is not None and "data_json" in by_text["items"][0]
    assert garg.get("/api/admin/audit?limit=1000").status_code == 422
    # Admin and Sign-in events are visible here, unlike the feed
    assert garg.get("/api/admin/audit?tool=Sign-in").json()["total"] >= 2
    future = garg.get("/api/admin/audit?since=2999-01-01T00:00:00Z").json()
    assert future["total"] == 0


def test_settings_get_put(env, garg, priya):
    s = garg.get("/api/admin/settings").json()["settings"]
    assert s["clinic_name"] and s["banner_on"] is True
    r = garg.put("/api/admin/settings", {"settings": {"clinic_name": "Maple Street Vets", "banner_on": False, "wording.opening": {"a": 1}}})
    assert r.status_code == 200 and r.json()["settings"]["banner_on"] is False
    me = priya.get("/api/auth/me").json()
    assert me["settings"] == {"clinic_name": "Maple Street Vets", "banner_on": False}
    assert garg.put("/api/admin/settings", {"settings": {"banner_on": "yes"}}).status_code == 400
    assert garg.put("/api/admin/settings", {"settings": {"clinic_name": ""}}).status_code == 400
    assert garg.put("/api/admin/settings", {"settings": {"Bad Key!": 1}}).status_code == 400
    assert garg.put("/api/admin/settings", {"settings": {"ai_key": "sk-secret"}}).status_code == 400
    assert garg.put("/api/admin/settings", {"settings": {}}).status_code == 400
    assert garg.put("/api/admin/settings", {"settings": {"clinic_name": "x" * 121}}).status_code == 400
    audit = garg.get("/api/admin/audit?action=settings_updated").json()["items"]
    assert audit and "clinic_name" in audit[0]["text"]
    with env.session() as db:
        assert db.get(Setting, "ai_key") is None


def test_reset_demo_restores_ops_data_and_keeps_the_rest(env, garg, priya):
    priya.post("/api/callbacks/1/claim")
    priya.post("/api/drafts/1/approve")
    garg.put("/api/admin/settings", {"settings": {"clinic_name": "Kept Name"}})
    with env.session() as db:
        db.add(CallAnalytic(metric="calls_by_hour", dim1="9", value=3.0, n=12))
        db.add(ImportedCall(call_id="x-1", contains_private_content=False))
        db.commit()
    before_users = garg.get("/api/admin/users").json()
    before_events = garg.get("/api/admin/audit?limit=1").json()["total"]
    resets_before = garg.get("/api/admin/audit?action=demo_reset").json()["total"]

    r = garg.post("/api/admin/reset-demo")
    assert r.status_code == 200 and r.json()["counts"]["callbacks"] == 7 and r.json()["counts"]["outbox"] == 0

    items = priya.get("/api/callbacks").json()["items"]
    assert items[0]["client_label"] == "Dhillon" and items[0]["status"] == "new"
    assert {d["status"] for d in priya.get("/api/drafts").json()["items"]} == {"pending"}
    assert garg.get("/api/admin/users").json() == before_users
    assert garg.get("/api/admin/settings").json()["settings"]["clinic_name"] == "Kept Name"
    with env.session() as db:
        assert db.scalar(select(CallAnalytic.id)) is not None and db.scalar(select(ImportedCall.id)) is not None
    audit = garg.get("/api/admin/audit?action=demo_reset").json()
    assert audit["total"] == resets_before + 1 and audit["items"][0]["text"] == "Demo data reset" and audit["items"][0]["actor_name"] == "Dr. Garg"
    # old events stay (append-only), one new one added
    assert garg.get("/api/admin/audit?limit=1").json()["total"] == before_events + 1


def test_reset_demo_needs_admin_flag(env, priya):
    assert priya.post("/api/admin/reset-demo").status_code == 403
