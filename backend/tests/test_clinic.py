"""Knowledge & Fees, Phone, Campaigns and Reports (docs/PLAN_CLINIC_CRM.md phases 4, 5, 7, 8)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import AccessLog, CampaignRecipient, Contact, Message, Outbox, PhoneCall
from app.services import knowledge as kn


# ---------------------------------------------------------------- knowledge
def test_seed_has_approved_pending_and_conflicts(priya):
    r = priya.get("/api/knowledge?status=pending").json()
    assert r["counts"]["pending"] >= 8 and r["counts"]["conflicts"] >= 2 and r["counts"]["approved"] >= 10
    assert all(i["status"] == "pending" for i in r["items"])
    conflict = next(i for i in r["items"] if i["conflicts"])
    assert conflict["previous_body"] and conflict["previous_body"] != conflict["body"]


def test_only_approved_entries_are_listed_as_used(priya):
    approved = priya.get("/api/knowledge?status=approved").json()["items"]
    assert approved and all(i["status"] == "approved" for i in approved)


def test_suggest_then_owner_approves_and_desk_cannot(priya, garg):
    r = priya.post("/api/knowledge", {"category": "clinic_info", "title": "Parking", "body": "Free parking behind the building."})
    assert r.status_code == 201 and r.json()["item"]["status"] == "pending"
    eid = r.json()["item"]["id"]
    assert priya.post(f"/api/knowledge/{eid}/approve").status_code == 403
    assert garg.post(f"/api/knowledge/{eid}/approve").json()["item"]["status"] == "approved"
    assert garg.post(f"/api/knowledge/{eid}/approve").status_code == 409  # already decided


def test_replacing_an_approved_entry_retires_it_and_bumps_the_version(priya, garg):
    old = next(i for i in priya.get("/api/knowledge?status=approved&category=clinic_info").json()["items"] if i["title"] == "Where we are")
    r = priya.post("/api/knowledge", {"category": "clinic_info", "title": "Where we are", "body": "A new address text.", "replaces_id": old["id"]})
    assert r.status_code == 201
    new = r.json()["item"]
    assert new["conflicts"] and new["previous_body"] == old["body"] and new["supersedes_id"] == old["id"]
    garg.post(f"/api/knowledge/{new['id']}/approve")
    assert garg.get(f"/api/knowledge/{old['id']}").json()["item"]["status"] == "retired"
    assert garg.get(f"/api/knowledge/{new['id']}").json()["item"]["version"] == old["version"] + 1


def test_duplicates_are_refused(priya):
    old = priya.get("/api/knowledge?status=approved").json()["items"][0]
    same = priya.post("/api/knowledge", {"category": old["category"], "title": old["title"], "body": old["body"]})
    assert same.status_code == 409
    a = {"category": "specials", "title": "Spring", "body": "Some offer."}
    assert priya.post("/api/knowledge", a).status_code == 201
    assert priya.post("/api/knowledge", a).status_code == 409


def test_edit_only_while_pending_and_nurse_may_suggest(anita, priya, garg):
    r = anita.post("/api/knowledge", {"category": "patient_knowledge", "title": "Fasting note", "body": "Text one."})
    assert r.status_code == 201
    eid = r.json()["item"]["id"]
    assert priya.patch(f"/api/knowledge/{eid}", {"body": "Text two."}).json()["item"]["body"] == "Text two."
    garg.post(f"/api/knowledge/{eid}/approve")
    assert priya.patch(f"/api/knowledge/{eid}", {"body": "Text three."}).status_code == 409


def test_decline_and_bulk(garg, priya):
    pend = garg.get("/api/knowledge?status=pending").json()["items"]
    first, rest = pend[0], pend[1:4]
    assert garg.post(f"/api/knowledge/{first['id']}/decline", {"reason": "Not right"}).json()["item"]["decline_reason"] == "Not right"
    r = garg.post("/api/knowledge/bulk", {"ids": [e["id"] for e in rest] + [first["id"]], "action": "approve"}).json()
    assert r["done"] == len(rest) and r["skipped"] == 1
    assert priya.post("/api/knowledge/bulk", {"ids": [1], "action": "approve"}).status_code == 403


def test_ai_proposals_wait_for_a_person(env, garg):
    with env.session() as s:
        e = kn.propose(s, category="clinic_info", title="Late opening", body="Open until 8 pm on Thursdays.", proposed_by="ai", source_kind="call", source_ref="call, this week", confidence=70)
        s.commit()
        assert e.status == "pending" and e.proposed_by == "ai"
        with pytest.raises(Exception):
            kn.propose(s, category="clinic_info", title="Late opening", body="Open until 8 pm on Thursdays.", proposed_by="ai")
    items = garg.get("/api/knowledge?status=approved&q=Late opening").json()["items"]
    assert items == []


def test_fees_flow(priya, garg):
    r = priya.post("/api/fees", {"service": "Booster vaccine", "price_dollars": 70})
    assert r.status_code == 201 and r.json()["item"]["status"] == "pending"
    fid = r.json()["item"]["id"]
    assert priya.post(f"/api/fees/{fid}/approve").status_code == 403
    assert garg.post(f"/api/fees/{fid}/approve").json()["item"]["price_cents"] == 7000
    approved = [f for f in garg.get("/api/fees?status=approved").json()["items"] if f["service"] == "Booster vaccine"]
    assert len(approved) == 1 and approved[0]["id"] == fid  # the older example price was retired
    assert priya.patch(f"/api/fees/{fid}", {"note": "x"}).status_code == 409


# ---------------------------------------------------------------- phone
def test_phone_list_filters_and_pagination(priya):
    allc = priya.get("/api/phone/calls?per_page=100").json()
    assert allc["total"] >= 30
    ai = priya.get("/api/phone/calls?handled_by=ai&per_page=100").json()
    assert ai["total"] > 0 and all(c["handled_by"] == "ai" for c in ai["items"])
    page2 = priya.get("/api/phone/calls?per_page=10&page=2").json()
    assert len(page2["items"]) == 10
    assert priya.get("/api/phone/calls?result=nonsense").status_code == 422


def test_phone_summary_shape(priya):
    s = priya.get("/api/phone/summary?days=7").json()
    assert s["total"] == s["inbound"] + s["outbound"] and len(s["by_hour"]) == 24 and len(s["by_day"]) == 7
    assert 0 <= s["ai_share_pct"] <= 100


def test_transcript_only_for_owner_and_is_logged(env, priya, garg, anita):
    with env.session() as s:
        call = s.scalar(select(PhoneCall).where(PhoneCall.transcript.is_not(None)).limit(1))
        cid = call.id
    d = priya.get(f"/api/phone/calls/{cid}").json()["item"]
    assert d["has_transcript"] and d["transcript"] is None
    o = garg.get(f"/api/phone/calls/{cid}").json()["item"]
    assert o["transcript"]
    with env.session() as s:
        n = s.scalar(select(AccessLog).where(AccessLog.detail == "phone transcript"))
        assert n is not None
    assert anita.get(f"/api/phone/calls/{cid}").json()["item"]["transcript"] is None


def test_phone_export_permission(priya, anita):
    r = priya.get("/api/export/phone.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and r.text.splitlines()[0].startswith("when,direction")
    assert anita.get("/api/export/phone.csv").status_code == 403


def test_match_a_call_to_a_contact(env, priya):
    with env.session() as s:
        call = s.scalar(select(PhoneCall).where(PhoneCall.contact_id.is_(None), PhoneCall.handled_by != "none").limit(1))
        c = s.scalar(select(Contact).where(Contact.kind == "patient").limit(1))
        call_id, cid = call.id, c.id
    r = priya.patch(f"/api/phone/calls/{call_id}", {"contact_id": cid, "short_note": "Matched by the desk"})
    assert r.status_code == 200 and r.json()["item"]["contact"]["id"] == cid and r.json()["item"]["call_type"] == "patient"


# ---------------------------------------------------------------- campaigns
def _daytime(monkeypatch):
    monkeypatch.setattr("app.services.campaigns.in_quiet_hours", lambda now=None: False)


def _quiet(monkeypatch):
    monkeypatch.setattr("app.services.campaigns.in_quiet_hours", lambda now=None: True)


def test_quiet_hours_window():
    from app.services.campaigns import in_quiet_hours
    assert in_quiet_hours(datetime(2026, 11, 3, 8, 30, tzinfo=timezone.utc))      # 00:30 in Vancouver
    assert not in_quiet_hours(datetime(2026, 11, 2, 19, 30, tzinfo=timezone.utc))  # 11:30 in Vancouver


NEW = {"name": "Test blast", "channel": "sms", "purpose": "promo", "segment": {"kind": "patient"}, "subject": "", "body": "Hi {first_name}, news for {pet_name} from {clinic_name}."}


def test_preview_explains_who_is_left_out(priya):
    r = priya.post("/api/campaigns/preview", {k: NEW[k] for k in ("channel", "purpose", "segment", "subject", "body")}).json()
    assert r["matched"] == r["eligible"] + sum(r["skipped"].values())
    assert r["eligible"] > 0 and r["skipped"]
    assert r["samples"] and "{" not in r["samples"][0]["text"] and "STOP" in r["samples"][0]["text"]


def test_promo_needs_full_consent_care_allows_care_only(priya):
    base = {"channel": "sms", "segment": {"kind": "patient"}, "subject": "", "body": "Hi {first_name}"}
    promo = priya.post("/api/campaigns/preview", {**base, "purpose": "promo"}).json()
    care = priya.post("/api/campaigns/preview", {**base, "purpose": "care"}).json()
    assert care["eligible"] >= promo["eligible"]
    assert promo["skipped"].get("care_only", 0) + promo["skipped"].get("no_consent", 0) + promo["skipped"].get("unsubscribed", 0) > 0


def test_unknown_merge_field_and_missing_subject_are_refused(priya):
    assert priya.post("/api/campaigns", {**NEW, "body": "Hi {owner_name}"}).status_code == 400
    assert priya.post("/api/campaigns", {**NEW, "channel": "email", "subject": ""}).status_code == 400


def test_full_lifecycle_dry_run_respects_consent(env, priya, garg, monkeypatch):
    _daytime(monkeypatch)
    cid = priya.post("/api/campaigns", NEW).json()["item"]["id"]
    assert priya.post(f"/api/campaigns/{cid}/run").status_code == 403
    assert garg.post(f"/api/campaigns/{cid}/approve").status_code == 409  # still a draft
    assert priya.post(f"/api/campaigns/{cid}/submit").json()["item"]["status"] == "awaiting_approval"
    assert priya.post(f"/api/campaigns/{cid}/approve").status_code == 403
    assert garg.post(f"/api/campaigns/{cid}/approve").json()["item"]["status"] == "approved"
    assert priya.patch(f"/api/campaigns/{cid}", {"name": "Changed"}).status_code == 409
    run = garg.post(f"/api/campaigns/{cid}/run")
    assert run.status_code == 200
    counts = run.json()["counts"]
    assert counts["sent"] > 0 and counts["matched"] == counts["sent"] + sum(counts["skipped"].values())
    assert garg.post(f"/api/campaigns/{cid}/run").status_code == 409  # cannot run twice
    with env.session() as s:
        recs = s.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == cid, CampaignRecipient.state == "dry_run_sent")).all()
        assert len(recs) == counts["sent"]
        for r in recs:
            c = s.get(Contact, r.contact_id)
            assert c.sms_consent == "full" and c.phone  # promo: full consent only
            m = s.get(Message, r.message_id)
            assert m.delivery_state == "dry_run" and m.direction == "out"
            assert "{" not in m.body and "STOP" in m.body
        skipped = s.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == cid, CampaignRecipient.state == "skipped")).all()
        assert all(r.message_id is None and r.skipped_reason for r in skipped)
        outbox = s.scalars(select(Outbox).where(Outbox.status == "would_send")).all()
        assert len(outbox) >= counts["sent"]


def test_quiet_hours_block_a_run(env, priya, garg, monkeypatch):
    _quiet(monkeypatch)
    cid = priya.post("/api/campaigns", NEW).json()["item"]["id"]
    priya.post(f"/api/campaigns/{cid}/submit")
    garg.post(f"/api/campaigns/{cid}/approve")
    r = garg.post(f"/api/campaigns/{cid}/run")
    assert r.status_code == 409 and "Quiet hours" in r.json()["detail"]


def test_erasing_a_contact_removes_campaign_rows(env, priya, garg, monkeypatch):
    _daytime(monkeypatch)
    cid = priya.post("/api/campaigns", {**NEW, "purpose": "care"}).json()["item"]["id"]
    priya.post(f"/api/campaigns/{cid}/submit")
    garg.post(f"/api/campaigns/{cid}/approve")
    garg.post(f"/api/campaigns/{cid}/run")
    with env.session() as s:
        victim = s.scalar(select(CampaignRecipient.contact_id).where(CampaignRecipient.campaign_id == cid))
    assert garg.post(f"/api/admin/contacts/{victim}/erase").status_code == 200
    with env.session() as s:
        assert s.scalar(select(CampaignRecipient.id).where(CampaignRecipient.contact_id == victim)) is None
        assert s.scalar(select(PhoneCall.id).where(PhoneCall.contact_id == victim)) is None


# ---------------------------------------------------------------- reports
@pytest.mark.parametrize("key", ["agents", "pending_knowledge", "conversion", "recall", "call_volume", "campaigns"])
def test_every_report_runs(key, priya, garg):
    for who in (priya, garg):
        r = who.get(f"/api/reports/{key}?from=2026-01-01&to=2026-12-31")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["key"] == key and body["stats"] and isinstance(body["sections"], list)


def test_reports_catalog_permissions_and_range(anita, priya):
    assert anita.get("/api/reports").status_code == 403
    assert len(priya.get("/api/reports").json()["items"]) == 6
    assert priya.get("/api/reports/nope").status_code == 404
    assert priya.get("/api/reports/agents?from=2026-12-31&to=2026-01-01").status_code == 400
    assert priya.get("/api/reports/agents?from=2020-01-01&to=2026-12-31").status_code == 400


def test_pending_report_counts_match_the_queue(garg):
    q = garg.get("/api/knowledge?status=pending").json()["counts"]["pending"]
    rep = garg.get("/api/reports/pending_knowledge").json()
    assert rep["stats"][0]["value"] == q


# ---------------------------------------------------------------- real-call import (pipeline/app_import.py)
def _run_import(env, csv_path, *extra):
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    return subprocess.run([sys.executable, "-m", "pipeline.app_import", "--csv", str(csv_path), "--db", str(env.path), *extra],
                          cwd=repo, capture_output=True, text=True, env={**__import__("os").environ, "WALNUT_ENV": "test", "WALNUT_SECRET_KEY": "x" * 40})


def _write_csv(path):
    import csv
    work = ("Speaker 1: Walnut Grove Animal Hospital, how can I help you?\nSpeaker 2: Hi, I would like to book an appointment for my dog for vaccines next week.\n"
            "Speaker 1: Sure, how about Thursday at ten?\nSpeaker 2: That works, thank you.")
    refill = ("Speaker 1: Walnut Grove Animal Hospital, how can I help?\nSpeaker 2: I need a refill on my cat's medication please.\n"
              "Speaker 1: I will have the doctor approve it and call you back tomorrow.\nSpeaker 2: Thank you.")
    rows = [
        ("20260801-091500_102_+16045550123_Outgoing_Auto_111.mp3", "2026-08-01", "09:15:00", "+16045550123", 62, "eng", work),
        ("20260801-101500_101_null_Outgoing_Auto_222.mp3", "2026-08-01", "10:15:00", "", 71, "eng", refill),
        ("20260801-111500_103_null_Outgoing_Auto_333.mp3", "2026-08-01", "11:15:00", "", 80, "hin", "yeh ek hindi baat cheet hai"),
        ("20260801-121500_102_null_Outgoing_Auto_444.mp3", "2026-08-01", "12:15:00", "", 20, "eng", "Speaker 1: Are you coming for lunch later?\nSpeaker 2: Yes, I will bring the salad."),
        ("20260801-091500_102_+16045550123_Outgoing_Auto_111.mp3", "2026-08-01", "09:15:00", "+16045550123", 62, "eng", work),  # same file twice
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "date", "time", "number", "direction", "audio_seconds", "cost_usd", "language", "transcript", "plain_text"])
        for f, d, t, n, secs, lang, text in rows:
            w.writerow([f, d, t, n, "Outgoing", secs, 0.01, lang, text, text.replace("Speaker 1: ", "").replace("Speaker 2: ", "")])
    return len(rows)


def test_import_is_safe_and_repeatable(env, garg, tmp_path):
    p = tmp_path / "calls.csv"
    n = _write_csv(p)
    dry = _run_import(env, p, "--dry-run")
    assert dry.returncode == 0, dry.stderr
    with env.session() as s:
        assert s.scalar(select(PhoneCall.id).where(PhoneCall.source == "import")) is None  # dry run wrote nothing
    first = _run_import(env, p)
    assert first.returncode == 0, first.stderr
    out = dict(kv.split("=") for kv in first.stdout.split("Done. ")[1].strip().split(", "))
    imported = int(out["imported"])
    assert imported >= 1 and int(out["rows"]) == n
    assert imported + int(out["already_there"]) + int(out["skipped_private_or_uncertain"]) + int(out["skipped_staff_or_personal"]) + int(out["bad_rows"]) == n
    assert int(out["skipped_private_or_uncertain"]) + int(out["skipped_staff_or_personal"]) >= 1  # the Hindi and the lunch chat stay out
    with env.session() as s:
        real = s.scalars(select(PhoneCall).where(PhoneCall.source == "import")).all()
        assert len(real) == imported and all(r.phone is None and r.contact_id is None and r.external_ref.startswith("imp-") for r in real)
        assert all(r.transcript for r in real)
        blob = " ".join(str(v) for r in real for v in (r.short_note, r.agent_label, r.external_ref))
        assert "5550123" not in blob and ".mp3" not in blob and "Outgoing" not in blob
        assert s.scalar(select(PhoneCall.id).where(PhoneCall.is_demo.is_(True))) is None  # example calls are gone
    again = _run_import(env, p)
    assert "imported=0" in again.stdout and f"already_there={imported + 1}" in again.stdout  # the file lists one recording twice
    listed = garg.get("/api/phone/calls?per_page=100").json()
    assert listed["total"] == imported
    cid = listed["items"][0]["id"]
    assert garg.get(f"/api/phone/calls/{cid}").json()["item"]["transcript"]


def test_reset_demo_keeps_real_calls_and_does_not_add_demo_calls(env, garg):
    from app.models import PhoneCall as PC
    from app.seed import reset_demo
    with env.session() as s:
        s.query(PC).delete()
        s.add(PC(started_at=__import__("app.services.timeutil", fromlist=["now_utc"]).now_utc(), direction="unknown", handled_by="human", result="answered",
                 call_type="unknown", source="import", external_ref="imp-test", transcript="x", is_demo=False))
        s.commit()
        reset_demo(s, None)
        s.commit()
        rows = s.scalars(select(PC)).all()
        assert [r.external_ref for r in rows] == ["imp-test"]
