import os
"""Screenshots of every screen per role into docs/screenshots/app/. Usage: WALNUT_E2E_CREDS=... python e2e/app_shots.py"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from helpers import BASE, sign_in  # noqa: E402

OWNER = os.environ.get("WALNUT_E2E_OWNER", "garg")
DESK = os.environ.get("WALNUT_E2E_DESK", "priya")
NURSE = os.environ.get("WALNUT_E2E_NURSE", "anita")

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots" / "app"
OUT.mkdir(parents=True, exist_ok=True)
VIEWS = {"brief": "Brief", "overnight": "Overnight", "callbacks": "Callbacks", "lab-results": "Lab results",
         "schedule": "Schedule", "after-hours-setup": "After-hours setup", "how-it-fits": "How it fits", "admin": "Admin"}
USERS = {OWNER: OWNER, "desk": DESK, "nurse": NURSE}

with sync_playwright() as p:
    b = p.chromium.launch()
    for tag, user in USERS.items():
        c = b.new_context(viewport={"width": 1400, "height": 1000})
        pg = c.new_page()
        sign_in(pg, user)
        for slug, label in VIEWS.items():
            link = pg.get_by_role("link", name=re.compile("^" + label))
            if not link.count():
                continue
            link.click()
            pg.wait_for_timeout(900)
            pg.screenshot(path=str(OUT / f"{tag}-{slug}.png"), full_page=True)
            if tag == OWNER and slug in ("brief", "overnight", "callbacks"):
                pg.evaluate("document.documentElement.setAttribute('data-theme','dark')")
                pg.screenshot(path=str(OUT / f"{tag}-{slug}-dark.png"), full_page=True)
                pg.evaluate("document.documentElement.removeAttribute('data-theme')")
        if tag == OWNER:
            ph = b.new_context(viewport={"width": 390, "height": 844})
            pp = ph.new_page()
            sign_in(pp, user)
            for slug in ("brief", "overnight", "callbacks"):
                pp.get_by_role("link", name=re.compile("^" + VIEWS[slug])).click()
                pp.wait_for_timeout(700)
                pp.screenshot(path=str(OUT / f"{tag}-{slug}-phone.png"), full_page=True)
            ph.close()
        c.close()
    b.close()
