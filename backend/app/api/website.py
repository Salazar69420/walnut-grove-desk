"""Website: the clinic site's content (read from the live site) and the SEO agent's audits."""
from __future__ import annotations

import json
from typing import Any

import httpx

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import Strict
from app.db import get_db
from app.models import User, WebsiteAudit
from app.security.auth import require
from app.security.permissions import has_perm
from app.services import settings_store, timeutil, website
from app.services import events as ev

router = APIRouter(prefix="/api/website", tags=["website"])


def audit_view(a: WebsiteAudit | None, full: bool = False) -> dict[str, Any] | None:
    if a is None:
        return None
    out: dict[str, Any] = {"id": a.id, "status": a.status, "base_url": a.base_url, "started_at": timeutil.iso(a.started_at),
                           "finished_at": timeutil.iso(a.finished_at) if a.finished_at else None, "started_by": a.started_by,
                           "summary": json.loads(a.summary_json or "{}"), "error": a.error, "has_plan": bool(a.ai_plan)}
    if full:
        pages = json.loads(a.pages_json or "[]")
        out["issues"] = json.loads(a.issues_json or "[]")
        out["pages"] = [{k: p.get(k) for k in ("url", "status", "ms", "bytes", "title", "description", "h1", "words", "schema", "canonical", "imgs_no_alt")} for p in pages]
        out["ai_plan"] = a.ai_plan
    return out


@router.get("")
def overview(user: User = Depends(require("website.view")), db: Session = Depends(get_db)):
    latest = db.scalar(select(WebsiteAudit).order_by(WebsiteAudit.id.desc()).limit(1))
    done = db.scalar(select(WebsiteAudit).where(WebsiteAudit.status == "done").order_by(WebsiteAudit.id.desc()).limit(1))
    history = db.scalars(select(WebsiteAudit).where(WebsiteAudit.status == "done").order_by(WebsiteAudit.id.desc()).limit(10)).all()
    return {"settings": website.get_settings(db), "running": latest is not None and latest.status == "running", "latest": audit_view(done, full=True),
            "history": [{"id": h.id, "finished_at": timeutil.iso(h.finished_at), "score": json.loads(h.summary_json or "{}").get("score")} for h in history],
            "can_run": has_perm(user, "website.run")}


@router.get("/pages")
def page_content(url: str, user: User = Depends(require("website.view")), db: Session = Depends(get_db)):
    done = db.scalar(select(WebsiteAudit).where(WebsiteAudit.status == "done").order_by(WebsiteAudit.id.desc()).limit(1))
    if done is None:
        raise HTTPException(status_code=404, detail="Run the first audit to read the site.")
    page = next((p for p in json.loads(done.pages_json) if p["url"] == url), None)
    if page is None:
        raise HTTPException(status_code=404, detail="That page was not found in the last audit.")
    return {"item": page}


@router.post("/audit", status_code=202)
def run_audit(user: User = Depends(require("website.run")), db: Session = Depends(get_db)):
    if db.scalar(select(WebsiteAudit.id).where(WebsiteAudit.status == "running").limit(1)):
        raise HTTPException(status_code=409, detail="An audit is already running.")
    a = website.start(db, user.display_name)
    return {"item": audit_view(a)}


@router.post("/audits/{aid}/plan")
def plan(aid: int, user: User = Depends(require("website.run")), db: Session = Depends(get_db)):
    from app.assistant import ai
    a = db.get(WebsiteAudit, aid)
    if a is None or a.status != "done":
        raise HTTPException(status_code=404, detail="No finished audit with that number.")
    try:
        text = website.ai_plan(db, a)
    except ai.AiFailed as e:
        raise HTTPException(status_code=502, detail=f"The AI could not write the plan just now ({e}).")
    a = db.get(WebsiteAudit, aid)
    a.ai_plan = text
    ev.record(db, ev.ADMIN, "website_plan", f"{user.display_name} asked the SEO agent for a fix plan", user, "setting", website.KEY)
    db.commit()
    return {"ai_plan": text}


@router.post("/audits/{aid}/knowledge")
def knowledge(aid: int, user: User = Depends(require("website.run")), db: Session = Depends(get_db)):
    from app.assistant import ai
    a = db.get(WebsiteAudit, aid)
    if a is None or a.status != "done":
        raise HTTPException(status_code=404, detail="No finished audit with that number.")
    try:
        n = website.ai_knowledge(db, a, user)
    except (ai.AiFailed, ValueError) as e:
        raise HTTPException(status_code=502, detail=f"The AI could not read the site just now ({type(e).__name__}).")
    return {"added": n}


@router.get("/studio")
def studio_status(user: User = Depends(require("website.run"))):
    """Is the Content studio (Contento, a separate app on this server) running?"""
    import httpx
    try:
        up = httpx.get("http://127.0.0.1:3100/login", timeout=3.0).status_code < 500
    except httpx.HTTPError:
        up = False
    return {"up": up, "url": "https://contento.77-42-69-194.sslip.io/admin/dashboard"}


