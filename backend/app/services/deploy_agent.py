"""The deployment agent: turns SEO issues into code changes on the clinic website, and ships them through Vercel (no GitHub).

1. Reads the code that is live right now. When the live production deployment was made by this agent, its files come from the
   agent's own copy on the server (every file it ever uploaded is kept by its hash). Otherwise (the site was last deployed from
   a Git repository) they come from that exact public commit, read-only, no account needed.
2. The AI writes small, exact edits (find this text, replace it with that) or new files (like public/llms.txt). Every edit is
   checked: only inside app/, utils/, components/ or public/, the text to find must occur exactly once, sizes are capped.
3. The whole site, with the edits, is uploaded to the Vercel project as a PREVIEW deployment, and Vercel builds it.
4. Approve mode: a person reads the change and clicks Approve and deploy; the same files are then deployed to production.
   Auto mode: that happens as soon as the preview build passes. A failed build never reaches the live site.
The Vercel token is the owner's (scoped to the clinic's Vercel team), stored encrypted; it is never shown again or logged.
"""
from __future__ import annotations

import difflib
import hashlib
import io
import json
import logging
import os
import re
import tarfile
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
API = "https://api.vercel.com"
CODELOAD = "https://codeload.github.com"
TRANSPORT = None  # tests put a fake Vercel (and code download) here
DEFAULTS = {"mode": "approve", "token_enc": "", "token_last4": None, "team_id": "walnut-groves-projects", "project_id": "", "project_name": "walnut-grove", "domains": [], "framework": ""}
PATH_OK = re.compile(r"^(app|utils|components|public)/[A-Za-z0-9_.\[\]()/@-]{1,180}$")
SKIP_UPLOAD = re.compile(r"(^|/)(\.git|node_modules|\.next|\.vercel|\.turbo)(/|$)|(^|/)\.env(\.|$)|\.DS_Store$")
ALWAYS = ["app/layout.tsx", "app/sitemap.ts", "app/robots.ts", "utils/seoBuilder.js", "utils/schemaBuilder.js", "utils/constants.ts"]
MAX_FILE = 30_000          # biggest file shown to the AI
MAX_TOTAL = 110_000        # all files shown to the AI together
MAX_SITE_FILE = 25 * 1024 * 1024
MAX_SITE = 400 * 1024 * 1024
MAX_FILES = 6000
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


class VercelError(RuntimeError):
    def __init__(self, status: int, code: str = "", message: str = "", body: Any = None):
        super().__init__(f"vercel_{status}{'_' + code if code else ''}")
        self.status, self.code, self.message, self.body = status, code, message, body


def get_cfg(db: Session) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    stored = settings_store.get_raw(db, KEY, None)
    if isinstance(stored, dict):
        cfg.update({k: v for k, v in stored.items() if k in DEFAULTS})
    return cfg


def connected(cfg: dict[str, Any]) -> bool:
    return bool(cfg["token_enc"] and cfg["project_id"])


def public(cfg: dict[str, Any]) -> dict[str, Any]:
    return {"mode": cfg["mode"], "has_token": bool(cfg["token_enc"]), "token_last4": cfg["token_last4"], "team_id": cfg["team_id"],
            "project_id": cfg["project_id"], "project_name": cfg["project_name"], "domains": cfg["domains"], "connected": connected(cfg),
            "dashboard": "https://vercel.com/dashboard"}


# ---------------------------------------------------------------- Vercel
class Vc:
    def __init__(self, token: str, team_id: str):
        self.team = team_id
        self.h = httpx.Client(base_url=API, timeout=60.0, transport=TRANSPORT, headers={"Authorization": f"Bearer {token}", "User-Agent": "walnut-desk-seo-agent"})

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.h.close()

    def req(self, method: str, path: str, params: dict[str, Any] | None = None, **kw) -> Any:
        # the team can be its id (team_...) or the name in the dashboard address (vercel.com/<name>)
        q = {**(params or {}), **(({"teamId": self.team} if self.team.startswith("team_") else {"slug": self.team}) if self.team else {})}
        r = self.h.request(method, path, params=q, **kw)
        if r.status_code >= 400:
            try:
                err = r.json().get("error") or {}
            except ValueError:
                err = {}
            raise VercelError(r.status_code, str(err.get("code") or ""), str(err.get("message") or "")[:200], err)
        return r.json() if r.content else {}

    def upload(self, data: bytes, sha: str) -> None:
        self.req("POST", "/v2/files", content=data, headers={"Content-Type": "application/octet-stream", "x-vercel-digest": sha, "Content-Length": str(len(data))})


