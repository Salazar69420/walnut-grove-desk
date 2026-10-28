from __future__ import annotations

import csv
import io

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.models import Callback, Event, Outbox
from app.services import events as ev
from app.services.broadcaster import broadcaster


def drain(q, wait=0.3):
    """Collect queued SSE messages. Delivery hops to the event loop thread, so allow a moment."""
    import time

    time.sleep(wait)
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ------------------------------------------------------------ callbacks
def test_callbacks_list_order_and_filters(priya):
    items = priya.get("/api/callbacks").json()["items"]
    # reference order: new (high first, newest first), claimed, done
    assert [i["client_label"] for i in items] == ["Dhillon", "Nguyen", "Walker", "Chen", "Fraser", "Langley Referral Clinic", "Lab courier"]
    assert items[0]["at_display"] == "9:31 am" and items[0]["priority"] == "high"
    assert items[1]["red_flag"] is True
    high = priya.get("/api/callbacks?filter=high").json()["items"]
    assert [i["client_label"] for i in high] == ["Dhillon", "Nguyen"]
    mine = priya.get("/api/callbacks?filter=mine").json()["items"]
    assert [i["client_label"] for i in mine] == ["Langley Referral Clinic"]
    assert priya.get("/api/callbacks?filter=bogus").status_code == 422


def test_claim_done_and_conflicts(env, priya, marcus, anita):
    r = priya.post("/api/callbacks/1/claim")
    assert r.status_code == 200
    item = r.json()["item"]
    assert item["status"] == "claimed" and item["owner_name"] == "Priya"
    # someone else gets 409 with the reference-style wording
    r = marcus.post("/api/callbacks/1/claim")
    assert r.status_code == 409 and r.json() == {"detail": "Already taken by Priya"}
    # claiming your own is a harmless repeat
    assert priya.post("/api/callbacks/1/claim").status_code == 200
    # another person cannot close it
    r = marcus.post("/api/callbacks/1/done")
    assert r.status_code == 409 and r.json() == {"detail": "Already taken by Priya"}
    r = priya.post("/api/callbacks/1/done")
    assert r.status_code == 200 and r.json()["item"]["status"] == "done"
    assert priya.post("/api/callbacks/1/done").status_code == 409
    assert marcus.post("/api/callbacks/1/claim").json() == {"detail": "Already closed"}
    assert priya.post("/api/callbacks/999/claim").status_code == 404
    # nurses may work callbacks too
    assert anita.post("/api/callbacks/2/claim").status_code == 200
    mine = anita.get("/api/callbacks?filter=mine").json()["items"]
    assert [i["client_label"] for i in mine] == ["Chen"]

    texts = [e["text"] for e in priya.get("/api/events").json()["items"]]
    assert "Priya claimed Dhillon (Simba (cat))" in texts
    assert "Priya closed Dhillon (results)" in texts
    assert "Anita claimed Chen (Biscuit (Labrador))" in texts


def test_two_users_race_one_winner(env, priya, marcus):
    # sequential here, but it is the same atomic UPDATE guard
    codes = sorted([priya.post("/api/callbacks/3/claim").status_code, marcus.post("/api/callbacks/3/claim").status_code])
    assert codes == [200, 409]


# ------------------------------------------------------------ results
def test_results_list_order(anita):
    items = anita.get("/api/results").json()["items"]
    assert [i["pet_label"] for i in items] == ["Maple (spaniel)", "Simba (cat)", "Luna (Husky)", "Rocky (Boxer)", "Oscar (beagle)"]
    assert [i["age_days"] for i in items] == [3, 2, 2, 1, 0]
    assert items[0]["stage_names"] == ["Received", "Doctor reviewed", "Client told"]
    assert items[0]["late"] is True and items[3]["late"] is False and items[4]["late"] is False


