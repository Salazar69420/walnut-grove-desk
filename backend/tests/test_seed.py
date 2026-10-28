from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models import (
    AppointmentRequest, Callback, CallLogDemo, Conversation, Draft, EmergencyRouting, Event, Hospital,
    LabResult, OpenSlot, Shift, Suggestion, User, VisitType,
)
from app.seed import RESET_TABLES, create_users, reset_demo
from app.seed import reference_data as ref
from app.seed.__main__ import main as seed_main
from app.services import timeutil


def snapshot(s):
    out = {}
    for m in RESET_TABLES:
        cols = [c.name for c in m.__table__.columns]
        rows = [tuple(getattr(r, c) for c in cols) for r in s.scalars(select(m).order_by(m.id)).all()]
        out[m.__tablename__] = rows
    return out


def test_reset_is_idempotent(env):
    with env.session() as s:
        now = timeutil.now_utc()
        reset_demo(s, None, now=now)
        s.commit()
        first = snapshot(s)
        ev1 = s.scalar(select(func.count()).select_from(Event))
        reset_demo(s, None, now=now)
        s.commit()
        reset_demo(s, None, now=now)
        s.commit()
        assert snapshot(s) == first
        # events only grow by exactly one "Demo data reset" per reset
        assert s.scalar(select(func.count()).select_from(Event)) == ev1 + 2


def test_seed_contents_match_reference(env):
    with env.session() as s:
        assert s.scalar(select(func.count()).select_from(Callback)) == 7
        assert s.scalar(select(func.count()).select_from(LabResult)) == 5
        assert s.scalar(select(func.count()).select_from(Draft)) == 3
        assert s.scalar(select(func.count()).select_from(Suggestion)) == 4
        assert s.scalar(select(func.count()).select_from(Shift)) == len(ref.ROSTER)
        assert s.scalar(select(func.count()).select_from(Conversation)) == 5
        assert s.scalar(select(func.count()).select_from(VisitType)) == 5
        assert s.scalar(select(func.count()).select_from(OpenSlot)) == 3
        assert s.scalar(select(func.count()).select_from(AppointmentRequest)) == 2
        assert s.scalar(select(func.count()).select_from(EmergencyRouting)) == 2
        h = s.scalars(select(Hospital).order_by(Hospital.sort)).all()
        assert h[0].name == "Boundary Bay Veterinary Specialty Hospital" and h[0].place == "Surrey"
        assert h[0].confirmed is False and h[0].phone == "" and h[0].address == ""
        assert h[0].hours_claim == "Says it is open 24 hours a day, 7 days a week"
        assert h[1].name == "Backup hospital" and h[1].is_placeholder is True
        # missed calls last week: 41
        missed = s.scalar(select(func.count()).select_from(CallLogDemo).where(CallLogDemo.answered.is_(False)))
        assert missed == 41 + 32
        # the claimed callback is linked to the account by display name
        c4 = s.get(Callback, 4)
        assert c4.owner_name == "Priya" and c4.status == "claimed" and c4.owner_id is not None
        c7 = s.get(Callback, 7)
        assert c7.source == "overnight" and c7.conversation_id == 4
        conv = s.get(Conversation, 1)
        assert json.loads(conv.messages_json)[0]["who"] in ("bot", "client")


def test_time_anchoring_in_vancouver(env):
    # Freeze "now" at 9:41 am Vancouver on a known day
    now = timeutil.at_local(timeutil.today_local(timeutil.now_utc()), 9, 41)
    with env.session() as s:
        reset_demo(s, None, now=now)
        s.commit()
        today = timeutil.today_local(now)
        c1 = s.get(Callback, 1)
        assert timeutil.local(c1.received_at).strftime("%H:%M") == "09:31" and timeutil.local_date(c1.received_at) == today
        # overnight: evening times belong to yesterday, early morning to today
        o3 = s.get(Conversation, 3)
        o5 = s.get(Conversation, 5)
        assert timeutil.local_date(o3.created_at) == today - timedelta(days=1) and timeutil.local(o3.created_at).strftime("%H:%M") == "23:40"
        assert timeutil.local_date(o5.created_at) == today and timeutil.local(o5.created_at).strftime("%H:%M") == "05:30"
        r1 = s.get(LabResult, 1)
        assert timeutil.age_days(r1.received_at, now) == 3
        assert timeutil.age_days(s.get(LabResult, 5).received_at, now) == 0
        # stored as UTC
        raw = s.connection().exec_driver_sql("select received_at from callbacks where id = 1").scalar()
        assert str(raw).startswith(str(c1.received_at.strftime("%Y-%m-%d %H:%M")))


def test_create_users_refuses_to_overwrite(env):
    with env.session() as s:
        with pytest.raises(RuntimeError, match="refusing to overwrite"):
            create_users(s)


def test_create_users_fresh_db(tmp_path, monkeypatch, capsys):
    from app import config, db

    path = tmp_path / "fresh.db"
    monkeypatch.setenv("WALNUT_DB_PATH", str(path))
    config.set_settings(config.load_settings())
    try:
        assert seed_main(["--create-users"]) == 0
        out = capsys.readouterr().out.strip().splitlines()
        rows = [line.split("\t") for line in out]
        assert [r[0] for r in rows] == ["garg", "priya", "marcus", "anita", "jordan", "krish"]
        assert all(len(r[1]) == 20 for r in rows) and len({r[1] for r in rows}) == 6
        with db.get_sessionmaker()() as s:
            users = {u.username: u for u in s.scalars(select(User)).all()}
            assert all(u.must_change_password for u in users.values())
            assert users["garg"].is_admin and users["krish"].is_admin and not users["priya"].is_admin
            assert users["garg"].password_hash.startswith("$argon2id$")
        assert seed_main(["--create-users"]) == 1  # second run refuses
        assert seed_main(["--reset-demo"]) == 0
        assert seed_main([]) == 2
    finally:
        config.set_settings(None)
