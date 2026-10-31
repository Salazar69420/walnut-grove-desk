"""The website section: crawl, SEO checks, comparisons, and the permissions around it (no network: pages are given)."""
from app.services import website

BASE = "https://site.example"


def page(path, **kw):
    p = {"url": BASE + path, "status": 200, "type": "text/html", "ms": 300, "bytes": 40000, "redirects": 0, "title": f"Page {path} | Walnut Grove Animal Hospital Langley",
         "description": "A description that is long enough to be useful for people searching for this page.", "canonical": BASE + path, "lang": "en",
         "viewport": True, "og_title": True, "og_image": True, "h1": ["One heading"], "imgs_no_alt": 0, "schema": [], "schema_errors": 0, "words": 600, "robots": "",
         "postal": ["V2Y 1A1"], "maps_link": True, "question_headings": 4, "phones": ["6045550100"]}
    p.update(kw)
    return p


SITE = {"llms_status": 200, "robots_status": 200, "robots": "User-agent: *\nAllow: /", "sitemap_status": 200, "sitemap_urls": 2, "sitemap_hosts": ["site.example"], "sitemap_paths": ["/", "/about"]}


def codes(issues):
    return {i["code"] for i in issues}


def test_a_clean_site_has_only_the_missing_local_schema():
    pages = [page("/"), page("/about", title="About us | Walnut Grove Animal Hospital Langley", description="Who we are: the team and the clinic in Walnut Grove, Langley, since many years.")]
    assert codes(website.check(pages, SITE, BASE, BASE)) == {"no_local_schema"}
    pages[0]["schema"] = ["VeterinaryCare"]
    assert website.check(pages, SITE, BASE, BASE) == [] and website.score([]) == 100


def test_common_problems_are_found():
    pages = [page("/", schema=["VeterinaryCare"], canonical="https://www.site.example/"), page("/about", title=None or "", h1=[], imgs_no_alt=3, words=40),
             page("/faq", status=404), page("/dup", title="Same"), page("/dup2", title="Same", description="")]
    found = codes(website.check(pages, SITE, BASE, BASE))
    assert {"broken_pages", "no_title", "h1_count", "img_alt", "thin", "duplicate_title", "no_description", "canonical_host", "not_in_sitemap"} <= found


def test_audits_are_compared():
    old = [{"code": "a", "count": 3}, {"code": "b", "count": 1}]
    new = [{"code": "a", "count": 5}, {"code": "c", "count": 1}]
    assert website.compare(new, old) == {"new": ["c"], "fixed": ["b"], "worse": ["a"], "better": []}


def test_only_owners_run_audits_and_desk_can_read(env, garg, priya, anita):
    assert garg.get("/api/website").status_code == 200
    assert priya.get("/api/website").status_code in (200, 403)
    assert anita.get("/api/website").status_code == 403  # nurses do not see the website section
    assert priya.post("/api/website/audit", {}).status_code == 403


def test_page_parser_reads_what_search_engines_see():
    import httpx
    html = ('<html lang="en"><head><title>Vaccines | Walnut Grove</title><meta name="description" content="Dog and cat vaccines in Langley.">'
            '<link rel="canonical" href="https://site.example/services/vaccines"><script type="application/ld+json">{"@type":"VeterinaryCare"}</script></head>'
            '<body><h1>Vaccines</h1><p>We offer core vaccines.</p><img src="a.png"><a href="/contact">Contact</a></body></html>')
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html, headers={"content-type": "text/html"})))
    p = website.fetch_page(client, BASE + "/services/vaccines")
    assert p["title"] == "Vaccines | Walnut Grove" and p["h1"] == ["Vaccines"] and p["schema"] == ["VeterinaryCare"] and p["imgs_no_alt"] == 1
    assert BASE + "/contact" in p["links"] and "core vaccines" in p["text"] and p["lang"] == "en"