def test_results_advance(anita, marcus):
    r = anita.post("/api/results/1/advance")
    assert r.status_code == 200 and r.json()["item"]["stage"] == 1 and r.json()["item"]["stage_name"] == "Doctor reviewed"
    # stale click: client thought it was still at stage 0
    r = anita.post("/api/results/1/advance", {"from_stage": 0})
    assert r.status_code == 409
    r = anita.post("/api/results/1/advance", {"from_stage": 1})
    assert r.status_code == 200 and r.json()["item"]["stage"] == 2 and r.json()["item"]["told_by"] == "Anita"
    r = anita.post("/api/results/1/advance")
    assert r.status_code == 409 and r.json()["detail"].startswith("Already closed")
    assert anita.post("/api/results/999/advance").status_code == 404
    texts = [e["text"] for e in anita.get("/api/events").json()["items"]]
    assert "Maple (spaniel), Blood panel: doctor reviewed" in texts
    assert "Maple (spaniel), Blood panel: client told" in texts
    # closed results sink to the bottom
    items = anita.get("/api/results").json()["items"]
    assert items[-2]["pet_label"] in ("Maple (spaniel)", "Oscar (beagle)")
    assert marcus.get("/api/results").status_code == 200  # desk can read


# ------------------------------------------------------------ drafts
def test_draft_edit_and_approve_writes_outbox(env, priya, marcus):
    items = priya.get("/api/drafts").json()["items"]
    assert [d["status"] for d in items] == ["pending"] * 3
    r = priya.patch("/api/drafts/1", {"body": "Hi Ms. Chen, new wording."})
    assert r.status_code == 200 and r.json()["item"]["body"] == "Hi Ms. Chen, new wording." and r.json()["item"]["edited_by"] == "Priya"
    assert priya.patch("/api/drafts/1", {"body": ""}).status_code == 422
    assert priya.patch("/api/drafts/1", {"body": "x" * 2001}).status_code == 422
    r = priya.post("/api/drafts/1/approve")
    assert r.status_code == 200
    d = r.json()["item"]
    assert d["status"] == "sent" and d["approved_by"] == "Priya" and d["approved_at"]
    with env.session() as s:
        rows = s.scalars(select(Outbox)).all()
        assert len(rows) == 1
        assert rows[0].status == "would_send" and rows[0].channel == "sms" and rows[0].body == "Hi Ms. Chen, new wording."
        assert rows[0].to_label == "Ms. Chen, for Biscuit"
    texts = [e["text"] for e in priya.get("/api/events").json()["items"]]
    assert "Priya approved: Annual exam recall to Ms. Chen, for Biscuit" in texts
    assert marcus.post("/api/drafts/1/approve").json() == {"detail": "Already approved by Priya"}
    assert marcus.patch("/api/drafts/1", {"body": "late edit"}).status_code == 409


# ------------------------------------------------------------ shifts
def test_shifts_grouped_with_phone_cover(priya):
    days = priya.get("/api/shifts").json()["items"]
    assert [d["day"] for d in days] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    by = {d["day"]: d for d in days}
    # Mon: Marcus 8:00-16:30 covers 9:00, Priya starts 10:00 -> 1 on phones
    assert by["Mon"]["desk_at_9"] == 1 and by["Mon"]["cover"] == "warn"
    assert by["Sat"]["desk_at_9"] == 1 and by["Sat"]["cover"] == "warn" and by["Sat"]["busiest"] is True  # Marcus 8:30-16:00
    assert by["Tue"]["desk_at_9"] == 1 and by["Wed"]["desk_at_9"] == 1
    assert by["Sun"]["closed"] is True and by["Sun"]["cover"] is None and by["Sun"]["shifts"] == []
    assert by["Mon"]["shifts"][0]["label"] == "Marcus 08:00-16:30"
    assert {s["role"] for s in by["Mon"]["shifts"]} == {"desk", "nurse", "doc"}


# ------------------------------------------------------------ events and tools
def test_tools_cards_and_counts(priya):
    items = priya.get("/api/tools").json()["items"]
    assert [i["name"] for i in items] == ["Call logger", "Callback list", "Results tracker", "Recall sender", "After-hours assistant", "Staff roster", "ImproMed sync"]
    counts = {i["name"]: i["event_count"] for i in items}
    assert counts["Call logger"] == 3 and counts["Callback list"] == 0
    priya.post("/api/callbacks/1/claim")
    counts = {i["name"]: i["event_count"] for i in priya.get("/api/tools").json()["items"]}
    assert counts["Callback list"] == 1


