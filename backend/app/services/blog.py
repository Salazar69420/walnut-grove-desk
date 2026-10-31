"""Blog posts on the clinic website, through the site's CMS (Sanity): list, add and remove.

Needs the Sanity project id, the dataset and a token with Editor (write) access, entered by an owner and stored encrypted.
New posts are written the way the site's blog schema expects (title, slug, excerpt, main image with alt text, publishedAt,
author, category, read time, tags, content as portable text: paragraphs, ## and ### headings, "- " bullet lists). The AI
can draft a post from a topic, using only approved clinic knowledge for clinic facts; a person reads it before publishing.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.security import crypto
from app.services import settings_store, timeutil

KEY = "website_cms"
API_VERSION = "v2025-02-19"
TRANSPORT = None  # tests put a fake CMS here
CATEGORIES = ["Pet Health & Wellness", "Preventative Care", "Nutrition & Safety", "Surgical & Specialized Care", "Puppy & Kitten Care", "Clinic News & Community"]
DEFAULTS = {"project_id": "", "dataset": "production", "token_enc": "", "token_last4": None}


class CmsError(Exception):
    pass


def get_cfg(db: Session) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    stored = settings_store.get_raw(db, KEY, None)
    if isinstance(stored, dict):
        cfg.update({k: v for k, v in stored.items() if k in DEFAULTS})
    return cfg


def public(cfg: dict[str, Any]) -> dict[str, Any]:
    return {"project_id": cfg["project_id"], "dataset": cfg["dataset"], "has_token": bool(cfg["token_enc"]), "token_last4": cfg["token_last4"],
            "ready": bool(cfg["project_id"] and cfg["token_enc"]), "categories": CATEGORIES}


def _client(cfg: dict[str, Any]) -> tuple[httpx.Client, str]:
    if not (cfg["project_id"] and cfg["token_enc"]):
        raise CmsError("The website's CMS is not connected yet (Sanity project id and token).")
    base = f"https://{cfg['project_id']}.api.sanity.io/{API_VERSION}"
    return httpx.Client(timeout=30.0, transport=TRANSPORT, headers={"Authorization": f"Bearer {crypto.decrypt(cfg['token_enc'])}"}), base


def _check(r: httpx.Response) -> Any:
    if r.status_code in (401, 403):
        raise CmsError("The CMS token was refused. It needs Editor (write) access to this project.")
    if r.status_code >= 400:
        raise CmsError(f"The CMS answered {r.status_code}.")
    return r.json()


def list_posts(db: Session) -> list[dict[str, Any]]:
    cfg = get_cfg(db)
    db.commit()
    c, base = _client(cfg)
    q = '*[_type == "blog" && !(_id in path("drafts.**"))] | order(publishedAt desc) {_id, title, "slug": slug.current, excerpt, publishedAt, category, readTime}'
    with c:
        return _check(c.get(f"{base}/data/query/{cfg['dataset']}", params={"query": q})).get("result", [])


def _blocks(text: str) -> list[dict[str, Any]]:
    """Plain text with ## / ### headings and "- " bullets to Sanity portable text."""
    out: list[dict[str, Any]] = []
    for raw in re.split(r"\n\s*\n|\n(?=\s*(?:#{2,3} |- ))", text.strip()):
        line = raw.strip()
        if not line:
            continue
        style, item = "normal", None
        if line.startswith("### "):
            style, line = "h3", line[4:]
        elif line.startswith("## "):
            style, line = "h2", line[3:]
        elif line.startswith("- "):
            item, line = "bullet", line[2:]
        block = {"_type": "block", "_key": uuid.uuid4().hex[:12], "style": style, "markDefs": [],
                 "children": [{"_type": "span", "_key": uuid.uuid4().hex[:12], "text": re.sub(r"\s+", " ", line), "marks": []}]}
        if item:
            block.update(listItem=item, level=1)
        out.append(block)
    return out


