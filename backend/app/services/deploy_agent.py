"""The deployment agent: turns SEO issues into code changes on the website repo, and ships them.

1. Reads the relevant files of the site's Next.js code through the GitHub API (layout, sitemap, robots, the SEO and schema
   helpers, the pages named in the issues).
2. The AI writes small, exact edits (find this text, replace it with that) or new files (like public/llms.txt). Every edit is
   checked: only inside app/, utils/, components/ or public/, the text to find must occur exactly once, sizes are capped.
3. The edits are committed to a new branch and a pull request is opened. Vercel builds a preview of it.
4. Approve mode: a person reads the change and clicks Approve and deploy. Auto mode: as soon as the preview build passes it is
   merged; Vercel then deploys the live site. A failed build is never merged.
The GitHub token is the owner's, limited to the website repo, stored encrypted; it is never shown again or logged.
"""
from __future__ import annotations

import base64
import difflib
import json
import logging
import re
import threading
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.models import WebsiteAudit, WebsiteChange
from app.security import crypto
from app.services import settings_store, timeutil

log = logging.getLogger("walnut.deploy_agent")
KEY = "website_deploy"
API = "https://api.github.com"
TRANSPORT = None  # tests put a fake GitHub here
DEFAULTS = {"repo": "Salazar69420/walnut-grove-salazar", "branch": "main", "mode": "approve", "token_enc": "", "token_last4": None}
PATH_OK = re.compile(r"^(app|utils|components|public)/[A-Za-z0-9_.\[\]()/@-]{1,180}$")
ALWAYS = ["app/layout.tsx", "app/sitemap.ts", "app/robots.ts", "utils/seoBuilder.js", "utils/schemaBuilder.js", "utils/constants.ts"]
MAX_FILE = 30_000
MAX_TOTAL = 110_000
SCHEMA = {"type": "object", "properties": {
    "summary": {"type": "string", "description": "what this change does, for the clinic owner, plain words"},
    "edits": {"type": "array", "maxItems": 30, "items": {"type": "object", "properties": {
        "path": {"type": "string"}, "find": {"type": "string", "description": "exact existing text, unique in the file"},
        "replace": {"type": "string"}}, "required": ["path", "find", "replace"], "additionalProperties": False}},
    "creates": {"type": "array", "maxItems": 5, "items": {"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}}},
    "required": ["summary", "edits", "creates"], "additionalProperties": False}
PROMPT = ("You are a careful senior Next.js (App Router) engineer fixing SEO issues on a veterinary clinic's website. You get the "
          "issues and the current files. Make the smallest correct changes that fix as many issues as possible without breaking "
          "the build: valid TypeScript/JSX, keep imports working, do not rename exports, do not touch styling. Prefer central fixes "
          "(one site URL constant for canonicals and the sitemap, generateMetadata/metadata per page with a unique title and "
          "description, a VeterinaryCare JSON-LD block in the layout, public/llms.txt). Use ONLY facts that appear in the files or "
          "page texts given (name, address, phone, hours); never invent them, leave such fields out instead. Each edit's 'find' must "
          "be copied exactly from the file and occur once. Reply with JSON only.")


def get_cfg(db: Session) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    stored = settings_store.get_raw(db, KEY, None)
    if isinstance(stored, dict):
        cfg.update({k: v for k, v in stored.items() if k in DEFAULTS})
    return cfg


def public(cfg: dict[str, Any]) -> dict[str, Any]:
    return {"repo": cfg["repo"], "branch": cfg["branch"], "mode": cfg["mode"], "has_token": bool(cfg["token_enc"]), "token_last4": cfg["token_last4"]}


class Gh:
    def __init__(self, token: str, repo: str):
        self.repo = repo
        self.h = httpx.Client(base_url=API, timeout=30.0, transport=TRANSPORT, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                                                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "walnut-desk-seo-agent"})

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.h.close()

    def req(self, method: str, path: str, **kw) -> Any:
        r = self.h.request(method, f"/repos/{self.repo}{path}", **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"github_{r.status_code}")
        return r.json() if r.content else {}

    def file(self, path: str, ref: str) -> str | None:
        r = self.h.get(f"/repos/{self.repo}/contents/{path}", params={"ref": ref})
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise RuntimeError(f"github_{r.status_code}")
        d = r.json()
        return base64.b64decode(d.get("content", "")).decode("utf-8", "replace") if isinstance(d, dict) and d.get("encoding") == "base64" else None


def _pick_files(tree: list[str], issues: list[dict[str, Any]]) -> list[str]:
    files = [f for f in ALWAYS if f in tree]
    for i in issues:
        for u in i.get("urls", [])[:6]:
            p = urlparse(u).path.strip("/")
            cand = f"app/{p}/page.tsx" if p else "app/page.tsx"
            if cand in tree and cand not in files:
                files.append(cand)
    for extra in ("app/contact/page.tsx", "app/faq/page.tsx", "app/page.tsx"):
        if extra in tree and extra not in files:
            files.append(extra)
    return files[:14]


def plan_and_push(change_id: int, token: str, cfg: dict[str, Any], ai_cfg: dict[str, Any], issues: list[dict[str, Any]], page_texts: str) -> None:
    """Background: read files, ask the AI for edits, commit them on a new branch, open a pull request. Own DB sessions only."""
    from app import db as app_db
    from app.services.transcribe import _post
    S = app_db.get_sessionmaker()

    def fail(msg: str) -> None:
        with S() as s:
            c = s.get(WebsiteChange, change_id)
            c.status, c.error = "failed", msg[:300]
            s.commit()
    try:
        with Gh(token, cfg["repo"]) as gh:
            base_sha = gh.req("GET", f"/git/ref/heads/{cfg['branch']}")["object"]["sha"]
            tree_sha = gh.req("GET", f"/git/commits/{base_sha}")["tree"]["sha"]
            tree = [t["path"] for t in gh.req("GET", f"/git/trees/{tree_sha}", params={"recursive": "1"}).get("tree", []) if t.get("type") == "blob"]
            files: dict[str, str] = {}
            total = 0
            for f in _pick_files(tree, issues):
                text = gh.file(f, base_sha)
                if text is None or len(text) > MAX_FILE or total + len(text) > MAX_TOTAL:
                    continue
                files[f] = text
                total += len(text)
            brief = [{k: i.get(k) for k in ("severity", "area", "title", "fix", "detail")} | {"pages": [urlparse(u).path for u in i.get("urls", [])[:8]]} for i in issues]
            msg = (f"ISSUES:\n{json.dumps(brief)}\n\nPAGE TEXTS (facts you may use):\n{page_texts[:8000]}\n\nFILES:\n"
                   + "\n\n".join(f"=== {p} ===\n{t}" for p, t in files.items()) + f"\n\nOTHER FILES IN THE REPO (not shown): {', '.join(sorted(t for t in tree if t.startswith('app/'))[:120])}")
            content = _post(ai_cfg, {"model": ai_cfg["model"], "temperature": 0.1, "max_tokens": 12000,
                                     "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": msg}],
                                     "response_format": {"type": "json_schema", "json_schema": {"name": "code_changes", "strict": True, "schema": SCHEMA}}}, timeout=300.0)
            m = re.search(r"\{.*\}", content, re.S)
            data = json.loads(m.group(0)) if m else {}
            new: dict[str, str] = {}
            skipped = 0
            for e in data.get("edits", []):
                path, find, repl = str(e.get("path", "")), str(e.get("find", "")), str(e.get("replace", ""))
                cur = new.get(path, files.get(path))
                if cur is None or not PATH_OK.match(path) or ".." in path or not find or cur.count(find) != 1 or len(repl) > 20_000:
                    skipped += 1
                    continue
                new[path] = cur.replace(find, repl, 1)
            for c in data.get("creates", []):
                path, body = str(c.get("path", "")), str(c.get("content", ""))
                if not PATH_OK.match(path) or ".." in path or path in tree or len(body) > 20_000:
                    skipped += 1
                    continue
                new[path] = body
            if not new:
                fail("The AI did not produce a change that could be applied safely. Nothing was pushed.")
                return
            diff = "".join("".join(difflib.unified_diff((files.get(p) or "").splitlines(True), t.splitlines(True), f"a/{p}", f"b/{p}")) for p, t in new.items())
            blobs = [{"path": p, "mode": "100644", "type": "blob", "sha": gh.req("POST", "/git/blobs", json={"content": t, "encoding": "utf-8"})["sha"]} for p, t in new.items()]
            tree_new = gh.req("POST", "/git/trees", json={"base_tree": tree_sha, "tree": blobs})["sha"]
            summary = str(data.get("summary") or "SEO fixes").strip()[:2000]
            commit = gh.req("POST", "/git/commits", json={"message": f"SEO: {summary[:72]}\n\n{summary}\n\nMade by the Walnut Grove Desk SEO agent.", "tree": tree_new, "parents": [base_sha]})["sha"]
            branch = f"seo-agent/{change_id}-{timeutil.now_utc().strftime('%Y%m%d%H%M')}"
            gh.req("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": commit})
            pr = gh.req("POST", "/pulls", json={"title": f"SEO agent: {summary[:80]}", "head": branch, "base": cfg["branch"],
                                                "body": f"{summary}\n\nFiles: {', '.join(new)}" + (f"\n\n{skipped} suggested edits were skipped because they could not be applied exactly." if skipped else "")
                                                + "\n\nOpened by the Walnut Grove Desk SEO agent. Vercel builds a preview of this branch."})
        with S() as s:
            c = s.get(WebsiteChange, change_id)
            c.status, c.branch, c.head_sha, c.pr_number, c.pr_url, c.summary, c.diff = "building", branch, commit, pr["number"], pr["html_url"], summary, diff[:200_000]
            s.commit()
    except Exception as e:  # noqa: BLE001 - nothing secret is logged
        log.warning("deploy agent failed (%s %s)", type(e).__name__, str(e)[:40] if isinstance(e, RuntimeError) else "")
        fail(f"Could not prepare the change ({str(e)[:60] if isinstance(e, RuntimeError) else type(e).__name__}). Check the GitHub token has Contents and Pull requests access to the repo.")


