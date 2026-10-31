from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy.orm import Session

from app.api.schemas import Strict
from app.assistant.handlers import HANDLERS, ans, guess_all
from app.db import get_db
from app.models import User
from app.security.auth import require
from app.seed import reference_data as ref

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


def chips_for(user: User) -> list[list[str]]:
    return ref.CHIPS.get(user.role, [])


@router.get("/chips")
def chips(user: User = Depends(require("assistant.use")), db: Session = Depends(get_db)):
    g = ref.GREETINGS[user.role].format(name=user.display_name)
    from app.assistant import staff_agent
    on = staff_agent.enabled(db)
    return {"greeting": g, "chips": [{"key": k, "question": q} for k, q in chips_for(user)], "ai": on}


class AskBody(Strict):
    key: str | None = Field(default=None, max_length=40)
    text: str | None = Field(default=None, max_length=300)


@router.post("/ask")
def ask(body: AskBody, user: User = Depends(require("assistant.use")), db: Session = Depends(get_db)):
    allowed = {k for k, _ in chips_for(user)}
    key = body.key
    if key is not None:
        if key not in allowed or key not in HANDLERS:
            raise HTTPException(status_code=403, detail="forbidden")
    elif body.text and body.text.strip():
        key = next((g for g in guess_all(body.text) if g in allowed), None)
    else:
        raise HTTPException(status_code=400, detail="Ask a question or tap one of the questions")
    if key is None:
        qs = "; ".join(q for _, q in chips_for(user))
        return {"answer": ans([f"I can answer questions about: {qs}"], source="fallback")}
    answer = HANDLERS[key](db, user)
    db.commit()
    return {"answer": answer}


# ---------------------------------------------------------------- AI chat over the clinic's data (when "AI for staff" is on)
class Turn(Strict):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class ChatBody(Strict):
    messages: list[Turn] = Field(min_length=1, max_length=24)


@router.post("/chat")
def chat(body: ChatBody, user: User = Depends(require("assistant.use")), db: Session = Depends(get_db)):
    from app.assistant import staff_agent
    from app.services import events as ev
    if not staff_agent.enabled(db):
        raise HTTPException(status_code=409, detail="AI for staff is off. An admin turns it on in Admin, AI assistant.")
    try:
        out = staff_agent.run(db, user, [m.model_dump() for m in body.messages])
    except staff_agent.AgentFailed as e:
        raise HTTPException(status_code=502, detail=f"The AI could not answer just now ({e}). The question buttons still work.")
    ev.record(db, ev.ADMIN, "staff_assistant_used", f"{user.display_name} asked the staff assistant", user, "setting", "staff_assistant",
              {"tools": out["tools"], "proposals": len(out["proposals"])}, publish=False)
    db.commit()
    return out


@router.post("/proposals/{pid}/confirm")
def confirm_proposal(pid: str, user: User = Depends(require("assistant.use")), db: Session = Depends(get_db)):
    from app.assistant import staff_agent
    return {"done": staff_agent.confirm(db, user, pid)}


@router.post("/proposals/{pid}/cancel")
def cancel_proposal(pid: str, user: User = Depends(require("assistant.use"))):
    from app.assistant import staff_agent
    staff_agent.cancel(user, pid)
    return {"ok": True}
