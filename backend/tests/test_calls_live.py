"""Live calls from TELUS land in the Phone log once, linked to the one contact with that number."""
from __future__ import annotations

from sqlalchemy import select

from app.models import Contact, PhoneCall
from app.services import calls_live


def test_new_calls_are_added_once_and_linked(env):
    with env.session() as s:
        c = s.scalar(select(Contact).where(Contact.is_demo.is_(True)).order_by(Contact.id).limit(1))
        c.phone = "+16045550161"
        s.add(PhoneCall(started_at=c.created_at, direction="inbound", source="import", external_ref="imp-x", recording_id="777", is_demo=False))
        s.commit()
        cid = c.id
    data = {"v": 1, "calls": [
        {"id": "a1", "at": "2026-10-31T17:00:00+00:00", "d": "inbound", "r": "answered", "s": 95, "n": "+16045550161", "rec": None},
        {"id": "a2", "at": "2026-10-31T17:10:00+00:00", "d": "inbound", "r": "missed", "s": 0, "n": "+17785550188", "rec": None},
        {"id": "a3", "at": "2026-10-31T16:00:00+00:00", "d": "outbound", "r": "answered", "s": 60, "n": "+16045550199", "rec": "777"},
        {"id": "../bad", "at": "x"},
    ]}
    with env.session() as s:
        assert calls_live.ingest(s, data) == {"added": 2, "numbers_added": 1}
    with env.session() as s:
        assert calls_live.ingest(s, data) == {"added": 0, "numbers_added": 0}
        a1 = s.scalar(select(PhoneCall).where(PhoneCall.external_ref == "tel-a1"))
        a2 = s.scalar(select(PhoneCall).where(PhoneCall.external_ref == "tel-a2"))
        assert a1.contact_id == cid and a1.source == "telus" and a1.duration_seconds == 95
        assert a2.contact_id is None and a2.handled_by == "none" and a2.result == "missed"
        assert s.scalar(select(PhoneCall).where(PhoneCall.recording_id == "777")).phone == "+16045550199"
