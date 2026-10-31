"""Team chat: everyone channel, direct and group chats, unread counts, and privacy between conversations."""


def test_everyone_channel_and_direct_chat_with_unread_counts(env, garg, priya, anita):
    g = garg.get("/api/chats").json()
    everyone = next(t for t in g["items"] if t["kind"] == "everyone")
    priya_id = next(s["id"] for s in g["staff"] if s["name"].startswith("Priya"))
    assert garg.post(f"/api/chats/{everyone['id']}/messages", {"body": "Morning all, busy day."}).status_code == 201
    p = priya.get("/api/chats").json()
    assert p["unread"] >= 1 and next(t for t in p["items"] if t["kind"] == "everyone")["last"]["body"] == "Morning all, busy day."
    d = garg.post("/api/chats", {"user_ids": [priya_id]}).json()["item"]
    assert garg.post("/api/chats", {"user_ids": [priya_id]}).json()["item"]["id"] == d["id"]  # one conversation per pair
    garg.post(f"/api/chats/{d['id']}/messages", {"body": "Can you call the Shah family back?"})
    msgs = priya.get(f"/api/chats/{d['id']}/messages").json()
    assert [m["body"] for m in msgs["items"]] == ["Can you call the Shah family back?"] and msgs["items"][0]["who"]["name"].startswith("Dr")
    assert next(t for t in priya.get("/api/chats").json()["items"] if t["id"] == d["id"])["unread"] == 0  # read by opening
    assert anita.get(f"/api/chats/{d['id']}/messages").status_code == 404  # not a member
    assert anita.post(f"/api/chats/{d['id']}/messages", {"body": "hi"}).status_code == 404


def test_group_chat(env, garg, priya, anita):
    staff = garg.get("/api/chats").json()["staff"]
    ids = [s["id"] for s in staff if s["name"].startswith(("Priya", "Anita"))]
    t = garg.post("/api/chats", {"user_ids": ids, "title": "Surgery Thursday"}).json()["item"]
    assert t["kind"] == "group" and t["title"] == "Surgery Thursday"
    anita.post(f"/api/chats/{t['id']}/messages", {"body": "Prep room is ready."})
    assert priya.get(f"/api/chats/{t['id']}/messages").json()["items"][0]["body"] == "Prep room is ready."