def check_connection(token: str, team_id: str, project_id: str) -> dict[str, Any]:
    """Look the project up (by id or by name) with the token; returns its id, name, framework and domains. Raises VercelError when it does not work."""
    with Vc(token, team_id) as vc:
        p = vc.req("GET", f"/v9/projects/{project_id}")
        doms = vc.req("GET", f"/v9/projects/{p.get('id') or project_id}/domains").get("domains", [])
    live = [d["name"] for d in doms if not d.get("redirect")]
    custom = sorted([d for d in live if not d.endswith(".vercel.app")], key=lambda d: (not d.startswith("www."), d))
    return {"project_id": p.get("id") or project_id, "project_name": p.get("name", ""), "framework": p.get("framework") or "",
            "domains": custom + [d for d in live if d.endswith(".vercel.app")]}


def site_dir() -> str:
    from app import config
    d = os.path.join(os.path.dirname(os.path.abspath(config.get_settings().db_path)), "site")
    os.makedirs(os.path.join(d, "blobs"), mode=0o750, exist_ok=True)
    os.makedirs(os.path.join(d, "deployments"), mode=0o750, exist_ok=True)
    return d


def _sha(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()  # what Vercel uses to name uploaded files


def _keep(files: dict[str, bytes]) -> list[dict[str, Any]]:
    """Store every file by its hash (once) and return the manifest Vercel wants."""
    d = site_dir()
    out = []
    for path, data in sorted(files.items()):
        sha = _sha(data)
        blob = os.path.join(d, "blobs", sha)
        if not os.path.exists(blob):
            with open(blob + ".part", "wb") as fh:
                fh.write(data)
            os.replace(blob + ".part", blob)
        out.append({"file": path, "sha": sha, "size": len(data)})
    return out


def _save_manifest(dep_id: str, manifest: list[dict[str, Any]]) -> None:
    with open(os.path.join(site_dir(), "deployments", re.sub(r"[^A-Za-z0-9_-]", "", dep_id) + ".json"), "w") as fh:
        json.dump(manifest, fh)


def _load_manifest(dep_id: str) -> list[dict[str, Any]] | None:
    p = os.path.join(site_dir(), "deployments", re.sub(r"[^A-Za-z0-9_-]", "", dep_id) + ".json")
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        return json.load(fh)


def _blob(sha: str) -> bytes:
    with open(os.path.join(site_dir(), "blobs", re.sub(r"[^0-9a-f]", "", sha)), "rb") as fh:
        return fh.read()


def _from_git(org: str, repo: str, sha: str) -> dict[str, bytes]:
    """The exact commit the live site was built from, as a public download (read only, no account)."""
    with httpx.Client(timeout=120.0, transport=TRANSPORT, follow_redirects=True, headers={"User-Agent": "walnut-desk-seo-agent"}) as h:
        r = h.get(f"{CODELOAD}/{org}/{repo}/tar.gz/{sha}")
    if r.status_code != 200:
        raise VercelError(r.status_code, "source_download", "The site's code could not be downloaded from its public repository.")
    files: dict[str, bytes] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue  # no links, devices or folders
            parts = m.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue
            path = parts[1]
            if ".." in path.split("/") or path.startswith("/") or SKIP_UPLOAD.search(path) or m.size > MAX_SITE_FILE:
                continue
            total += m.size
            if total > MAX_SITE or len(files) >= MAX_FILES:
                raise VercelError(413, "too_big", "The site is larger than the agent handles.")
            fh = tar.extractfile(m)
            if fh is not None:
                files[path] = fh.read()
    if not files:
        raise VercelError(422, "empty", "The downloaded code was empty.")
    return files


def live_source(vc: Vc, project_id: str) -> tuple[dict[str, bytes], str]:
    """The code of the newest READY production deployment, and a short note on where it came from."""
    deps = vc.req("GET", "/v6/deployments", params={"projectId": project_id, "target": "production", "state": "READY", "limit": 5}).get("deployments", [])
    if not deps:
        raise VercelError(404, "no_production", "The Vercel project has no live (production) deployment yet.")
    d = deps[0]
    dep_id = d.get("uid") or d.get("id")
    mine = _load_manifest(dep_id)
    if mine is not None:
        return {f["file"]: _blob(f["sha"]) for f in mine}, f"the agent's own last deployment ({dep_id})"
    meta = d.get("meta") or {}
    if meta.get("githubCommitSha") and meta.get("githubCommitOrg") and meta.get("githubCommitRepo"):
        files = _from_git(meta["githubCommitOrg"], meta["githubCommitRepo"], meta["githubCommitSha"])
        return files, f"{meta['githubCommitOrg']}/{meta['githubCommitRepo']} at {meta['githubCommitSha'][:7]} (what is live now)"
    raise VercelError(409, "unknown_source", "The live site was deployed in a way the agent cannot read the code of (not from Git, not by this app).")


def deploy(vc: Vc, cfg: dict[str, Any], manifest: list[dict[str, Any]], target: str | None, change_id: int) -> dict[str, Any]:
    """Create a deployment from stored files; uploads only the files Vercel does not have yet."""
    body = {"name": cfg["project_name"] or "walnut-grove", "project": cfg["project_id"], "files": manifest,
            "meta": {"walnutDesk": "seo-agent", "walnutDeskChange": str(change_id)}}
    if target:
        body["target"] = target
    for _ in range(3):
        try:
            return vc.req("POST", "/v13/deployments", params={"skipAutoDetectionConfirmation": 1}, json=body)
        except VercelError as e:
            missing = (e.body or {}).get("missing") if e.code == "missing_files" else None
            if not missing:
                raise
            for sha in missing:
                vc.upload(_blob(sha), sha)
    raise VercelError(500, "upload_loop", "Vercel kept asking for files.")


# ---------------------------------------------------------------- the agent
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


def plan_and_deploy(change_id: int, token: str, cfg: dict[str, Any], ai_cfg: dict[str, Any], issues: list[dict[str, Any]], page_texts: str) -> None:
    """Background: read the live code, ask the AI for edits, upload the result as a preview deployment. Own DB sessions only."""
    from app import db as app_db
    from app.services.transcribe import _post
    S = app_db.get_sessionmaker()

    def fail(msg: str) -> None:
        with S() as s:
            c = s.get(WebsiteChange, change_id)
            c.status, c.error = "failed", msg[:300]
            s.commit()
    try:
        with Vc(token, cfg["team_id"]) as vc:
            site, origin = live_source(vc, cfg["project_id"])
            tree = sorted(site)
            files: dict[str, str] = {}
            total = 0
            for f in _pick_files(tree, issues):
                text = site[f].decode("utf-8", "replace")
                if len(text) > MAX_FILE or total + len(text) > MAX_TOTAL:
                    continue
                files[f] = text
                total += len(text)
            brief = [{k: i.get(k) for k in ("severity", "area", "title", "fix", "detail")} | {"pages": [urlparse(u).path for u in i.get("urls", [])[:8]]} for i in issues]
            msg = (f"ISSUES:\n{json.dumps(brief)}\n\nPAGE TEXTS (facts you may use):\n{page_texts[:8000]}\n\nFILES:\n"
                   + "\n\n".join(f"=== {p} ===\n{t}" for p, t in files.items()) + f"\n\nOTHER FILES IN THE SITE (not shown): {', '.join(t for t in tree if t.startswith('app/'))[:6000]}")
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
                if not PATH_OK.match(path) or ".." in path or path in site or len(body) > 20_000:
                    skipped += 1
                    continue
                new[path] = body
            if not new:
                fail("The AI did not produce a change that could be applied safely. Nothing was deployed.")
                return
            diff = "".join("".join(difflib.unified_diff((files.get(p) or "").splitlines(True), t.splitlines(True), f"a/{p}", f"b/{p}")) for p, t in new.items())
            site.update({p: t.encode("utf-8") for p, t in new.items()})
            manifest = _keep(site)
            dep = deploy(vc, cfg, manifest, None, change_id)
            _save_manifest(dep["id"], manifest)
        summary = str(data.get("summary") or "SEO fixes").strip()[:2000]
        if skipped:
            summary += f"\n\n{skipped} suggested edits were skipped because they could not be applied exactly."
        with S() as s:
            c = s.get(WebsiteChange, change_id)
            c.status, c.head_sha, c.summary, c.diff = "building", dep["id"], summary, diff[:200_000]
            c.preview_url = "https://" + dep["url"] if dep.get("url") else None
            c.pr_url = dep.get("inspectorUrl") or None
            c.branch = ""  # the production deployment id, once approved
            s.commit()
        log.info("deploy agent: preview from %s", origin.split(" ")[0])
    except VercelError as e:
        log.warning("deploy agent failed (%s)", e)
        fail(f"Vercel said no ({e.message or e}). Check the token has access to the project's team." if e.status in (401, 403)
             else f"Could not prepare the change: {e.message or e}")
    except Exception as e:  # noqa: BLE001 - nothing secret is logged
        log.warning("deploy agent failed (%s)", type(e).__name__)
        fail(f"Could not prepare the change ({type(e).__name__}).")


def start(db: Session, audit: WebsiteAudit, codes: list[str], who: str, mode: str | None = None) -> WebsiteChange:
    from app.assistant import ai
    cfg = get_cfg(db)
    if not connected(cfg):
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
    threading.Thread(target=plan_and_deploy, args=(c.id, crypto.decrypt(cfg["token_enc"]), cfg, ai_cfg, issues, texts), daemon=True).start()
    return c


def _state(vc: Vc, dep_id: str) -> tuple[str, dict[str, Any]]:
    d = vc.req("GET", f"/v13/deployments/{dep_id}")
    return str(d.get("readyState") or d.get("status") or ""), d


def refresh(db: Session, c: WebsiteChange) -> WebsiteChange:
    """Follow the preview build (and, after approval, the production build). In auto mode, ship as soon as the preview passes."""
    if c.status not in ("building", "ready", "deploying") or not c.head_sha:
        return c
    cfg = get_cfg(db)
    if not connected(cfg):
        return c
    token = crypto.decrypt(cfg["token_enc"])
    cid, status, preview_id, prod_id, mode = c.id, c.status, c.head_sha, c.branch, c.mode
    db.commit()
    try:
        with Vc(token, cfg["team_id"]) as vc:
            if status == "deploying" and prod_id:
                st, d = _state(vc, prod_id)
                c = db.get(WebsiteChange, cid)
                if st == "READY":
                    c.status, c.merged_at = "merged", c.merged_at or timeutil.now_utc()
                elif st in ("ERROR", "CANCELED"):
                    c.status, c.error = "failed", "The production build failed, so the live site did not change."
                db.commit()
                return c
            st, d = _state(vc, preview_id)
            if st in ("ERROR", "CANCELED"):
                c = db.get(WebsiteChange, cid)
                c.status, c.error = "failed", "The preview build failed, so nothing was deployed. Open it on Vercel to see why."
                db.commit()
                return c
            if st == "READY":
                if mode == "auto":
                    return _ship(db, vc, cfg, cid)
                c = db.get(WebsiteChange, cid)
                c.status = "ready"
                db.commit()
                return c
    except Exception as e:  # noqa: BLE001
        log.warning("deploy agent refresh failed (%s)", type(e).__name__)
        db.rollback()
    return db.get(WebsiteChange, cid)


def _ship(db: Session, vc: Vc, cfg: dict[str, Any], cid: int) -> WebsiteChange:
    c = db.get(WebsiteChange, cid)
    manifest = _load_manifest(c.head_sha)
    if manifest is None:
        raise VercelError(410, "gone", "The files of this preview are no longer on the server.")
    db.commit()
    dep = deploy(vc, cfg, manifest, "production", cid)
    _save_manifest(dep["id"], manifest)
    c = db.get(WebsiteChange, cid)
    c.status, c.branch = "deploying", dep["id"]
    db.commit()
    return c


def approve(db: Session, c: WebsiteChange) -> WebsiteChange:
    cfg = get_cfg(db)
    token = crypto.decrypt(cfg["token_enc"])
    cid = c.id
    db.commit()
    with Vc(token, cfg["team_id"]) as vc:
        return _ship(db, vc, cfg, cid)


def reject(db: Session, c: WebsiteChange) -> WebsiteChange:
    c.status = "rejected"
    db.commit()
    return c


def view(c: WebsiteChange, full: bool = False) -> dict[str, Any]:
    out = {"id": c.id, "status": c.status, "mode": c.mode, "created_at": timeutil.iso(c.created_at), "created_by": c.created_by, "inspect_url": c.pr_url,
           "preview_url": c.preview_url, "summary": c.summary, "error": c.error, "merged_at": timeutil.iso(c.merged_at) if c.merged_at else None,
           "issues": [x for x in c.issue_codes.split(",") if x]}
    if full:
        out["diff"] = c.diff
    return out
