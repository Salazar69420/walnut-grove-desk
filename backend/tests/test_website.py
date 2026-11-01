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


def _tarball(files: dict[str, bytes]) -> bytes:
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for path, data in files.items():
            info = tarfile.TarInfo("walnut-grove-bd16e31/" + path)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeWorld:
    """A fake Vercel API and a fake public code download (codeload)."""
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.created: list[dict] = []
        self.deploys: list[dict] = []
        self.uploaded: set[str] = set()
        self.live = "dpl_git"
        self.state = "READY"
        self.downloads = 0

    def gh(self, r: httpx.Request) -> httpx.Response:
        p, m = r.url.path, r.method
        self.calls.append((m, p))
        if r.url.host == "api.vercel.com" and r.url.params.get("slug") not in (None, "walnut-groves-projects") or r.url.params.get("teamId") not in (None, "team_1"):
            return httpx.Response(403, json={"error": {"code": "forbidden", "message": "Not your team"}})
        if r.url.host == "codeload.github.com":
            self.downloads += 1
            return httpx.Response(200, content=_tarball({"app/layout.tsx": b"export const metadata = { title: 'Walnut Grove' }\n", "app/page.tsx": b"export default function P() { return null }\n",
                                                        "package.json": b"{}", ".env": b"SECRET=1", "node_modules/x/index.js": b"x"}))
        if r.headers.get("authorization") != "Bearer vcp_" + "x" * 30:
            return httpx.Response(403, json={"error": {"code": "forbidden", "message": "Not authorized"}})
        if p == "/v1/query/web-analytics/visits/aggregate":
            by = r.url.params.get("by")
            if by == "day":
                from datetime import date, timedelta
                today = date.today()
                return httpx.Response(200, json={"data": [{"timestamp": f"{(today - timedelta(days=i)).isoformat()}T00:00:00.000Z", "pageviews": 10 + i, "visitors": 5} for i in range(40)]})
            rows = {"requestPath": [{"requestPath": "/", "pageviews": 50, "visitors": 30}, {"requestPath": "/studio/desk", "pageviews": 99, "visitors": 2}, {"requestPath": "/contact", "pageviews": 20, "visitors": 15}],
                    "referrerHostname": [{"referrerHostname": "google.com", "pageviews": 40, "visitors": 25}, {"referrerHostname": None, "pageviews": 30, "visitors": 20}]}.get(by, [])
            return httpx.Response(200, json={"data": rows})
        if p in ("/v9/projects/prj_1", "/v9/projects/walnut-grove"):
            return httpx.Response(200, json={"id": "prj_1", "name": "walnut-grove", "framework": "nextjs"})
        if p == "/v9/projects/prj_1/domains":
            return httpx.Response(200, json={"domains": [{"name": "walnut-grove.vercel.app"}, {"name": "walnutgroveanimalclinic.com", "redirect": "www.walnutgroveanimalclinic.com"}, {"name": "www.walnutgroveanimalclinic.com"}]})
        if p == "/v6/deployments":
            meta = {"githubCommitOrg": "xyberdeploymentsca-eng", "githubCommitRepo": "walnut-grove", "githubCommitSha": "bd16e3113e"} if self.live == "dpl_git" else {"walnutDesk": "seo-agent"}
            return httpx.Response(200, json={"deployments": [{"uid": self.live, "meta": meta}]})
        if p == "/v2/files" and m == "POST":
            self.uploaded.add(r.headers["x-vercel-digest"])
            return httpx.Response(200, json={})
        if p == "/v13/deployments" and m == "POST":
            body = json.loads(r.content)
            missing = [f["sha"] for f in body["files"] if f["sha"] not in self.uploaded]
            if missing:
                return httpx.Response(400, json={"error": {"code": "missing_files", "message": "Missing files", "missing": missing}})
            self.deploys.append(body)
            dep = f"dpl_{len(self.deploys)}"
            if body.get("target") == "production":
                self.live = dep
            return httpx.Response(200, json={"id": dep, "url": f"walnut-grove-{dep}.vercel.app", "inspectorUrl": f"https://vercel.com/i/{dep}"})
        if p.startswith("/v13/deployments/"):
            return httpx.Response(200, json={"readyState": self.state})
        return httpx.Response(404, json={"error": {"code": "not_found"}})

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


TOKEN = "vcp_" + "x" * 30


def _connect(garg, mode="approve"):
    garg.put("/api/admin/assistant", {"api_key": "sk-or-v1-testkey-1234"})
    return garg.put("/api/website/deploy/settings", {"token": TOKEN, "team_id": "team_1", "project_id": "prj_1", "mode": mode})


