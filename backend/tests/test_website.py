"""The website section: crawl, SEO checks, comparisons, and the permissions around it (no network: pages are given)."""
from app.services import website

BASE = "https://site.example"


def page(path, **kw):
    p = {"url": BASE + path, "status": 200, "type": "text/html", "ms": 300, "bytes": 40000, "redirects": 0, "title": f"Page {path} | Walnut Grove Animal Hospital Langley",
         "description": "A description that is long enough to be useful for people searching for this page.", "canonical": BASE + path, "lang": "en",
         "viewport": True, "og_title": True, "og_image": True, "h1": ["One heading"], "imgs_no_alt": 0, "schema": [], "schema_errors": 0, "words": 600, "robots": ""}
    p.update(kw)
    return p


SITE = {"robots_status": 200, "robots": "User-agent: *\nAllow: /", "sitemap_status": 200, "sitemap_urls": 2, "sitemap_hosts": ["site.example"], "sitemap_paths": ["/", "/about"]}


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
