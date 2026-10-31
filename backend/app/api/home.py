"""Home: the three workstreams (people, calendar, tasks) in one payload."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CalendarEvent, Contact, Message, Task, User
from app.security.auth import require
from app.services import crm, timeutil

router = APIRouter(prefix="/api", tags=["home"])


def _unread_flag():
    return exists().where(
        Message.contact_id == Contact.id, Message.direction == "in",
        or_(Contact.last_read_at.is_(None), Message.created_at > Contact.last_read_at),
    )


@router.get("/home")
def home(mine: bool = False, user: User = Depends(require("home.view")), db: Session = Depends(get_db)):
    now = timeutil.now_utc()
    today = timeutil.today_local(now)
    day_start = crm.local_day_start(today)
    week_end = crm.local_day_start(today + timedelta(days=7))
    unread = _unread_flag()

    people_stmt = select(Contact).where(Contact.erased_at.is_(None))
    if mine:
        people_stmt = people_stmt.where(Contact.owner_user_id == user.id)
    people = db.scalars(people_stmt.order_by(case((unread, 0), else_=1), Contact.last_active_at.desc(), Contact.id.desc()).limit(20)).all()
    unread_people = db.scalar(select(func.count()).select_from(people_stmt.where(unread).subquery())) or 0

    ev_stmt = select(CalendarEvent).where(CalendarEvent.starts_at >= day_start, CalendarEvent.starts_at < week_end,
                                          CalendarEvent.status != "cancelled")
    if mine:
        ev_stmt = ev_stmt.where(CalendarEvent.staff_user_id == user.id)
    events = db.scalars(ev_stmt.order_by(CalendarEvent.starts_at, CalendarEvent.id).limit(100)).all()
    tomorrow_start = crm.local_day_start(today + timedelta(days=1))
    events_today = sum(1 for e in events if day_start <= e.starts_at < tomorrow_start)

    task_stmt = select(Task).where(Task.status.in_(crm.ACTIVE_TASK))
    if mine:
        task_stmt = task_stmt.where(Task.assigned_to == user.id)
    tasks = db.scalars(task_stmt.order_by(*crm.task_order()).limit(20)).all()
    open_tasks = db.scalar(select(func.count()).select_from(task_stmt.subquery())) or 0
    my_tasks = db.scalar(select(func.count()).select_from(Task).where(Task.status.in_(crm.ACTIVE_TASK), Task.assigned_to == user.id)) or 0

    return {
        "people": crm.contact_rows(db, people),
        "events": crm.event_dicts(db, events),
        "tasks": crm.task_dicts(db, tasks),
        "counts": {"unread_people": unread_people, "events_today": events_today, "open_tasks": open_tasks, "my_tasks": my_tasks},
    }