def test_events_since_and_audit_tools_hidden(env, priya, garg):
    first = priya.get("/api/events").json()
    assert first["items"][0]["text"] == "Missed call from Walker, no voicemail"
    last = first["last_id"]
    assert priya.get("/api/events?since=%d" % last).json()["items"] == []
    priya.post("/api/callbacks/1/claim")
    new = priya.get("/api/events?since=%d" % last).json()["items"]
    assert [e["action"] for e in new] == ["callback_claimed"]
    # Admin and Sign-in events never show in the feed
    assert all(e["tool"] not in ("Admin", "Sign-in") for e in priya.get("/api/events?limit=200").json()["items"])
    assert priya.get("/api/events?limit=500").status_code == 422


def test_events_are_append_only(env):
    with env.session() as s:
        ev.record(s, ev.CALL_LOGGER, "test", "A test line", None, "x", 1)
        s.commit()
        with pytest.raises(DBAPIError) as exc:
            s.execute(text("UPDATE events SET text = 'changed' WHERE id = (SELECT min(id) FROM events)"))
        assert "events are append-only" in str(exc.value)
        s.rollback()
        with pytest.raises(DBAPIError) as exc:
            s.execute(text("DELETE FROM events"))
        assert "events are append-only" in str(exc.value)
        s.rollback()
        assert s.scalar(select(Event).where(Event.action == "test")) is not None


def test_record_rejects_unknown_tool(env):
    with env.session() as s:
        with pytest.raises(ValueError):
            ev.record(s, "Made up tool", "x", "y")


def test_publish_after_commit_only(env):
    q = broadcaster.subscribe()
    with env.session() as s:
        e = ev.record(s, ev.CALLBACK_LIST, "callback_claimed", "t", None, "callback", 7)
        assert drain(q, 0.1) == []  # nothing before the commit
        s.rollback()
        assert drain(q, 0.1) == []  # nothing after a rollback
        e = ev.record(s, ev.CALLBACK_LIST, "callback_claimed", "t", None, "callback", 7)
        s.commit()
    msgs = drain(q)
    assert {"entity": "callbacks", "id": 7, "event_id": e.id} in msgs
    assert {"entity": "events", "id": e.id, "event_id": e.id} in msgs


def test_api_action_publishes_change(env, priya):
    q = broadcaster.subscribe()
    priya.post("/api/callbacks/1/claim")
    entities = {m["entity"]: m for m in drain(q)}
    assert entities["callbacks"]["id"] == 1
    assert "events" in entities


def test_stream_needs_login(env):
    assert env.client().get("/api/stream").status_code == 401


# ------------------------------------------------------------ export
def test_export_escapes_formulas(env, priya):
    from app.services import timeutil

    with env.session() as s:
        for i, bad in enumerate(["=HYPERLINK(\"http://x\")", "+1+1", "-2+3", "@SUM(A1)", "\tTAB", "normal text"]):
            s.add(Callback(client_label=bad, pet_label="", what=bad, kind="Message", via="Call", received_at=timeutil.now_utc(), priority="normal"))
        s.commit()
    r = priya.get("/api/export/callbacks.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].startswith("attachment")
    rows = list(csv.reader(io.StringIO(r.text)))
    header = rows[0]
    clients = {row[header.index("client")] for row in rows[1:]}
    assert "'=HYPERLINK(\"http://x\")" in clients
    assert "'+1+1" in clients and "'-2+3" in clients and "'@SUM(A1)" in clients and "'\tTAB" in clients
    assert "normal text" in clients
    for row in rows[1:]:
        for cell in row:
            assert not cell.startswith(("=", "+", "-", "@"))


def test_export_all_lists(env, priya, garg):
    for name in ("callbacks", "results", "drafts", "shifts", "events"):
        r = priya.get(f"/api/export/{name}.csv")
        assert r.status_code == 200, name
        assert len(r.text.strip().splitlines()) >= 2
    shifts = list(csv.reader(io.StringIO(priya.get("/api/export/shifts.csv").text)))
    assert shifts[0] == ["id", "day", "person", "role", "start", "end", "proposed"]
    assert garg.get("/api/export/audit.csv").status_code == 200


def test_healthz(env):
    r = env.client().get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
