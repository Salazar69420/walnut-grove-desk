"""'Try it as a client': the same engine the real channels use, with sessions stored in sim_sessions."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy.orm import Session

from app.aftercare import engine as E
from app.aftercare import service as svc
from app.api.schemas import Strict
from app.db import get_db
from app.models import SimSession, User
from app.security.auth import require
from app.services import timeutil

router = APIRouter(prefix="/api/sim", tags=["sim"])


class StartBody(Strict):
    channel: Literal["text", "call"]


class ReplyBody(Strict):
    option_index: int | None = Field(default=None, ge=0, le=50)
    text: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _one(self):
        if (self.option_index is None) == (self.text is None):
            raise ValueError("send either option_index or text")
        return self


def _state(row: SimSession) -> E.State:
    blob = json.loads(row.picks_json or "{}")
    return E.State(channel=row.channel, node=row.node, picks=blob.get("picks", {}), messages=json.loads(row.messages_json or "[]"),
                   done=row.done, outcome=blob.get("outcome"))


def _save(row: SimSession, st: E.State) -> None:
    row.node = st.node
    row.picks_json = json.dumps({"picks": st.picks, "outcome": st.outcome})
    row.messages_json = json.dumps(st.messages)
    row.done = st.done
    row.updated_at = timeutil.now_utc()


def _payload(row: SimSession, st: E.State, ctx: E.Ctx) -> dict:
    v = E.view(st, ctx)
    return {"id": row.id, "channel": st.channel, "node": st.node, "messages": st.messages, "options": v["options"],
            "done": st.done, "accepts_text": v["accepts_text"]}


@router.post("/start")
def start(body: StartBody, user: User = Depends(require("sim.use")), db: Session = Depends(get_db)):
    ctx = svc.build_ctx(db)
    st = E.start(body.channel, ctx)
    row = SimSession(user_id=user.id, channel=body.channel, created_at=timeutil.now_utc(), updated_at=timeutil.now_utc())
    _save(row, st)
    db.add(row)
    db.commit()
    return {"session": _payload(row, st, ctx)}


@router.post("/{sid}/reply")
def reply(sid: int, body: ReplyBody, user: User = Depends(require("sim.use")), db: Session = Depends(get_db)):
    row = db.get(SimSession, sid)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="No such conversation")
    ctx = svc.build_ctx(db)
    st = _state(row)
    if st.done:
        raise HTTPException(status_code=409, detail="This conversation is finished. Start a new one.")
    inp = {"option_index": body.option_index} if body.option_index is not None else {"text": body.text}
    try:
        st, _bot, outcome = E.step(st, inp, ctx)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e).capitalize())
    _save(row, st)
    out = None
    if outcome is not None:
        conv = svc.persist_outcome(db, outcome, st.messages, channel=row.channel, source="sim", is_test=True,
                                   actor=user, client_label="You (test)", origin="a test client", created_by=user.id)
        row.conversation_id = conv.id
        out = {"kind": conv.kind, "conversation_id": conv.id}
    db.commit()
    resp = {"session": _payload(row, st, ctx)}
    if out:
        resp["outcome"] = out
    return resp