def start(db: Session, audit: WebsiteAudit, codes: list[str], who: str, mode: str | None = None) -> WebsiteChange:
    from app.assistant import ai
    cfg = get_cfg(db)
    if not cfg["token_enc"]:
        raise ValueError("no_token")
    ai_cfg = ai.get_config(db)
    if not ai_cfg.get("key_enc"):
        raise ValueError("no_ai_key")
    issues = [i for i in json.loads(audit.issues_json) if not codes or i["code"] in codes]
    pages = json.loads(audit.pages_json)
    texts = "\n\n".join(f"{urlparse(p['url']).path or '/'}: {p.get('text', '')[:1500]}" for p in pages if any(k in p["url"] for k in ("/contact", "/about")) or urlparse(p["url"]).path in ("", "/"))
    c = WebsiteChange(audit_id=audit.id, mode=mode or cfg["mode"], status="planning", created_at=timeutil.now_utc(), created_by=who[:120],
                      issue_codes=",".join(i["code"] for i in issues)[:600])
    db.add(c)
    db.commit()
    threading.Thread(target=plan_and_push, args=(c.id, crypto.decrypt(cfg["token_enc"]), cfg, ai_cfg, issues, texts), daemon=True).start()
    return c


def refresh(db: Session, c: WebsiteChange) -> WebsiteChange:
    """Look at the pull request's build checks; in auto mode, merge as soon as they pass. Called when the page looks."""
    if c.status not in ("building", "ready") or not c.head_sha:
        return c
    cfg = get_cfg(db)
    if not cfg["token_enc"]:
        return c
    token = crypto.decrypt(cfg["token_enc"])
    db.commit()
    try:
        with Gh(token, cfg["repo"]) as gh:
            st = gh.req("GET", f"/commits/{c.head_sha}/status")
            runs = gh.req("GET", f"/commits/{c.head_sha}/check-runs").get("check_runs", [])
            states = [s.get("state") for s in st.get("statuses", [])] + [("success" if r.get("conclusion") in ("success", "neutral", "skipped") else "failure" if r.get("conclusion") else "pending") for r in runs]
            preview = next((s.get("target_url") for s in st.get("statuses", []) if "vercel" in (s.get("context") or "").lower() and s.get("target_url")), "")
            pr = gh.req("GET", f"/pulls/{c.pr_number}")
            merged = bool(pr.get("merged"))
            closed = pr.get("state") == "closed" and not merged
            do_merge = False
            if not merged and not closed and states and all(x == "success" for x in states) and c.mode == "auto":
                do_merge = True
            if do_merge:
                gh.req("PUT", f"/pulls/{c.pr_number}/merge", json={"merge_method": "squash"})
                merged = True
        c = db.get(WebsiteChange, c.id)
        c.preview_url = preview or c.preview_url
        if merged:
            c.status, c.merged_at = "merged", c.merged_at or timeutil.now_utc()
        elif closed:
            c.status = "rejected"
        elif any(x in ("failure", "error") for x in states):
            c.status, c.error = "failed", "The preview build failed, so it was not merged. Open the pull request to see why."
        elif states and all(x == "success" for x in states):
            c.status = "ready"
        db.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("deploy agent refresh failed (%s)", type(e).__name__)
        db.rollback()
    return db.get(WebsiteChange, c.id)


