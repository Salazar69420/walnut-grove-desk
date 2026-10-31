"""Tasks: list, create, update, take."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.schemas import Strict
from app.db import get_db
from app.models import Contact, Task, User
from app.security.auth import require
from app.services import crm
from app.services import events as ev
from app.services import timeutil

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

TaskKind = Literal["unknown_knowledge", "escalation", "callback", "refill", "booking", "complaint", "emergency_followup", "results", "other"]
Status = Literal["open", "in_progress", "done"]
Priority = Literal["high", "normal", "low"]
Classification = Literal["inquiry", "patient"]


class TaskIn(Strict):
    kind: TaskKind
    classification: Classification
    title: str = Field(min_length=1, max_length=200)
    reason: str = Field(default="", max_length=2000)
    contact_id: int | None = None
    assigned_to: int | None = None
    priority: Priority = "normal"
    due_at: datetime | None = None


class TaskPatch(Strict):
    status: Status | None = None
    assigned_to: int | None = None
    priority: Priority | None = None
    due_at: datetime | None = None
    expected_status: Status | None = None


def _get(db: Session, task_id: int) -> Task:
    t = db.get(Task, task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="No such task")
    return t


def _active_user(db: Session, user_id: int) -> User:
    u = db.get(User, user_id)
    if u is None or not u.active:
        raise HTTPException(status_code=400, detail="That person cannot be given the task.")
    return u


def _announce_assignment(db: Session, actor: User, assignee: User, task: Task) -> None:
    if assignee.id != actor.id:
        crm.notify(db, assignee.id, "task_assigned", f"{actor.display_name} gave you a task: {task.title}", f"/tasks/{task.id}")


@router.get("")
def list_tasks(
    status: Literal["open", "in_progress", "done", "active", "all"] = "active",
    classification: Classification | None = None,
    kind: TaskKind | None = None,
    assigned: str = Query(default="all", max_length=20),
    contact_id: int | None = None,
    q: str | None = Query(default=None, max_length=80),
    user: User = Depends(require("tasks.view")),
    db: Session = Depends(get_db),
):
    stmt = select(Task)
    if status == "active":
        stmt = stmt.where(Task.status.in_(crm.ACTIVE_TASK))
    elif status != "all":
        stmt = stmt.where(Task.status == status)
    if classification:
        stmt = stmt.where(Task.classification == classification)
    if kind:
        stmt = stmt.where(Task.kind == kind)
    if assigned == "me":
        stmt = stmt.where(Task.assigned_to == user.id)
    elif assigned == "none":
        stmt = stmt.where(Task.assigned_to.is_(None))
    elif assigned.isdigit():
        stmt = stmt.where(Task.assigned_to == int(assigned))
    elif assigned != "all":
        raise HTTPException(status_code=400, detail="Assigned must be me, none, all or a person's number.")
    if contact_id is not None:
        stmt = stmt.where(Task.contact_id == contact_id)
    if q and q.strip():
        like = crm.like_pattern(q.strip())
        stmt = stmt.outerjoin(Contact, Contact.id == Task.contact_id).where(
            or_(Task.title.like(like, escape="\\"), Task.reason.like(like, escape="\\"), Contact.display_name.like(like, escape="\\")))
    order = crm.task_order()
    if status == "done":
        order = (Task.done_at.desc(), Task.id.desc())
    rows = db.scalars(stmt.order_by(*order).limit(500)).all()
    return {"items": crm.task_dicts(db, rows), "counts": crm.person_counts(db, user)}


@router.post("", status_code=201)
def create_task(body: TaskIn, user: User = Depends(require("tasks.work")), db: Session = Depends(get_db)):
    contact = None
    if body.contact_id is not None:
        contact = db.get(Contact, body.contact_id)
        if contact is None or contact.erased_at is not None:
            raise HTTPException(status_code=400, detail="That contact was not found.")
    assignee = _active_user(db, body.assigned_to) if body.assigned_to is not None else None
    t = Task(
        kind=body.kind, classification=body.classification, title=body.title, reason=body.reason, contact_id=body.contact_id,
        assigned_to=assignee.id if assignee else None, priority=body.priority, status="open",
        due_at=crm.aware(body.due_at), raised_by="human", created_at=timeutil.now_utc(),
    )
    db.add(t)
    db.flush()
    if assignee:
        _announce_assignment(db, user, assignee, t)
    ev.record(db, ev.TASKS, "task_created", f"{user.display_name} created a task", user, "task", t.id, {"kind": t.kind, "priority": t.priority})
    db.commit()
    return {"item": crm.task_dicts(db, [t])[0]}


@router.patch("/{task_id}")
def patch_task(task_id: int, body: TaskPatch, background: BackgroundTasks, user: User = Depends(require("tasks.work")), db: Session = Depends(get_db)):
    t = _get(db, task_id)
    if body.expected_status is not None and body.expected_status != t.status:
        raise HTTPException(status_code=409, detail="Someone else just changed this task. Reload to see the latest.")
    ch = body.model_dump(exclude_unset=True)
    ch.pop("expected_status", None)
    if not ch:
        return {"item": crm.task_dicts(db, [t])[0]}
    if "status" in ch and ch["status"] is None:
        raise HTTPException(status_code=400, detail="Status cannot be empty.")
    if "priority" in ch and ch["priority"] is None:
        raise HTTPException(status_code=400, detail="Priority cannot be empty.")
    now = timeutil.now_utc()
    notes: list[str] = []
    if "status" in ch and ch["status"] != t.status:
        new = ch["status"]
        if new == "done":
            t.done_at, t.done_by = now, user.id
            notes.append(f"{user.display_name} marked a task done")
        else:
            notes.append(f"{user.display_name} {'started' if new == 'in_progress' else 'reopened'} a task")
            t.done_at = t.done_by = None
        t.status = new
    if "assigned_to" in ch and ch["assigned_to"] != t.assigned_to:
        if ch["assigned_to"] is None:
            t.assigned_to = None
            notes.append(f"{user.display_name} unassigned a task")
        else:
            assignee = _active_user(db, ch["assigned_to"])
            t.assigned_to = assignee.id
            _announce_assignment(db, user, assignee, t)
            notes.append(f"{user.display_name} assigned a task")
    if "priority" in ch and ch["priority"] != t.priority:
        t.priority = ch["priority"]
        notes.append(f"{user.display_name} changed a task's priority")
    if "due_at" in ch:
        t.due_at = crm.aware(ch["due_at"])
        notes.append(f"{user.display_name} changed a task's due time")
    db.flush()
    text = notes[0] if len(notes) == 1 else f"{user.display_name} updated a task"
    ev.record(db, ev.TASKS, "task_updated", text, user, "task", t.id, {"status": t.status, "priority": t.priority})
    db.commit()
    out = {"item": crm.task_dicts(db, [t])[0]}
    if t.status == "done" and t.raised_by == "ai":
        from app.assistant import learn
        db.commit()  # end this session's read so the learning step can write
        background.add_task(learn.after_done, t.id, user.id)  # may suggest knowledge from how the person handled it
    return out


@router.post("/{task_id}/take")
def take_task(task_id: int, user: User = Depends(require("tasks.work")), db: Session = Depends(get_db)):
    t = _get(db, task_id)
    if t.status == "done":
        raise HTTPException(status_code=409, detail="This task is already done.")
    if t.assigned_to is not None and t.assigned_to != user.id:
        other = db.get(User, t.assigned_to)
        raise HTTPException(status_code=409, detail=f"Already taken by {other.display_name if other else 'someone else'}")
    if t.assigned_to == user.id and t.status == "in_progress":
        return {"item": crm.task_dicts(db, [t])[0]}
    t.assigned_to = user.id
    t.status = "in_progress"
    ev.record(db, ev.TASKS, "task_taken", f"{user.display_name} took a task", user, "task", t.id, {})
    db.commit()
    return {"item": crm.task_dicts(db, [t])[0]}
