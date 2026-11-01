"""Creative studio API (Operations > Creative studio) and the public share page a campaign text links to."""
from __future__ import annotations

import json
import os
from html import escape
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import Strict
from app.db import get_db
from app.models import Creative, User
from app.security import crypto
from app.security.auth import require
from app.security.permissions import has_perm
from app.services import creatives as C
from app.services import events as ev
from app.services import settings_store, timeutil

router = APIRouter(prefix="/api/creatives", tags=["creatives"])
public = APIRouter(tags=["creatives"])


def view(c: Creative, full: bool = False) -> dict[str, Any]:
    q = json.loads(c.question_json or "{}")
    out = {"id": c.id, "kind": c.kind, "format": c.format, "title": c.title, "status": c.status, "thinking": bool(q.get("thinking")),
           "question": None if q.get("thinking") else (q if q.get("question") else None), "answered": len(json.loads(c.qa_json or "[]")),
           "options": json.loads(c.options_json or "{}"), "estimate_usd": C.estimate_usd(c.kind, json.loads(c.options_json or "{}"), bool(c.reference_url)),
           "cost_usd": round((c.cost_usd or 0) + (c.llm_cost_usd or 0), 3), "error": c.error, "has_reference": bool(c.reference_url),
           "share_url": C.share_url(c), "media_url": f"/c/{c.public_token}/media" if c.public_token and c.file_name else None,
           "caption": c.caption, "sms_text": c.sms_text, "created_at": timeutil.iso(c.created_at), "done_at": timeutil.iso(c.done_at) if c.done_at else None}
    if full:
        out.update(prompt=c.prompt, plan=json.loads(c.plan_json or "{}"), qa=json.loads(c.qa_json or "[]"))
    return out


def _get(db: Session, cid: int) -> Creative:
    c = db.get(Creative, cid)
    if c is None:
        raise HTTPException(status_code=404, detail="No such creative")
    return c


@router.get("")
def overview(user: User = Depends(require("creatives.view")), db: Session = Depends(get_db)):
    rows = db.scalars(select(Creative).order_by(Creative.id.desc()).limit(120)).all()
    fmt = lambda t: [{"key": k, "label": v["label"], "blurb": v["blurb"]} for k, v in t.items()]  # noqa: E731
    return {"items": [view(c) for c in rows], "formats": {"image": fmt(C.IMAGE_FORMATS), "video": fmt(C.VIDEO_FORMATS)}, "settings": C.public_cfg(db),
            "can_draft": has_perm(user, "creatives.draft"), "can_make": has_perm(user, "creatives.make"), "can_settings": has_perm(user, "admin.settings")}


@router.get("/{cid}")
def one(cid: int, user: User = Depends(require("creatives.view")), db: Session = Depends(get_db)):
    return {"item": view(_get(db, cid), full=True)}


class StartIn(Strict):
    kind: Literal["image", "video"]
    format: str = Field(min_length=2, max_length=40)


@router.post("", status_code=201)
def start(body: StartIn, user: User = Depends(require("creatives.draft")), db: Session = Depends(get_db)):
    if body.format not in (C.VIDEO_FORMATS if body.kind == "video" else C.IMAGE_FORMATS):
        raise HTTPException(status_code=400, detail="Pick one of the formats.")
    f = (C.VIDEO_FORMATS if body.kind == "video" else C.IMAGE_FORMATS)[body.format]
    c = Creative(kind=body.kind, format=body.format, title=f["label"], status="interview", options_json=json.dumps(C.default_options(body.kind)),
                 created_by=user.id, created_at=timeutil.now_utc(), question_json=json.dumps({"thinking": True}), messages_json="[]")
    db.add(c)
    db.commit()
    ev.record(db, ev.CAMPAIGNS, "creative_started", f"{user.display_name} started a {body.kind} in Creative studio: {f['label']}", user, "creative", c.id, publish=False)
    db.commit()
    C.in_background(_first_question, c.id, body.kind, body.format)
    db.expire_all()  # the step may already have finished (tests run it inline)
    return {"item": view(db.get(Creative, c.id))}


def _first_question(db: Session, cid: int, kind: str, fmt: str) -> None:
    f = (C.VIDEO_FORMATS if kind == "video" else C.IMAGE_FORMATS)[fmt]
    system = C.INTERVIEW_PROMPT.format(kind=kind, format_label=f["label"], format_blurb=f["blurb"], max_q=C.MAX_QUESTIONS, safety=C.SAFETY, facts=C.clinic_facts(db))
    c = db.get(Creative, cid)
    c.messages_json = json.dumps([{"role": "system", "content": system}])
    db.commit()
    C.next_turn(db, cid, None)


