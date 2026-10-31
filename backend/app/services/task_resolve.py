"""When a person on the team reaches a client (text, email, an answered call either way, a voicemail left for them), the open
tasks for that client that were raised before it are resolved: the client has been dealt with. If they get back in touch, the
assistant raises a new task as usual. Booking tasks stay open (approving the appointment closes them).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Task, User
from app.services import crm, timeutil
from app.services import events as ev

RESOLVED_BY_CONTACT = ("callback", "unknown_knowledge", "escalation", "emergency_followup", "results", "refill", "complaint", "other")


def resolve_for_contact(db: Session, contact_id: int | None, how: str, at: datetime, user: User | None = None) -> list[int]:
    if not contact_id:
        return []
    at = crm.aware(at)
    done: list[int] = []
    for t in db.scalars(select(Task).where(Task.contact_id == contact_id, Task.status != "done", Task.kind.in_(RESOLVED_BY_CONTACT))).all():
        if crm.aware(t.created_at) > at:
            continue  # raised after this contact: still to do
        t.status, t.done_at, t.done_by = "done", timeutil.now_utc(), user.id if user else None
        t.reason = (t.reason + f"\nResolved automatically: {how} ({timeutil.local(at).strftime('%b %d, %I:%M %p').replace(' 0', ' ')}).")[:2000]
        ev.record(db, ev.TASKS, "task_resolved_by_contact", f"Task resolved: {how}", user or "Walnut Grove Desk", "task", t.id, {"status": "done"})
        done.append(t.id)
    learn = [(i, user.id) for i in done if user is not None and db.get(Task, i).raised_by == "ai"]
    if learn:
        def later():
            time.sleep(4)  # after the request has committed the staff reply
            from app.assistant import learn as L
            for tid, uid in learn:
                L.after_done(tid, uid)
        threading.Thread(target=later, daemon=True).start()
    return done