def test_maps_and_ai_search_checks():
    pages = [page("/", schema=["VeterinaryCare"], local={"name": "Clinic", "telephone": "x"}, phones=["6045550100", "6045550199"], postal=[], maps_link=False, question_headings=0)]
    site = {**SITE, "llms_status": 404, "robots": "User-agent: GPTBot\nDisallow: /\n"}
    found = {i["code"]: i["area"] for i in website.check(pages, site, BASE, BASE)}
    assert found["local_schema_incomplete"] == "maps" and found["nap_phone"] == "maps" and found["no_address_text"] == "maps" and found["no_maps_link"] == "maps"
    assert found["ai_bots_blocked"] == "ai" and found["no_llms_txt"] == "ai" and found["few_questions"] == "ai"
    assert website.score([{"code": "no_llms_txt", "severity": "low"}], "ai") == 98 and website.score([{"code": "no_llms_txt", "severity": "low"}], "maps") == 100


# ---------------------------------------------------------------- deployment agent and blog (GitHub, Sanity and the AI are faked)
import base64
import json

import httpx
import pytest
from sqlalchemy import select

from app.adapters import llm as llm_adapter
from app.models import WebsiteAudit, WebsiteChange
from app.services import blog, deploy_agent, timeutil


class FakeWorld:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.merged = False
        self.created: list[dict] = []

    def gh(self, r: httpx.Request) -> httpx.Response:
        p, m = r.url.path, r.method
        self.calls.append((m, p))
        if p.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base"}})
        if p.endswith("/git/commits/base"):
            return httpx.Response(200, json={"tree": {"sha": "t0"}})
        if "/git/trees/t0" in p:
            return httpx.Response(200, json={"tree": [{"path": "app/layout.tsx", "type": "blob"}, {"path": "app/page.tsx", "type": "blob"}]})
        if "/contents/app/layout.tsx" in p:
            return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(b"export const metadata = { title: 'Walnut Grove' }\n").decode()})
        if "/contents/" in p:
            return httpx.Response(404, json={})
        if p.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "b1"})
        if p.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "t1"})
        if p.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "c1"})
        if p.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if p.endswith("/pulls") and m == "POST":
            return httpx.Response(201, json={"number": 7, "html_url": "https://github.com/x/y/pull/7"})
        if p.endswith("/commits/c1/status"):
            return httpx.Response(200, json={"statuses": [{"state": "success", "context": "Vercel", "target_url": "https://preview.example"}]})
        if p.endswith("/commits/c1/check-runs"):
            return httpx.Response(200, json={"check_runs": []})
        if p.endswith("/pulls/7/merge"):
            self.merged = True
            return httpx.Response(200, json={"merged": True})
        if p.endswith("/pulls/7"):
            return httpx.Response(200, json={"merged": self.merged, "state": "closed" if self.merged else "open"})
        return httpx.Response(404, json={})

    def ai(self, r: httpx.Request) -> httpx.Response:
        body = json.loads(r.content)
        name = body.get("response_format", {}).get("json_schema", {}).get("name")
        if name == "code_changes":
            out = {"summary": "Gave the home page a specific title.", "creates": [{"path": "public/llms.txt", "content": "Walnut Grove Animal Hospital"}],
                   "edits": [{"path": "app/layout.tsx", "find": "title: 'Walnut Grove'", "replace": "title: 'Walnut Grove Animal Hospital | Langley vet'"},
                             {"path": "../etc/passwd", "find": "x", "replace": "y"}]}
        else:
            out = {"title": "Puppy's first visit", "excerpt": "What to expect.", "body": "## Before you come\nBring records.", "category": "Puppy & Kitten Care", "tags": ["puppy"]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(out)}}]})

    def cms(self, r: httpx.Request) -> httpx.Response:
        if "/data/query/" in r.url.path:
            return httpx.Response(200, json={"result": [{"_id": "p1", "title": "Old post", "slug": "old-post"}]})
        if "/data/mutate/" in r.url.path:
            self.created.append(json.loads(r.content))
            return httpx.Response(200, json={"results": []})
        if "/assets/images/" in r.url.path:
            return httpx.Response(200, json={"document": {"_id": "image-abc"}})
        return httpx.Response(404)


