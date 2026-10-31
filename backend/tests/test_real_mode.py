"""Contacts from call transcripts (pipeline/people.py, pipeline/app_contacts.py) and real-data mode (app/seed/real_mode.py)."""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Callback, Contact, Message, Pet, PhoneCall, KnowledgeEntry, Hospital, User, Shift
from app.seed import real_mode

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from pipeline.people import extract  # noqa: E402

GREET = "Speaker 1: Walnut Grove Animal Hospital, how can I help you?\n"


def call(client_lines: str) -> str:
    return GREET + client_lines


# ---------------------------------------------------------------- extraction
def test_full_name_and_pet_from_the_callers_own_words():
    f = extract(call("Speaker 2: Hi, my name is Morgan Fletcher, calling about my dog Biscuit, she needs her vaccines.\nSpeaker 1: Sure."), set())
    assert (f.first, f.last, f.pet, f.species) == ("Morgan", "Fletcher", "Biscuit", "dog")


def test_staff_names_and_the_clinic_speaker_are_never_the_caller():
    text = "Speaker 1: Walnut Grove Animal Hospital, this is Kirsten.\nSpeaker 2: Hi, I need to book an appointment for my cat Mochi."
    f = extract(text, {"kirsten"})
    assert f.first is None and f.pet == "Mochi" and f.species == "cat"
    assert extract("Speaker 1: Hello, my name is Morgan Fletcher.\nSpeaker 2: Hi.", set()) is None  # nobody said the clinic greeting


@pytest.mark.parametrize("line", [
    "Speaker 2: It's Monday morning and my dog is in the car.",
    "Speaker 2: This is Walnut Grove, right? I'm calling to ask about my dog to be seen.",
    "Speaker 2: Yeah, my cat has a cough.",
    "Speaker 2: I'm Sorry, what was that?",
])
def test_ordinary_words_do_not_become_names_or_pets(line):
    assert extract(call(line), set()) is None


def test_first_name_alone_is_found_but_not_enough_on_its_own():
    f = extract(call("Speaker 2: Hi, this is Dana calling."), set())
    assert f.first == "Dana" and f.last is None and f.pet is None


# ---------------------------------------------------------------- import with contacts
def _run(env, csv_path, *extra):
    return subprocess.run([sys.executable, "-m", "pipeline.app_import", "--csv", str(csv_path), "--db", str(env.path), *extra], cwd=REPO,
                          capture_output=True, text=True, env={**os.environ, "WALNUT_ENV": "test", "WALNUT_SECRET_KEY": "y" * 40})


def _csv(path):
    rows = [
        ("20260802-091500_102_null_Outgoing_Auto_1.mp3", "2026-08-02", "09:15:00", call("Speaker 2: Hi, my name is Morgan Fletcher. I need a refill on my dog Biscuit's medication please.\nSpeaker 1: I will have the doctor approve it and call you back tomorrow.\nSpeaker 2: Thank you.")),
        ("20260805-101500_101_null_Outgoing_Auto_2.mp3", "2026-08-05", "10:15:00", call("Speaker 2: Hello, this is Morgan Fletcher again about my dog Biscuit's refill, can you confirm?\nSpeaker 1: Yes, it is ready for pickup.\nSpeaker 2: Great, thank you.")),
        ("20260806-111500_103_null_Outgoing_Auto_3.mp3", "2026-08-06", "11:15:00", call("Speaker 2: Hi, I would like to book an appointment for my cat. This is Dana calling.\nSpeaker 1: Sure, how about Thursday?\nSpeaker 2: Okay thanks.")),
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "date", "time", "number", "direction", "audio_seconds", "cost_usd", "language", "transcript", "plain_text"])
        for f, d, t, text in rows:
            w.writerow([f, d, t, "", "Outgoing", 75, 0.01, "eng", text, text.replace("Speaker 1: ", "").replace("Speaker 2: ", "")])


def test_contacts_are_created_with_no_consent_no_phone_and_linked_calls(env, garg, tmp_path):
    p = tmp_path / "c.csv"
    _csv(p)
    out = _run(env, p)
    assert out.returncode == 0, out.stderr
    with env.session() as s:
        cs = s.scalars(select(Contact).where(Contact.is_demo.is_(False))).all()
        names = {c.display_name for c in cs}
        assert "Morgan Fletcher" in names and "Dana" not in names  # a first name alone is never a contact
        m = next(c for c in cs if c.display_name == "Morgan Fletcher")
        assert m.phone is None and m.email is None and m.sms_consent == "none" and m.email_consent == "none" and m.ai_enabled is False
        assert "No consent recorded" in m.consent_source and "check" in m.preference_notes.lower()
        assert [p.name for p in s.scalars(select(Pet).where(Pet.contact_id == m.id)).all()] == ["Biscuit"]
        linked = s.scalars(select(PhoneCall).where(PhoneCall.contact_id == m.id)).all()
        assert len(linked) == 2 and all(c.call_type in ("patient", "inquiry") for c in linked)
        assert m.kind == "patient"  # a refill and a pickup mean an existing relationship
        msgs = s.scalars(select(Message).where(Message.contact_id == m.id, Message.channel == "call")).all()
        assert len(msgs) == 2 and all("Morgan" not in x.body and "Biscuit" not in x.body for x in msgs)  # lines hold fixed wording, not call text
    again = _run(env, p)
    assert "contacts_created=0" in again.stdout and "calls_linked=0" in again.stdout
    with env.session() as s:
        assert len(s.scalars(select(Contact).where(Contact.display_name == "Morgan Fletcher")).all()) == 1
    # the owner can see the card and the call line
    r = garg.get(f"/api/contacts?q=Fletcher").json()
    assert r["total"] == 1 and r["items"][0]["sms_consent"] == "none"