class AnswerIn(Strict):
    answer: str = Field(min_length=1, max_length=1000)


@router.post("/{cid}/answer")
def answer(cid: int, body: AnswerIn, user: User = Depends(require("creatives.draft")), db: Session = Depends(get_db)):
    c = _get(db, cid)
    if c.status != "interview" or json.loads(c.question_json or "{}").get("thinking"):
        raise HTTPException(status_code=409, detail="That question was already answered.")
    c.error = ""
    q = json.loads(c.question_json or "{}")
    c.question_json = json.dumps({**q, "thinking": True})
    db.commit()
    C.in_background(_answer, cid, body.answer.strip())
    db.expire_all()
    return {"item": view(db.get(Creative, cid))}


def _answer(db: Session, cid: int, text: str) -> None:
    c = db.get(Creative, cid)
    q = json.loads(c.question_json or "{}")
    q.pop("thinking", None)
    c.question_json = json.dumps(q)
    db.commit()
    C.next_turn(db, cid, text)


@router.post("/{cid}/finish")
def finish(cid: int, user: User = Depends(require("creatives.draft")), db: Session = Depends(get_db)):
    """Skip the remaining questions and write it now (or write it again after edits to the answers)."""
    c = _get(db, cid)
    if c.status not in ("interview", "review", "failed"):
        raise HTTPException(status_code=409, detail="It is being made already.")
    c.question_json, c.error = json.dumps({"thinking": True}), ""
    db.commit()
    C.in_background(_write, cid)
    db.expire_all()
    return {"item": view(db.get(Creative, cid))}


def _write(db: Session, cid: int) -> None:
    c = db.get(Creative, cid)
    c.question_json = "{}"
    db.commit()
    C.write(db, cid)


class EditIn(Strict):
    title: str | None = Field(default=None, max_length=160)
    prompt: str | None = Field(default=None, min_length=20, max_length=6000)
    caption: str | None = Field(default=None, max_length=1200)
    sms_text: str | None = Field(default=None, max_length=300)
    options: dict[str, Any] | None = None


@router.put("/{cid}")
def edit(cid: int, body: EditIn, user: User = Depends(require("creatives.draft")), db: Session = Depends(get_db)):
    c = _get(db, cid)
    ch = body.model_dump(exclude_unset=True)
    for k in ("title", "prompt", "caption", "sms_text"):
        if ch.get(k) is not None:
            setattr(c, k, C._clean(ch[k]))
    if body.options is not None:
        o = body.options
        if c.kind == "video":
            if o.get("resolution") not in ("480p", "720p", "1080p") or int(o.get("duration", 0)) not in (5, 8, 10, 12, 15):
                raise HTTPException(status_code=400, detail="Pick a size (480p, 720p or 1080p) and a length (5 to 15 seconds).")
            opts = {"resolution": o["resolution"], "duration": int(o["duration"])}
        else:
            if o.get("quality") not in ("low", "medium", "high") or o.get("aspect_ratio") not in ("1:1", "4:5", "9:16", "16:9"):
                raise HTTPException(status_code=400, detail="Pick a quality and a shape.")
            opts = {"quality": o["quality"], "aspect_ratio": o["aspect_ratio"]}
        c.options_json = json.dumps(opts)
    db.commit()
    return {"item": view(c, full=True)}


@router.post("/{cid}/reference")
async def reference(cid: int, file: UploadFile = File(...), user: User = Depends(require("creatives.draft")), db: Session = Depends(get_db)):
    _get(db, cid)
    kind = (file.content_type or "").lower()
    if kind not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=415, detail="Use a JPEG, PNG or WebP photo.")
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Keep the photo under 10 MB.")
    try:
        C.upload_reference(db, cid, data, file.filename or "photo.jpg", kind)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"item": view(db.get(Creative, cid), full=True)}


@router.post("/{cid}/generate")
def generate(cid: int, user: User = Depends(require("creatives.make")), db: Session = Depends(get_db)):
    try:
        c = C.generate(db, cid)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    ev.record(db, ev.CAMPAIGNS, "creative_generating", f"{user.display_name} is making the {c.kind} \"{c.title}\" (about ${c.cost_usd:.2f})", user, "creative", c.id)
    db.commit()
    if c.status == "failed":
        raise HTTPException(status_code=502, detail=c.error or "The generator refused it.")
    return {"item": view(c, full=True)}