def approve(db: Session, c: WebsiteChange) -> WebsiteChange:
    cfg = get_cfg(db)
    token = crypto.decrypt(cfg["token_enc"])
    db.commit()
    with Gh(token, cfg["repo"]) as gh:
        gh.req("PUT", f"/pulls/{c.pr_number}/merge", json={"merge_method": "squash"})
    c = db.get(WebsiteChange, c.id)
    c.status, c.merged_at = "merged", timeutil.now_utc()
    db.commit()
    return c


def reject(db: Session, c: WebsiteChange) -> WebsiteChange:
    cfg = get_cfg(db)
    token = crypto.decrypt(cfg["token_enc"])
    db.commit()
    with Gh(token, cfg["repo"]) as gh:
        gh.req("PATCH", f"/pulls/{c.pr_number}", json={"state": "closed"})
    c = db.get(WebsiteChange, c.id)
    c.status = "rejected"
    db.commit()
    return c


def view(c: WebsiteChange, full: bool = False) -> dict[str, Any]:
    out = {"id": c.id, "status": c.status, "mode": c.mode, "created_at": timeutil.iso(c.created_at), "created_by": c.created_by, "pr_url": c.pr_url,
           "preview_url": c.preview_url, "summary": c.summary, "error": c.error, "merged_at": timeutil.iso(c.merged_at) if c.merged_at else None,
           "issues": [x for x in c.issue_codes.split(",") if x]}
    if full:
        out["diff"] = c.diff
    return out