def test_nobody_without_consent_can_be_messaged_from_an_imported_contact(env, garg, tmp_path):
    p = tmp_path / "c.csv"
    _csv(p)
    _run(env, p)
    cid = garg.get("/api/contacts?q=Fletcher").json()["items"][0]["id"]
    r = garg.post(f"/api/contacts/{cid}/messages", {"channel": "sms", "body": "hello"})
    assert r.status_code == 409


# ---------------------------------------------------------------- real-data mode
def test_purge_removes_every_demo_row_keeps_real_rows_and_blocks_reset(env, garg):
    with env.session() as s:
        s.add(Contact(kind="inquiry", display_name="Real Person", consent_source="x", is_demo=False))
        s.add(PhoneCall(started_at=__import__("app.services.timeutil", fromlist=["now_utc"]).now_utc(), direction="unknown", handled_by="human", result="answered",
                        call_type="unknown", source="import", external_ref="imp-1", is_demo=False))
        s.commit()
        users_before = len(s.scalars(select(User)).all())
        hospitals_before = len(s.scalars(select(Hospital)).all())
        assert real_mode.counts(s)  # there is demo data to remove
        real_mode.purge_demo(s)
        s.commit()
        assert real_mode.counts(s) == {}
        assert [c.display_name for c in s.scalars(select(Contact)).all()] == ["Real Person"]
        assert len(s.scalars(select(PhoneCall)).all()) == 1
        assert not s.scalars(select(Callback)).all() and not s.scalars(select(Shift)).all() and not s.scalars(select(KnowledgeEntry)).all()
        assert len(s.scalars(select(User)).all()) == users_before and len(s.scalars(select(Hospital)).all()) == hospitals_before
    r = garg.post("/api/admin/reset-demo")
    assert r.status_code == 409 and "real data" in r.json()["detail"]
    with env.session() as s:
        assert [c.display_name for c in s.scalars(select(Contact)).all()] == ["Real Person"]


def test_call_lines_carry_their_call_so_the_timeline_can_offer_the_transcript(env, garg, priya, tmp_path):
    p = tmp_path / "c.csv"
    _csv(p)
    _run(env, p)
    cid = garg.get("/api/contacts?q=Fletcher").json()["items"][0]["id"]
    with env.session() as s:
        msgs = s.scalars(select(Message).where(Message.contact_id == cid, Message.channel == "call")).all()
        assert msgs and all(m.phone_call_id for m in msgs)
    items = [i for i in garg.get(f"/api/contacts/{cid}/timeline?limit=50").json()["items"] if i["channel"] == "call"]
    assert items and all(i["meta"]["call"]["has_transcript"] for i in items)
    call_id = items[0]["meta"]["call"]["id"]
    # owner downloads it; the desk cannot
    r = garg.get(f"/api/phone/calls/{call_id}/transcript.txt")
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment; filename=\"call-2026-08-")
    assert "Walnut Grove Animal Hospital" in r.text and r.text.startswith("Call transcript")
    assert "Morgan" not in r.headers["content-disposition"]  # the file name never carries a name
    assert priya.get(f"/api/phone/calls/{call_id}/transcript.txt").status_code == 403
    with env.session() as s:
        from app.models import AccessLog
        assert s.scalar(select(AccessLog).where(AccessLog.detail == "phone transcript download")) is not None


def test_the_migration_links_existing_call_lines(tmp_path):
    """Rows written before migration 0007 had no link; the migration matches them on contact and start time."""
    import sqlite3
    from alembic import command
    from app import migrate
    path = str(tmp_path / "old.db")
    cfg = migrate.alembic_config(path)
    command.upgrade(cfg, "0006")
    con = sqlite3.connect(path)
    con.execute("INSERT INTO contacts(id,kind,display_name,email_consent,sms_consent,consent_source,source,ai_enabled,preference_notes,created_at,last_active_at,is_demo) "
                "VALUES (9001,'inquiry','Link Test','none','none','x','phone',0,'', '2026-08-01 09:00:00','2026-08-01 09:00:00',0)")
    con.execute("INSERT INTO phone_calls(id,started_at,direction,handled_by,agent_label,contact_id,duration_seconds,result,call_type,short_note,source,is_demo) "
                "VALUES (9002,'2026-08-01 16:15:00','inbound','human','',9001,60,'answered','inquiry','n','import',0)")
    con.execute("INSERT INTO messages(contact_id,channel,direction,sender_kind,subject,body,delivery_state,created_at,is_demo) "
                "VALUES (9001,'call','in','client','','n','received','2026-08-01 16:15:00',0)")
    con.commit()
    con.close()
    command.upgrade(cfg, "head")
    con = sqlite3.connect(path)
    assert con.execute("select phone_call_id from messages where contact_id=9001").fetchone()[0] == 9002
    con.close()
