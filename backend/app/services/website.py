"""The clinic website: read its pages from the live site, check them for SEO problems, and help fix them.

* Crawl: the home page, robots.txt, sitemap.xml and every same-site link (up to MAX_PAGES), read-only, a few at a time. For each
  page: status, speed, size, title, description, canonical, robots, headings, images without alt text, Open Graph, structured
  data (JSON-LD) and the visible text (shown in Website content, and used to suggest knowledge).
* Checks (in the spirit of the usual SEO checklists: crawl access, indexing, canonicals, redirects, sitemap, metadata, links,
  structured data, speed): each issue has a severity, the pages it affects, and a suggested fix.
* Each audit is kept, so the next one shows what is new and what was fixed.
* The AI (the assistant's OpenRouter model) turns an audit into a prioritised fix plan with ready-to-paste code for the site's
  Next.js code, and can suggest knowledge entries from the site's own text (PENDING, for an owner to approve).
The crawl and the model run with no database transaction open.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WebsiteAudit
from app.services import settings_store, timeutil

log = logging.getLogger("walnut.website")
KEY = "website"
DEFAULTS = {"url": "https://walnut-grove-salazar.vercel.app", "canonical": "https://walnutgroveanimalclinic.com",
            "repo": "https://github.com/Salazar69420/walnut-grove-salazar", "studio": "https://walnut-grove-salazar.vercel.app/studio"}
MAX_PAGES = 80
WORKERS = 4
TIMEOUT = 20.0
UA = "WalnutGroveDesk-SEO/1.0 (+site owner audit)"
SKIP = re.compile(r"\.(png|jpe?g|gif|webp|svg|ico|pdf|zip|mp4|mp3|css|js|xml|txt|woff2?)$", re.I)
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def get_settings(db: Session) -> dict[str, str]:
    cfg = dict(DEFAULTS)
    stored = settings_store.get_raw(db, KEY, None)
    if isinstance(stored, dict):
        cfg.update({k: str(v) for k, v in stored.items() if k in DEFAULTS and v})
    return cfg


# ---------------------------------------------------------------- parsing one page
class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self.lang = ""
        self.h1: list[str] = []
        self.h2 = 0
        self.links: list[str] = []
        self.imgs = 0
        self.imgs_no_alt = 0
        self.jsonld: list[str] = []
        self.text: list[str] = []
        self._in: list[str] = []
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "meta":
            k = (a.get("name") or a.get("property") or "").lower()
            if k:
                self.meta[k] = a.get("content", "")
        elif tag == "link" and "canonical" in a.get("rel", "").lower():
            self.canonical = a.get("href", "")
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "img":
            self.imgs += 1
            if "alt" not in a:
                self.imgs_no_alt += 1
        elif tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in.append("ld")
            self._buf = ""
            return
        if tag in ("title", "h1", "h2", "script", "style", "noscript", "svg"):
            self._in.append(tag)
            self._buf = ""

    def handle_endtag(self, tag):
        if not self._in:
            return
        top = self._in[-1]
        if tag == "script" and top == "ld":
            self.jsonld.append(self._buf)
            self._in.pop()
        elif tag == top:
            if tag == "title":
                self.title = self._buf.strip()
            elif tag == "h1":
                self.h1.append(re.sub(r"\s+", " ", self._buf).strip())
            elif tag == "h2":
                self.h2 += 1
            self._in.pop()

    def handle_data(self, data):
        if self._in:
            self._buf += data
            if self._in[-1] in ("h1", "h2"):
                self.text.append(data)
            return
        self.text.append(data)


def _schema_types(blocks: list[str]) -> tuple[list[str], int]:
    types: list[str] = []
    bad = 0
    for b in blocks:
        try:
            data = json.loads(b)
        except ValueError:
            bad += 1
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                t = x.get("@type")
                types += t if isinstance(t, list) else ([t] if isinstance(t, str) else [])
                stack += [v for v in x.values() if isinstance(v, (dict, list))]
            elif isinstance(x, list):
                stack += x
    return sorted(set(types)), bad


def fetch_page(client: httpx.Client, url: str) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        r = client.get(url)
    except httpx.HTTPError as e:
        return {"url": url, "status": 0, "error": type(e).__name__, "ms": int((time.monotonic() - t0) * 1000)}
    ms = int((time.monotonic() - t0) * 1000)
    page: dict[str, Any] = {"url": url, "final_url": str(r.url), "status": r.status_code, "ms": ms, "bytes": len(r.content),
                            "redirects": len(r.history), "type": r.headers.get("content-type", "").split(";")[0],
                            "encoding": r.headers.get("content-encoding", ""), "cache": r.headers.get("cache-control", "")}
    if r.status_code != 200 or "html" not in page["type"]:
        return page
    p = _Page()
    try:
        p.feed(r.text)
    except Exception:  # noqa: BLE001 - a broken page is still reported
        pass
    types, bad = _schema_types(p.jsonld)
    text = re.sub(r"\s+", " ", " ".join(p.text)).strip()
    page.update(title=p.title[:200], description=p.meta.get("description", "")[:400], robots=p.meta.get("robots", ""), canonical=p.canonical,
                lang=p.lang, viewport=bool(p.meta.get("viewport")), og_title=bool(p.meta.get("og:title")), og_description=bool(p.meta.get("og:description")),
                og_image=bool(p.meta.get("og:image")), h1=p.h1[:5], h2=p.h2, imgs=p.imgs, imgs_no_alt=p.imgs_no_alt, schema=types, schema_errors=bad,
                words=len(text.split()), text=text[:6000],
                links=sorted({urldefrag(urljoin(str(r.url), h))[0] for h in p.links if not h.startswith(("mailto:", "tel:", "javascript:", "#"))})[:300])
    return page


def _same_site(u: str, host: str) -> bool:
    p = urlparse(u)
    return p.scheme in ("http", "https") and p.netloc == host and not SKIP.search(p.path)


def _sitemap_urls(client: httpx.Client, base: str) -> tuple[list[str], dict[str, Any]]:
    info: dict[str, Any] = {}
    try:
        r = client.get(urljoin(base, "/robots.txt"))
        info["robots_status"], info["robots"] = r.status_code, r.text[:3000] if r.status_code == 200 else ""
    except httpx.HTTPError:
        info["robots_status"], info["robots"] = 0, ""
    urls: list[str] = []
    try:
        r = client.get(urljoin(base, "/sitemap.xml"))
        info["sitemap_status"] = r.status_code
        if r.status_code == 200:
            urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)[:500]
    except httpx.HTTPError:
        info["sitemap_status"] = 0
    info["sitemap_urls"] = len(urls)
    info["sitemap_hosts"] = sorted({urlparse(u).netloc for u in urls})
    return urls, info


def crawl(base: str, max_pages: int = MAX_PAGES) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base = base.rstrip("/") + "/"
    host = urlparse(base).netloc
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA}) as client:
        smap, site = _sitemap_urls(client, base)
        # the sitemap may name another domain (the final one); check the same paths on the site being audited
        queue = [base] + [urljoin(base, urlparse(u).path or "/") for u in smap]
        seen: set[str] = set()
        pages: list[dict[str, Any]] = []
        while queue and len(pages) < max_pages:
            batch = []
            while queue and len(batch) < WORKERS * 2 and len(pages) + len(batch) < max_pages:
                u = urldefrag(queue.pop(0))[0].rstrip("/") or base
                if u in seen or not _same_site(u if u.endswith("/") else u + "/", host) and u != base.rstrip("/"):
                    continue
                seen.add(u)
                batch.append(u)
            if not batch:
                continue
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                for page in pool.map(lambda u: fetch_page(client, u), batch):
                    pages.append(page)
                    for link in page.get("links", []):
                        lu = link.rstrip("/")
                        if _same_site(link, host) and lu not in seen:
                            queue.append(lu)
        site["sitemap_paths"] = sorted({urlparse(u).path.rstrip("/") or "/" for u in smap})
    return pages, site


# ---------------------------------------------------------------- checks
def _issue(code, severity, title, fix, urls=None, detail=""):
    return {"code": code, "severity": severity, "title": title, "fix": fix, "urls": sorted(set(urls or []))[:50], "count": len(set(urls or [])), "detail": detail}


def check(pages: list[dict[str, Any]], site: dict[str, Any], base: str, canonical_host: str) -> list[dict[str, Any]]:
    host = urlparse(base).netloc
    want = urlparse(canonical_host).netloc if canonical_host else host
    ok = [p for p in pages if p.get("status") == 200 and p.get("title") is not None and "html" in p.get("type", "")]
    issues: list[dict[str, Any]] = []
    broken = [p["url"] for p in pages if p.get("status", 0) >= 400 or p.get("status") == 0]
    if broken:
        issues.append(_issue("broken_pages", "high", "Pages that do not load (404, 500 or no answer)",
                             "Fix or remove the links to these pages, or add a redirect to the right page.", broken))
    if site.get("robots_status") != 200:
        issues.append(_issue("no_robots", "medium", "robots.txt is missing", "Add app/robots.ts returning rules and the sitemap URL."))
    elif re.search(r"(?im)^\s*disallow:\s*/\s*$", site.get("robots", "")):
        issues.append(_issue("robots_blocks_all", "high", "robots.txt blocks the whole site", "Remove 'Disallow: /' so search engines can crawl the site."))
    if site.get("sitemap_status") != 200 or not site.get("sitemap_urls"):
        issues.append(_issue("no_sitemap", "high", "sitemap.xml is missing or empty", "Return every public page from app/sitemap.ts."))
    elif want and any(h != want for h in site.get("sitemap_hosts", [])):
        issues.append(_issue("sitemap_host", "high", f"The sitemap lists other domains than {want}",
                             "Build sitemap URLs from one setting (for example NEXT_PUBLIC_SITE_URL) so they always match the live domain.",
                             detail=", ".join(site.get("sitemap_hosts", []))))
    crawled_paths = {urlparse(p["url"]).path.rstrip("/") or "/" for p in ok}
    missing_from_sitemap = sorted(crawled_paths - set(site.get("sitemap_paths", [])))
    if site.get("sitemap_urls") and missing_from_sitemap:
        issues.append(_issue("not_in_sitemap", "medium", "Pages that are linked but not in the sitemap",
                             "Add these routes to app/sitemap.ts (blog posts and resource pages are easy to miss).", [urljoin(base, x) for x in missing_from_sitemap]))
    sm_broken = [x for x in site.get("sitemap_paths", []) if x not in crawled_paths and urljoin(base, x).rstrip("/") in {p["url"].rstrip("/") for p in pages if p.get("status") != 200}]
    if sm_broken:
        issues.append(_issue("sitemap_broken", "high", "Sitemap pages that do not load", "Remove them from the sitemap or fix the pages.", [urljoin(base, x) for x in sm_broken]))

    def per_page(code, sev, title, fix, cond):
        urls = [p["url"] for p in ok if cond(p)]
        if urls:
            issues.append(_issue(code, sev, title, fix, urls))
    per_page("noindex", "high", "Pages told not to appear in search (noindex)", "Remove noindex unless the page should really be hidden.",
             lambda p: "noindex" in (p.get("robots") or "").lower())
    per_page("no_title", "high", "Pages without a title", "Set metadata.title in each page (or generateMetadata).", lambda p: not p.get("title"))
    per_page("title_length", "low", "Titles that are too short or too long (aim for 30 to 60 characters)", "Write a specific title with the service and Langley.",
             lambda p: p.get("title") and not 30 <= len(p["title"]) <= 65)
    dup_titles = [t for t, n in Counter(p.get("title") for p in ok if p.get("title")).items() if n > 1]
    per_page("duplicate_title", "medium", "Pages sharing the same title", "Give every page its own title.", lambda p: p.get("title") in dup_titles)
    per_page("no_description", "medium", "Pages without a meta description", "Set metadata.description (one or two sentences, 70 to 160 characters).",
             lambda p: not p.get("description"))
    dup_desc = [t for t, n in Counter(p.get("description") for p in ok if p.get("description")).items() if n > 1]
    per_page("duplicate_description", "medium", "Pages sharing the same description", "Write a description for each page.", lambda p: p.get("description") in dup_desc)
    per_page("no_canonical", "medium", "Pages without a canonical link", "Set metadata.alternates.canonical for each page.", lambda p: not p.get("canonical"))
    canon_hosts = sorted({urlparse(urljoin(p["url"], p["canonical"])).netloc for p in ok if p.get("canonical")})
    other = [h for h in canon_hosts if h not in (want, host)]
    if other:
        issues.append(_issue("canonical_host", "high", f"Canonical links use {', '.join(other)} but the sitemap and site settings use {want}",
                             "Pick one address (with or without www), redirect the other to it, and build canonicals and the sitemap from the same setting.",
                             [p["url"] for p in ok if p.get("canonical") and urlparse(urljoin(p["url"], p["canonical"])).netloc in other],
                             detail=f"canonical: {', '.join(other)}; sitemap: {', '.join(site.get('sitemap_hosts', [])) or 'none'}"))
    per_page("h1_count", "medium", "Pages without exactly one main heading (H1)", "Use one H1 per page that says what the page is about.",
             lambda p: len(p.get("h1") or []) != 1)
    per_page("img_alt", "medium", "Images without alt text", "Add a short alt text to each image (empty alt only for decoration).", lambda p: p.get("imgs_no_alt", 0) > 0)
    per_page("thin", "low", "Pages with little text (under 250 words)", "Add useful detail: what is included, who it is for, what to expect, prices if public.",
             lambda p: p.get("words", 0) < 250)
    per_page("no_og", "low", "Pages without social sharing tags (Open Graph)", "Set metadata.openGraph (title, description, image).",
             lambda p: not (p.get("og_title") and p.get("og_image")))
    per_page("no_lang", "low", "Pages without a language set", "Set <html lang=\"en\"> in app/layout.tsx.", lambda p: not p.get("lang"))
    per_page("no_viewport", "medium", "Pages without a mobile viewport", "Next.js adds it by default; check the layout.", lambda p: not p.get("viewport"))
    per_page("schema_errors", "medium", "Structured data that does not parse", "Fix the JSON-LD so it is valid JSON.", lambda p: p.get("schema_errors", 0) > 0)
    home = next((p for p in ok if urlparse(p["url"]).path in ("", "/")), None)
    if home is not None and not {"VeterinaryCare", "LocalBusiness", "AnimalShelter", "MedicalBusiness"} & set(home.get("schema", [])):
        issues.append(_issue("no_local_schema", "high", "The home page has no local business structured data (VeterinaryCare)",
                             "Add a VeterinaryCare JSON-LD block (name, address, phone, opening hours, geo, url) to the layout; it feeds Google's local results.",
                             [home["url"]]))
    if ok and not any("FAQPage" in p.get("schema", []) for p in ok) and any("/faq" in p["url"] for p in ok):
        issues.append(_issue("no_faq_schema", "low", "The FAQ page has no FAQPage structured data", "Add FAQPage JSON-LD built from the same questions and answers.",
                             [p["url"] for p in ok if "/faq" in p["url"]]))
    per_page("slow", "medium", "Slow pages (over 1.5 seconds to load the HTML)", "Cache server-rendered pages (revalidate), and check slow data fetching.",
             lambda p: p.get("ms", 0) > 1500)
    per_page("heavy", "low", "Heavy HTML (over 300 KB)", "Move large inline data out of the page; paginate long lists.", lambda p: p.get("bytes", 0) > 300_000)
    redirected = [p["url"] for p in pages if p.get("redirects", 0) > 1]
    if redirected:
        issues.append(_issue("redirect_chain", "medium", "Links that go through more than one redirect", "Point links straight at the final address.", redirected))
    if host != want and want and False:
        issues.append(_issue("staging_host", "low", f"Audited on {host}; the site's own domain is {want}",
                             "When the real domain is live, set it as the site URL so canonicals and the sitemap all use it, and redirect the old host."))
    issues.sort(key=lambda i: (SEVERITY_ORDER[i["severity"]], -i["count"]))
    return issues


def score(issues: list[dict[str, Any]]) -> int:
    pen = {"high": 12, "medium": 5, "low": 2}
    return max(0, 100 - sum(pen[i["severity"]] for i in issues))


def compare(new: list[dict[str, Any]], old: list[dict[str, Any]]) -> dict[str, list[str]]:
    o, n = {i["code"]: i for i in old}, {i["code"]: i for i in new}
    return {"new": [c for c in n if c not in o], "fixed": [c for c in o if c not in n],
            "worse": [c for c in n if c in o and n[c]["count"] > o[c]["count"]], "better": [c for c in n if c in o and n[c]["count"] < o[c]["count"]]}


# ---------------------------------------------------------------- running an audit (in the background)
def start(db: Session, who: str) -> WebsiteAudit:
    cfg = get_settings(db)
    a = WebsiteAudit(base_url=cfg["url"][:200], status="running", started_at=timeutil.now_utc(), started_by=who[:120])
    db.add(a)
    db.commit()
    threading.Thread(target=_run, args=(a.id, cfg), daemon=True).start()
    return a


def _run(audit_id: int, cfg: dict[str, str]) -> None:
    from app import db as app_db
    S = app_db.get_sessionmaker()
    try:
        pages, site = crawl(cfg["url"])
        issues = check(pages, site, cfg["url"], cfg.get("canonical", ""))
        with S() as s:
            prev = s.scalar(select(WebsiteAudit).where(WebsiteAudit.status == "done", WebsiteAudit.id != audit_id).order_by(WebsiteAudit.id.desc()).limit(1))
            diff = compare(issues, json.loads(prev.issues_json)) if prev else {"new": [], "fixed": [], "worse": [], "better": []}
            a = s.get(WebsiteAudit, audit_id)
            a.pages_json, a.issues_json = json.dumps(pages), json.dumps(issues)
            a.summary_json = json.dumps({"pages": len(pages), "ok_pages": sum(1 for p in pages if p.get("status") == 200), "score": score(issues),
                                         "by_severity": dict(Counter(i["severity"] for i in issues)), "site": {k: v for k, v in site.items() if k != "robots"},
                                         "avg_ms": int(sum(p.get("ms", 0) for p in pages) / max(1, len(pages))), "compare": diff, "previous_id": prev.id if prev else None})
            a.status, a.finished_at = "done", timeutil.now_utc()
            from app.services import events as ev
            ev.record(s, ev.ADMIN, "website_audit_done", f"Website audit finished: {len(pages)} pages, {len(issues)} issue types", "SEO agent", "setting", KEY)
            s.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("website audit failed (%s)", type(e).__name__)
        with S() as s:
            a = s.get(WebsiteAudit, audit_id)
            if a is not None:
                a.status, a.error, a.finished_at = "failed", type(e).__name__[:200], timeutil.now_utc()
                s.commit()


# ---------------------------------------------------------------- the AI parts
PLAN_PROMPT = ("You are an SEO engineer for a veterinary clinic's website (Next.js App Router with a Sanity CMS, hosted on Vercel). From the audit "
               "below, write a prioritised fix plan: at most 10 items, most valuable first. For each: what is wrong, why it matters for "
               "a local clinic, the exact change (file and code, for example app/layout.tsx metadata, generateMetadata, app/sitemap.ts, a "
               "VeterinaryCare JSON-LD block), and how to verify it. Use ONLY facts that appear in the page text given (name, address, phone, "
               "hours); where a fact is not given write TODO instead of inventing it. Plain text with short headings, no tables.")
KNOW_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "maxItems": 25, "items": {"type": "object", "properties": {
    "category": {"type": "string", "enum": ["clinic_info", "fees", "patient_knowledge", "patient_communication", "inquiry_communication", "specials"]},
    "title": {"type": "string"}, "text": {"type": "string"}, "page": {"type": "string"}},
    "required": ["category", "title", "text", "page"], "additionalProperties": False}}}, "required": ["items"], "additionalProperties": False}
KNOW_PROMPT = ("You read the public website of Walnut Grove Animal Hospital. List general facts a front-desk text assistant could use to answer "
               "clients: services offered and what they include, hours, location and parking, policies, what to bring, prices if stated, "
               "FAQ answers. Use ONLY what the pages say, one or two short sentences each, written as the clinic would text them. No medical "
               "advice or doses. Give the page path each fact came from. Reply with JSON only.")


def ai_plan(db: Session, audit: WebsiteAudit) -> str:
    from app.assistant import ai
    from app.services.transcribe import _post
    cfg = ai.get_config(db)
    if not cfg.get("key_enc"):
        raise ai.AiFailed("no_key")
    issues = json.loads(audit.issues_json)
    pages = json.loads(audit.pages_json)
    home = next((p for p in pages if urlparse(p["url"]).path in ("", "/")), {})
    contact = next((p for p in pages if "/contact" in p["url"]), {})
    brief = [{k: i[k] for k in ("severity", "title", "count", "fix", "detail")} | {"examples": i["urls"][:5]} for i in issues]
    facts = f"HOME PAGE TEXT:\n{home.get('text', '')[:3000]}\n\nCONTACT PAGE TEXT:\n{contact.get('text', '')[:2000]}"
    db.commit()
    text = _post(cfg, {"model": cfg["model"], "temperature": 0.2, "max_tokens": 3500,
                       "messages": [{"role": "system", "content": PLAN_PROMPT},
                                    {"role": "user", "content": f"Site: {audit.base_url}\nAUDIT ISSUES:\n{json.dumps(brief)[:12000]}\n\n{facts}"}]}, timeout=180.0)
    return re.sub(r"\s*[—–]\s*", ", ", text)[:20000]


def ai_knowledge(db: Session, audit: WebsiteAudit, user) -> int:
    """Facts from the site's own pages become PENDING knowledge ("Website: /path"), for an owner to approve one by one."""
    from app.assistant import ai
    from app.models import KnowledgeEntry
    from app.services import crm
    from app.services import knowledge as kn
    from app.services.transcribe import _post
    cfg = ai.get_config(db)
    if not cfg.get("key_enc"):
        raise ai.AiFailed("no_key")
    pages = [p for p in json.loads(audit.pages_json) if p.get("text")]
    pages.sort(key=lambda p: (0 if any(k in p["url"] for k in ("/faq", "/contact", "/about", "/services")) else 1, len(p["url"])))
    corpus, total = [], 0
    for p in pages:
        chunk = f"PAGE {urlparse(p['url']).path or '/'}:\n{p['text'][:2500]}"
        if total + len(chunk) > 40000:
            break
        corpus.append(chunk)
        total += len(chunk)
    db.commit()
    content = _post(cfg, {"model": cfg["model"], "temperature": 0.1, "max_tokens": 4000,
                          "messages": [{"role": "system", "content": KNOW_PROMPT}, {"role": "user", "content": "\n\n".join(corpus)}],
                          "response_format": {"type": "json_schema", "json_schema": {"name": "site_knowledge", "strict": True, "schema": KNOW_SCHEMA}}}, timeout=180.0)
    m = re.search(r"\{.*\}", content, re.S)
    items = (json.loads(m.group(0)).get("items") if m else []) or []
    added = 0
    for it in items[:25]:
        title, text = str(it.get("title") or "").strip()[:200], re.sub(r"\s+", " ", str(it.get("text") or "")).strip()[:800]
        if len(title) < 3 or len(text) < 15 or ai.FORBIDDEN.search(text):
            continue
        if db.scalar(select(KnowledgeEntry.id).where(KnowledgeEntry.status == "pending", KnowledgeEntry.title == title).limit(1)):
            continue
        try:
            kn.propose(db, category=it.get("category") or "clinic_info", title=title, body=text, proposed_by="ai", source_kind="manual",
                       source_ref=f"Website: {str(it.get('page') or '/')[:60]}", user=user)
            added += 1
        except crm.CrmError:
            continue
    db.commit()
    return added