def slugify(title: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", title.lower())).strip("-")[:90] or uuid.uuid4().hex[:8]


def add_post(db: Session, *, title: str, excerpt: str, body: str, category: str, author: str, tags: list[str],
             image: bytes | None, image_type: str, image_alt: str) -> dict[str, Any]:
    cfg = get_cfg(db)
    db.commit()
    c, base = _client(cfg)
    words = len(body.split())
    doc: dict[str, Any] = {"_id": uuid.uuid4().hex, "_type": "blog", "title": title, "slug": {"_type": "slug", "current": slugify(title)},
                           "excerpt": excerpt[:260], "publishedAt": timeutil.iso(timeutil.now_utc()), "category": category if category in CATEGORIES else CATEGORIES[0],
                           "readTime": f"{max(1, round(words / 220))} min read", "tags": tags[:10], "content": _blocks(body),
                           "author": {"name": author or "Walnut Grove Animal Hospital", "role": "Veterinarian & Pet Care Specialist"}}
    with c:
        if image:
            asset = _check(c.post(f"{base}/assets/images/{cfg['dataset']}", content=image, headers={"Content-Type": image_type}))
            doc["mainImage"] = {"_type": "image", "asset": {"_type": "reference", "_ref": asset["document"]["_id"]}, "alt": image_alt or title}
        _check(c.post(f"{base}/data/mutate/{cfg['dataset']}", json={"mutations": [{"create": doc}]}))
    return {"_id": doc["_id"], "slug": doc["slug"]["current"], "title": title}


def delete_post(db: Session, post_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", post_id):
        raise CmsError("Not a post id.")
    cfg = get_cfg(db)
    db.commit()
    c, base = _client(cfg)
    with c:
        _check(c.post(f"{base}/data/mutate/{cfg['dataset']}", json={"mutations": [{"delete": {"id": post_id}}, {"delete": {"id": f"drafts.{post_id}"}}]}))


DRAFT_SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}, "excerpt": {"type": "string"}, "body": {"type": "string"},
                                                   "category": {"type": "string", "enum": CATEGORIES}, "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 6}},
                "required": ["title", "excerpt", "body", "category", "tags"], "additionalProperties": False}
DRAFT_PROMPT = ("You write blog posts for Walnut Grove Animal Hospital, a veterinary clinic in Langley, BC. Warm, clear, practical, written for pet "
                "owners, 500 to 800 words, Canadian English. Use '## ' and '### ' headings, short paragraphs and '- ' bullet lists, and end with a short "
                "question-and-answer section. General pet-care information only: no doses, no diagnoses, and say to call the clinic for anything "
                "about their own pet. For clinic facts (services, hours, prices) use ONLY the CLINIC KNOWLEDGE given; leave them out otherwise. "
                "Title under 60 characters, excerpt under 200. Reply with JSON only.")


def ai_draft(db: Session, topic: str) -> dict[str, Any]:
    from app.assistant import ai
    from app.services.transcribe import _post
    cfg = ai.get_config(db)
    if not cfg.get("key_enc"):
        raise CmsError("The OpenRouter key is not set (Admin, AI assistant).")
    kb, _ = ai.knowledge_block(db)
    db.commit()
    content = _post(cfg, {"model": cfg["model"], "temperature": 0.6, "max_tokens": 3000,
                          "messages": [{"role": "system", "content": DRAFT_PROMPT}, {"role": "user", "content": f"{kb[:6000]}\n\nTOPIC: {topic}"}],
                          "response_format": {"type": "json_schema", "json_schema": {"name": "blog_post", "strict": True, "schema": DRAFT_SCHEMA}}}, timeout=180.0)
    m = re.search(r"\{.*\}", content, re.S)
    d = json.loads(m.group(0)) if m else {}
    text = re.sub(r"\s*[—–]\s*", ", ", str(d.get("body") or ""))
    if ai.FORBIDDEN.search(text):
        raise CmsError("The draft mentioned medicines or doses, so it was not used. Try another topic.")
    return {"title": str(d.get("title") or "")[:120], "excerpt": str(d.get("excerpt") or "")[:260], "body": text, "category": d.get("category") or CATEGORIES[0],
            "tags": [str(t)[:30] for t in (d.get("tags") or [])][:6]}
