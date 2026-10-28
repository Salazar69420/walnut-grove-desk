from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import callbacks as callbacks_api
from app.api import serializers
from app.db import get_db
from app.models import Draft, Event, LabResult, Shift, User
from app.security.auth import require
from app.security.permissions import has_perm
from app.services import events as ev
from app.services import timeutil
from app.services.csvutil import to_csv
from app.services.timeutil import DAY_NAMES

router = APIRouter(prefix="/api/export", tags=["export"])

# export name -> perm that the list itself needs (checked on top of export.use)
LIST_PERM = {
    "callbacks": "callbacks.view",
    "results": "results.view",
    "drafts": "drafts.view",
    "shifts": "schedule.view",
    "events": "tools.view",
    "audit": "admin.audit",
    "overnight": "overnight.view",
    "analytics": "brief.view",
}


def _callbacks(db: Session, user: User):
    rows = db.scalars(callbacks_api.list_query("all", user)).all()
    now = timeutil.now_utc()
    header = ["id", "client", "pet", "what", "kind", "via", "received_at", "priority", "red_flag", "owner", "status", "source", "closed_at"]
    data = [
        [c.id, c.client_label, c.pet_label, c.what, c.kind, c.via, timeutil.iso(c.received_at), c.priority, c.red_flag,
         c.owner_name or "", c.status, c.source, timeutil.iso(c.closed_at) or ""]
        for c in rows
    ]
    return header, data


def _results(db: Session, user: User):
    now = timeutil.now_utc()
    rows = db.scalars(select(LabResult)).all()
    rows.sort(key=lambda r: (r.stage == 2, -timeutil.age_days(r.received_at, now), r.id))
    header = ["id", "pet", "owner", "test", "received_at", "age_days", "stage", "stage_name", "reviewed_by", "told_by"]
    data = [
        [r.id, r.pet_label, r.owner_label, r.test, timeutil.iso(r.received_at), timeutil.age_days(r.received_at, now),
         r.stage, serializers.STAGE_NAMES[r.stage], r.reviewed_by or "", r.told_by or ""]
        for r in rows
    ]
    return header, data


def _drafts(db: Session, user: User):
    rows = db.scalars(select(Draft).order_by(Draft.id)).all()
    header = ["id", "kind", "to", "body", "channel", "status", "edited_by", "approved_by", "approved_at"]
    data = [[d.id, d.kind, d.to_label, d.body, d.channel, d.status, d.edited_by or "", d.approved_by or "", timeutil.iso(d.approved_at) or ""] for d in rows]
    return header, data


def _shifts(db: Session, user: User):
    rows = db.scalars(select(Shift).order_by(Shift.weekday, Shift.start_min, Shift.id)).all()
    header = ["id", "day", "person", "role", "start", "end", "proposed"]
    data = [[s.id, DAY_NAMES[s.weekday], s.person_label, s.role, timeutil.hhmm(s.start_min), timeutil.hhmm(s.end_min), s.proposed] for s in rows]
    return header, data


def _events(db: Session, user: User):
    rows = db.scalars(select(Event).where(Event.tool.in_(ev.FEED_TOOLS)).order_by(Event.id.desc()).limit(5000)).all()
    header = ["id", "time", "tool", "who", "action", "text"]
    data = [[e.id, timeutil.iso(e.ts), e.tool, e.actor_name, e.action, e.text] for e in rows]
    return header, data


def _audit(db: Session, user: User):
    rows = db.scalars(select(Event).order_by(Event.id.desc()).limit(5000)).all()
    header = ["id", "time", "tool", "who", "action", "entity_type", "entity_id", "text", "ip"]
    data = [[e.id, timeutil.iso(e.ts), e.tool, e.actor_name, e.action, e.entity_type, e.entity_id, e.text, e.ip] for e in rows]
    return header, data


def _overnight(db: Session, user: User):
    from app.api import overnight as ov

    now = timeutil.now_utc()
    header = ["id", "client", "pet", "channel", "time", "kind", "status", "preferred_slot", "summary", "is_test", "source"]
    data = [[c["id"], c["client_label"], c["pet_label"], c["channel"], c["created_at"], c["kind"], c["status"],
             c["preferred_slot"] or "", c["summary"], c["is_test"], c["source"]]
            for c in (ov.item(r, now) for r in ov.ordered(db))]
    return header, data


def _analytics(db: Session, user: User):
    from app.models import CallAnalytic

    rows = db.scalars(select(CallAnalytic).order_by(CallAnalytic.metric, CallAnalytic.dim1, CallAnalytic.dim2, CallAnalytic.id)).all()
    header = ["source", "metric", "dim1", "dim2", "value", "n"]
    return header, [[r.source, r.metric, r.dim1, r.dim2, r.value, r.n] for r in rows]


BUILDERS = {"overnight": _overnight, "analytics": _analytics, "callbacks": _callbacks, "results": _results, "drafts": _drafts, "shifts": _shifts, "events": _events, "audit": _audit}


@router.get("/{name}.csv")
def export_csv(name: str, user: User = Depends(require("export.use")), db: Session = Depends(get_db)):
    builder = BUILDERS.get(name)
    if builder is None:
        raise HTTPException(status_code=404, detail="No such export")
    if not has_perm(user, LIST_PERM[name]):
        raise HTTPException(status_code=403, detail="forbidden")
    header, data = builder(db, user)
    ev.record(db, ev.ADMIN, "export", f"{user.display_name} exported {name}", user, "export", name, {"rows": len(data)}, publish=False)
    db.commit()
    return Response(
        content=to_csv(header, data),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="walnut-{name}.csv"'},
    )
