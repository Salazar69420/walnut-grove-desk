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
def chips(user: User = Depends(require("assistant.use"))):
    g = ref.GREETINGS[user.role].format(name=user.display_name)
    return {"greeting": g, "chips": [{"key": k, "question": q} for k, q in chips_for(user)]}


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