class SettingsIn(Strict):
    url: str = Field(min_length=8, max_length=200, pattern=r"^https://[A-Za-z0-9.\-]+(/.*)?$")
    canonical: str = Field(default="", max_length=200, pattern=r"^(https://[A-Za-z0-9.\-]+/?)?$")


@router.put("/settings")
def put_settings(body: SettingsIn, user: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    cfg = website.get_settings(db)
    cfg.update(url=body.url.rstrip("/"), canonical=body.canonical.rstrip("/"))
    settings_store.put_raw(db, website.KEY, cfg, by=user.display_name)
    db.commit()
    return {"settings": cfg}


# ---------------------------------------------------------------- deployment agent
from app.models import WebsiteChange  # noqa: E402
from app.security import crypto  # noqa: E402
from app.services import deploy_agent  # noqa: E402


def _after_merge(db: Session, before: str, c: WebsiteChange) -> None:
    """When a change is merged, Vercel deploys it; audit the live site again a few minutes later to show the difference."""
    if before != "merged" and c.status == "merged":
        import threading
        threading.Timer(180, lambda: _audit_now("SEO agent (after deploying a change)")).start()


def _audit_now(who: str) -> None:
    from app import db as app_db
    with app_db.get_sessionmaker()() as s:
        if not s.scalar(select(WebsiteAudit.id).where(WebsiteAudit.status == "running").limit(1)):
            website.start(s, who)


@router.get("/deploy")
def deploy_overview(user: User = Depends(require("website.view")), db: Session = Depends(get_db)):
    rows = db.scalars(select(WebsiteChange).order_by(WebsiteChange.id.desc()).limit(10)).all()
    for c in rows[:3]:
        if c.status in ("building", "ready"):
            before = c.status
            c2 = deploy_agent.refresh(db, c)
            _after_merge(db, before, c2)
    rows = db.scalars(select(WebsiteChange).order_by(WebsiteChange.id.desc()).limit(10)).all()
    return {"settings": deploy_agent.public(deploy_agent.get_cfg(db)), "changes": [deploy_agent.view(c) for c in rows], "can_run": has_perm(user, "website.run")}


@router.get("/deploy/{cid}")
def deploy_one(cid: int, user: User = Depends(require("website.view")), db: Session = Depends(get_db)):
    c = db.get(WebsiteChange, cid)
    if c is None:
        raise HTTPException(status_code=404, detail="No such change")
    before = c.status
    c = deploy_agent.refresh(db, c)
    _after_merge(db, before, c)
    return {"item": deploy_agent.view(c, full=True)}


class DeployIn(Strict):
    audit_id: int
    codes: list[str] = Field(default_factory=list, max_length=40)
    mode: str | None = Field(default=None, pattern="^(auto|approve)$")


@router.post("/deploy", status_code=202)
def deploy_start(body: DeployIn, user: User = Depends(require("website.run")), db: Session = Depends(get_db)):
    a = db.get(WebsiteAudit, body.audit_id)
    if a is None or a.status != "done":
        raise HTTPException(status_code=404, detail="No finished audit with that number.")
    if db.scalar(select(WebsiteChange.id).where(WebsiteChange.status == "planning").limit(1)):
        raise HTTPException(status_code=409, detail="The agent is already preparing a change.")
    try:
        c = deploy_agent.start(db, a, body.codes, user.display_name, body.mode)
    except ValueError as e:
        raise HTTPException(status_code=409, detail="Add a GitHub token for the website repo first (SEO agent, Deployment settings)." if str(e) == "no_token"
                            else "The OpenRouter key is not set (Admin, AI assistant).")
    ev.record(db, ev.ADMIN, "website_change_started", f"{user.display_name} asked the deployment agent to fix the website ({c.mode})", user, "setting", website.KEY)
    db.commit()
    return {"item": deploy_agent.view(c)}


@router.post("/deploy/{cid}/{action}")
def deploy_decide(cid: int, action: str, user: User = Depends(require("website.run")), db: Session = Depends(get_db)):
    c = db.get(WebsiteChange, cid)
    if c is None or action not in ("approve", "reject"):
        raise HTTPException(status_code=404, detail="No such change")
    if c.status not in ("building", "ready"):
        raise HTTPException(status_code=409, detail="This change is not waiting for a decision.")
    if action == "approve" and c.status != "ready":
        raise HTTPException(status_code=409, detail="Wait until the preview build has passed.")
    before = c.status
    try:
        c = deploy_agent.approve(db, c) if action == "approve" else deploy_agent.reject(db, c)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=f"GitHub refused ({e}).")
    _after_merge(db, before, c)
    ev.record(db, ev.ADMIN, f"website_change_{action}d", f"{user.display_name} {'deployed' if action == 'approve' else 'rejected'} a website change", user, "setting", website.KEY)
    db.commit()
    return {"item": deploy_agent.view(c)}


class DeploySettingsIn(Strict):
    token: str | None = Field(default=None, min_length=20, max_length=300)
    clear_token: bool = False
    mode: str | None = Field(default=None, pattern="^(auto|approve)$")
    repo: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    branch: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_./-]{1,80}$")