@router.delete("/{cid}")
def delete(cid: int, user: User = Depends(require("creatives.make")), db: Session = Depends(get_db)):
    c = _get(db, cid)
    if c.file_name:
        try:
            os.unlink(os.path.join(C.storage_dir(), os.path.basename(c.file_name)))
        except OSError:
            pass
    from app.models import Campaign
    for camp in db.scalars(select(Campaign).where(Campaign.creative_id == c.id)).all():
        camp.creative_id = None
    ev.record(db, ev.CAMPAIGNS, "creative_deleted", f"{user.display_name} deleted the creative \"{c.title}\"", user, "creative", c.id)
    db.delete(c)
    db.commit()
    return {"ok": True}


class SettingsIn(Strict):
    wavespeed_key: str | None = Field(default=None, min_length=10, max_length=300)
    clear_key: bool = False
    monthly_budget_usd: float | None = Field(default=None, ge=0, le=5000)


@router.put("/settings/studio")
def settings(body: SettingsIn, user: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    cfg = C.get_cfg(db)
    if body.wavespeed_key:
        k = body.wavespeed_key.strip()
        if " " in k or not k.isprintable():
            raise HTTPException(status_code=400, detail="That key has spaces or hidden characters. Paste it again.")
        cfg["wavespeed_key_enc"], cfg["wavespeed_last4"] = crypto.encrypt(k), k[-4:]
    if body.clear_key:
        cfg["wavespeed_key_enc"], cfg["wavespeed_last4"] = "", None
    if body.monthly_budget_usd is not None:
        cfg["monthly_budget_usd"] = round(float(body.monthly_budget_usd), 2)
    settings_store.put_raw(db, C.KEY, cfg, by=user.display_name)
    ev.record(db, ev.ADMIN, "creative_settings", f"{user.display_name} changed the Creative studio settings", user, "setting", C.KEY)
    db.commit()
    return {"settings": C.public_cfg(db)}


# ---------------------------------------------------------------- the share page a campaign text links to (public, by secret link only)
def _shared(db: Session, token: str) -> Creative:
    c = db.scalar(select(Creative).where(Creative.public_token == token, Creative.status == "done")) if len(token) >= 16 else None
    if c is None or not c.file_name:
        raise HTTPException(status_code=404, detail="Not found")
    return c


@public.get("/c/{token}/media", include_in_schema=False)
def shared_media(token: str, db: Session = Depends(get_db)):
    c = _shared(db, token)
    path = os.path.join(C.storage_dir(), os.path.basename(c.file_name))
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Not found")
    kind = "video/mp4" if c.file_name.endswith(".mp4") else f"image/{'jpeg' if c.file_name.endswith(('.jpg', '.jpeg')) else c.file_name.rsplit('.', 1)[-1]}"
    return FileResponse(path, media_type=kind, headers={"Cache-Control": "public, max-age=86400"})


@public.get("/c/{token}", include_in_schema=False, response_class=HTMLResponse)
def shared_page(token: str, db: Session = Depends(get_db)):
    c = _shared(db, token)
    name = escape(str(settings_store.get_raw(db, "clinic_name", None) or "Walnut Grove Animal Hospital"))
    media = (f'<video src="/c/{token}/media" controls playsinline autoplay muted loop></video>' if c.kind == "video"
             else f'<img src="/c/{token}/media" alt="{escape(c.title)}">')
    site = "https://www.walnutgroveanimalclinic.com"
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(c.title)} | {name}</title><meta property="og:title" content="{escape(c.title)}"><meta property="og:description" content="{escape(c.caption[:200])}">
<style>body{{margin:0;font-family:system-ui,sans-serif;background:#f6f3ec;color:#1d2a2a}}main{{max-width:520px;margin:0 auto;padding:20px 16px}}
img,video{{width:100%;border-radius:14px;display:block;background:#000}}h1{{font-size:20px;margin:16px 0 6px}}p{{line-height:1.5}}
a.b{{display:inline-block;margin-top:10px;padding:12px 18px;border-radius:10px;background:#1c7c6e;color:#fff;text-decoration:none;font-weight:600}}</style></head>
<body><main>{media}<h1>{name}</h1><p>{escape(c.caption)}</p><a class="b" href="{site}">Visit our website</a></main></body></html>""",
                        headers={"Cache-Control": "public, max-age=300"})