@pytest.fixture()
def world(monkeypatch):
    w = FakeWorld()
    monkeypatch.setattr(deploy_agent, "TRANSPORT", httpx.MockTransport(w.gh))
    monkeypatch.setattr(blog, "TRANSPORT", httpx.MockTransport(w.cms))
    monkeypatch.setattr(llm_adapter, "TRANSPORT", httpx.MockTransport(w.ai))
    import threading
    monkeypatch.setattr(threading.Thread, "start", lambda self: None if isinstance(self, threading.Timer) else self.run())  # agent work inline; no re-audit timers
    return w


def _audit(env):
    with env.session() as s:
        a = WebsiteAudit(base_url=BASE, status="done", started_at=timeutil.now_utc(), finished_at=timeutil.now_utc(), pages_json=json.dumps([page("/") | {"text": "Hi"}]),
                         issues_json=json.dumps([{"code": "duplicate_title", "severity": "medium", "title": "t", "fix": "f", "urls": [BASE + "/"], "count": 1, "detail": ""}]),
                         summary_json="{}")
        s.add(a)
        s.commit()
        return a.id


def test_the_agent_opens_a_pull_request_with_only_safe_edits_and_merges_after_approval(env, garg, world):
    assert garg.put("/api/admin/assistant", {"api_key": "sk-or-v1-testkey-1234"}).status_code == 200
    assert garg.put("/api/website/deploy/settings", {"token": "github_pat_" + "x" * 30, "mode": "approve"}).status_code == 200
    aid = _audit(env)
    r = garg.post("/api/website/deploy", {"audit_id": aid})
    assert r.status_code == 202, r.text
    with env.session() as s:
        c = s.scalar(select(WebsiteChange))
        assert c.status == "building" and c.pr_number == 7 and "Langley vet" in c.diff and "llms.txt" in c.diff and "passwd" not in c.diff
        cid = c.id
    assert garg.get(f"/api/website/deploy/{cid}").json()["item"]["status"] == "ready" and not world.merged  # approve mode waits
    assert garg.post(f"/api/website/deploy/{cid}/approve", {}).json()["item"]["status"] == "merged" and world.merged


def test_auto_mode_merges_when_the_build_passes(env, garg, world):
    garg.put("/api/admin/assistant", {"api_key": "sk-or-v1-testkey-1234"})
    garg.put("/api/website/deploy/settings", {"token": "github_pat_" + "x" * 30, "mode": "auto"})
    garg.post("/api/website/deploy", {"audit_id": _audit(env)})
    with env.session() as s:
        c = deploy_agent.refresh(s, s.scalar(select(WebsiteChange)))
        assert c.status == "merged" and world.merged


def test_blog_posts_are_listed_added_and_removed(env, garg, world):
    assert garg.get("/api/website/blog").json()["cms"]["ready"] is False
    assert garg.put("/api/website/blog/settings", {"project_id": "abc123", "dataset": "production", "token": "sk" + "t" * 30}).status_code == 200
    assert garg.get("/api/website/blog").json()["posts"][0]["title"] == "Old post"
    garg.put("/api/admin/assistant", {"api_key": "sk-or-v1-testkey-1234"})
    d = garg.post("/api/website/blog/draft", {"topic": "first puppy visit"}).json()["draft"]
    r = garg.c.post("/api/website/blog", data={"title": d["title"], "excerpt": "What to expect at the first visit.", "body": d["body"] + " " * 60 + "More text here for length.",
                                                "category": d["category"], "tags": "puppy"}, files={"image": ("a.png", b"\x89PNG....", "image/png")},
                    headers={"X-CSRF-Token": garg.csrf})
    assert r.status_code == 201, r.text
    doc = world.created[-1]["mutations"][0]["create"]
    assert doc["_type"] == "blog" and doc["slug"]["current"] == "puppy-s-first-visit" and doc["mainImage"]["asset"]["_ref"] == "image-abc"
    assert doc["content"][0]["style"] == "h2"
    assert garg.delete("/api/website/blog/p1").status_code == 200 and world.created[-1]["mutations"][0] == {"delete": {"id": "p1"}}