def test_connecting_vercel_checks_the_project_and_points_the_audit_at_its_domain(env, garg, world):
    bad = garg.put("/api/website/deploy/settings", {"token": "vcp_" + "y" * 30, "team_id": "team_1", "project_id": "prj_1"})
    assert bad.status_code == 400 and "token" in bad.json()["detail"]
    r = garg.put("/api/website/deploy/settings", {"token": TOKEN, "team_id": "walnut-groves-projects", "project_id": "walnut-grove"})  # the names from the address bar
    assert r.status_code == 200, r.text
    s = r.json()["settings"]
    assert s["project_id"] == "prj_1"  # stored as the id Vercel gave back
    assert s["connected"] and s["project_name"] == "walnut-grove" and s["domains"][0] == "www.walnutgroveanimalclinic.com" and "walnutgroveanimalclinic.com" not in s["domains"][1:]
    assert "token_enc" not in json.dumps(r.json()) and TOKEN not in json.dumps(r.json())
    assert garg.get("/api/website").json()["settings"]["url"] == "https://www.walnutgroveanimalclinic.com"


def test_the_agent_deploys_a_preview_with_only_safe_edits_and_ships_it_after_approval(env, garg, world):
    assert _connect(garg).status_code == 200
    r = garg.post("/api/website/deploy", {"audit_id": _audit(env)})
    assert r.status_code == 202, r.text
    with env.session() as s:
        c = s.scalar(select(WebsiteChange))
        assert c.status == "building" and c.head_sha == "dpl_1" and "Langley vet" in c.diff and "llms.txt" in c.diff and "passwd" not in c.diff
        cid = c.id
    preview = world.deploys[0]
    paths = {f["file"] for f in preview["files"]}
    assert "target" not in preview and preview["project"] == "prj_1" and {"app/layout.tsx", "public/llms.txt", "package.json"} <= paths
    assert not any(p.startswith((".env", "node_modules")) for p in paths) and world.downloads == 1
    item = garg.get(f"/api/website/deploy/{cid}").json()["item"]
    assert item["status"] == "ready" and item["preview_url"] == "https://walnut-grove-dpl_1.vercel.app" and len(world.deploys) == 1  # approve mode waits
    assert garg.post(f"/api/website/deploy/{cid}/approve", {}).json()["item"]["status"] == "deploying"
    assert world.deploys[1]["target"] == "production" and world.deploys[1]["files"] == preview["files"]
    assert garg.get(f"/api/website/deploy/{cid}").json()["item"]["status"] == "merged"
    # the next change starts from the agent's own copy of what is live: no second download
    # (the fake AI repeats the same edit, which is already live, so nothing new is deployed)
    garg.post("/api/website/deploy", {"audit_id": _audit(env)})
    with env.session() as s:
        last = s.scalars(select(WebsiteChange).order_by(WebsiteChange.id.desc())).first()
        assert last.status == "failed" and "could be applied safely" in last.error
    assert world.downloads == 1 and len(world.deploys) == 2


@pytest.fixture()
def vercel_only(monkeypatch):
    """Fake Vercel without running background threads inline (the analytics page has none, and several users call it)."""
    w = FakeWorld()
    monkeypatch.setattr(deploy_agent, "TRANSPORT", httpx.MockTransport(w.gh))
    from app.services import web_analytics
    web_analytics._cache.clear()
    return w


def test_website_analytics_dashboard_and_daily_copy(env, garg, priya, anita, vercel_only):
    assert garg.get("/api/website/analytics").json() == {"connected": False}
    _connect(garg)
    assert garg.get("/api/website/analytics?days=12").status_code == 400
    d = garg.get("/api/website/analytics?days=7").json()
    assert d["connected"] and d["site"] == "www.walnutgroveanimalclinic.com" and len(d["series"]) == 7
    assert d["totals"]["visitors"] == 35 and d["totals"]["pageviews"] == sum(10 + i for i in range(7)) and d["previous"]["visitors"] == 35
    assert [x["name"] for x in d["top"]["pages"]] == ["/", "/contact"]  # Sanity Studio (staff) is not a visitor page
    assert d["top"]["referrers"][1]["name"] == "Direct or unknown"
    assert priya.get("/api/website/analytics").status_code == 200 and anita.get("/api/website/analytics").status_code == 403
    from app.models import WebAnalyticsDay
    from app.services import web_analytics
    with env.session() as s:
        assert web_analytics.sync(s) > 30 and s.scalar(select(WebAnalyticsDay).limit(1)) is not None
    assert garg.get("/api/website/analytics?days=90").json()["series"][-1]["known"]


def test_auto_mode_ships_when_the_preview_builds_and_a_failed_build_never_does(env, garg, world):
    _connect(garg, mode="auto")
    garg.post("/api/website/deploy", {"audit_id": _audit(env)})
    with env.session() as s:
        c = deploy_agent.refresh(s, s.scalar(select(WebsiteChange)))
        assert c.status == "deploying"
        c = deploy_agent.refresh(s, c)
        assert c.status == "merged"
    world.state = "ERROR"
    garg.post("/api/website/deploy", {"audit_id": _audit(env)})
    with env.session() as s:
        c = s.scalars(select(WebsiteChange).order_by(WebsiteChange.id.desc())).first()
        c = deploy_agent.refresh(s, c)
        assert c.status == "failed" and not any(d.get("target") == "production" for d in world.deploys[2:])


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