@router.put("/deploy/settings")
def deploy_settings(body: DeploySettingsIn, user: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    cfg = deploy_agent.get_cfg(db)
    if body.token:
        t = body.token.strip()
        if " " in t or not t.isprintable():
            raise HTTPException(status_code=400, detail="That token has spaces or hidden characters. Paste it again.")
        cfg["token_enc"], cfg["token_last4"] = crypto.encrypt(t), t[-4:]
    if body.clear_token:
        cfg["token_enc"], cfg["token_last4"] = "", None
    for k in ("mode", "repo", "branch"):
        v = getattr(body, k)
        if v:
            cfg[k] = v
    settings_store.put_raw(db, deploy_agent.KEY, cfg, by=user.display_name)
    ev.record(db, ev.ADMIN, "website_deploy_settings", f"{user.display_name} changed the deployment agent settings (mode {cfg['mode']})", user, "setting", deploy_agent.KEY)
    db.commit()
    return {"settings": deploy_agent.public(cfg)}


# ---------------------------------------------------------------- blog posts (through the site's CMS)
from fastapi import File, Form, UploadFile  # noqa: E402

from app.services import blog  # noqa: E402


@router.get("/blog")
def blog_list(user: User = Depends(require("website.view")), db: Session = Depends(get_db)):
    cfg = blog.get_cfg(db)
    out: dict[str, Any] = {"cms": blog.public(cfg), "posts": [], "error": None, "can_edit": has_perm(user, "website.run")}
    if blog.public(cfg)["ready"]:
        try:
            out["posts"] = blog.list_posts(db)
        except (blog.CmsError, httpx.HTTPError) as e:
            out["error"] = str(e) if isinstance(e, blog.CmsError) else "The CMS could not be reached."
    return out


@router.post("/blog", status_code=201)
async def blog_add(title: str = Form(..., min_length=5, max_length=120), excerpt: str = Form(..., min_length=10, max_length=260),
                   body: str = Form(..., min_length=50, max_length=30000), category: str = Form(...), author: str = Form("", max_length=80),
                   tags: str = Form("", max_length=300), image_alt: str = Form("", max_length=200), image: UploadFile | None = File(None),
                   user: User = Depends(require("website.run")), db: Session = Depends(get_db)):
    data, kind = None, ""
    if image is not None and image.filename:
        kind = (image.content_type or "").lower()
        if kind not in ("image/jpeg", "image/png", "image/webp"):
            raise HTTPException(status_code=415, detail="Use a JPEG, PNG or WebP image.")
        data = await image.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Keep the image under 8 MB.")
    try:
        post = blog.add_post(db, title=title.strip(), excerpt=excerpt.strip(), body=body, category=category, author=author.strip() or user.display_name,
                             tags=[t.strip() for t in tags.split(",") if t.strip()], image=data, image_type=kind, image_alt=image_alt.strip())
    except blog.CmsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="The CMS could not be reached.")
    ev.record(db, ev.ADMIN, "blog_post_added", f"{user.display_name} published a blog post: {post['title'][:80]}", user, "setting", blog.KEY)
    db.commit()
    return {"item": post, "url": website.get_settings(db)["url"].rstrip("/") + "/blog/" + post["slug"]}


@router.delete("/blog/{post_id}")
def blog_delete(post_id: str, user: User = Depends(require("website.run")), db: Session = Depends(get_db)):
    try:
        blog.delete_post(db, post_id)
    except blog.CmsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="The CMS could not be reached.")
    ev.record(db, ev.ADMIN, "blog_post_removed", f"{user.display_name} removed a blog post", user, "setting", blog.KEY)
    db.commit()
    return {"ok": True}


class DraftIn(Strict):
    topic: str = Field(min_length=4, max_length=300)


@router.post("/blog/draft")
def blog_draft(body: DraftIn, user: User = Depends(require("website.run")), db: Session = Depends(get_db)):
    try:
        return {"draft": blog.ai_draft(db, body.topic)}
    except blog.CmsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="The AI could not write a draft just now.")


class CmsIn(Strict):
    project_id: str | None = Field(default=None, pattern=r"^[a-z0-9]{4,40}$")
    dataset: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,40}$")
    token: str | None = Field(default=None, min_length=20, max_length=400)
    clear_token: bool = False


@router.put("/blog/settings")
def blog_settings(body: CmsIn, user: User = Depends(require("admin.settings")), db: Session = Depends(get_db)):
    cfg = blog.get_cfg(db)
    if body.project_id:
        cfg["project_id"] = body.project_id
    if body.dataset:
        cfg["dataset"] = body.dataset
    if body.token:
        t = body.token.strip()
        if " " in t or not t.isprintable():
            raise HTTPException(status_code=400, detail="That token has spaces or hidden characters. Paste it again.")
        cfg["token_enc"], cfg["token_last4"] = crypto.encrypt(t), t[-4:]
    if body.clear_token:
        cfg["token_enc"], cfg["token_last4"] = "", None
    settings_store.put_raw(db, blog.KEY, cfg, by=user.display_name)
    ev.record(db, ev.ADMIN, "website_cms_settings", f"{user.display_name} connected the website CMS", user, "setting", blog.KEY)
    db.commit()
    return {"cms": blog.public(cfg)}
