"""Take reference screenshots of the Walnut Grove Desk prototype.

Opens reference/mock-reference.html and captures full-page screenshots:
  - 1440x1000, light mode: every role (garg, desk, nurse) x every sidebar view
  - 390x844 phone size: owner (garg) x every view
  - 1440x1000 dark mode (color_scheme='dark'): owner x every view

Output: docs/screenshots/reference/<role>-<view>[-phone|-dark].png

Google Fonts is loaded from the network by default. If that hangs in an
offline environment, set WALNUT_BLOCK_FONTS=1 to abort font requests so the
page falls back to system fonts.

Run with the tools venv:
    /opt/walnut-tools/.venv/bin/python /opt/walnut-desk/e2e/reference_shots.py
"""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

REF_URL = Path("/opt/walnut-desk/reference/mock-reference.html").as_uri()
OUT_DIR = Path("/opt/walnut-desk/docs/screenshots/reference")

# (role key used by the page, label in the "Viewing as" switcher, file slug)
ROLES = [
    ("garg", "Dr. Garg", "garg"),
    ("desk", "Front desk", "desk"),
    ("nurse", "Nurse", "nurse"),
]

# (data-v value used by the sidebar nav, label in the sidebar, file slug)
VIEWS = [
    ("brief", "Brief", "brief"),
    ("overnight", "Overnight", "overnight"),
    ("callbacks", "Callbacks", "callbacks"),
    ("results", "Lab results", "lab-results"),
    ("schedule", "Schedule", "schedule"),
    ("setup", "After-hours setup", "after-hours-setup"),
    ("tools", "How it fits", "how-it-fits"),
]

DESKTOP = {"width": 1440, "height": 1000}
PHONE = {"width": 390, "height": 844}


def block_fonts_if_requested(page):
    if os.environ.get("WALNUT_BLOCK_FONTS") == "1":
        for pattern in ("**/fonts.googleapis.com/**", "**/fonts.gstatic.com/**"):
            page.route(pattern, lambda route: route.abort())


def open_page(context):
    page = context.new_page()
    block_fonts_if_requested(page)
    page.goto(REF_URL, wait_until="load", timeout=60000)
    page.wait_for_selector("#side .nav")
    return page


def switch_role(page, role_key):
    page.click(f'#roles button[data-r="{role_key}"]')
    page.wait_for_selector(f'#roles button[data-r="{role_key}"][aria-pressed="true"]')


def open_view(page, view_key):
    page.click(f'#side button[data-v="{view_key}"]')
    page.wait_for_selector(f'#side button[data-v="{view_key}"][aria-current="page"]')
    page.evaluate("document.fonts.ready")


def shoot(page, path):
    page.screenshot(path=str(path), full_page=True)
    print(f"wrote {path}")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            # Desktop, light mode: every role and every view.
            ctx = browser.new_context(viewport=DESKTOP, reduced_motion="reduce")
            page = open_page(ctx)
            for role_key, _label, role_slug in ROLES:
                switch_role(page, role_key)
                for view_key, _vlabel, view_slug in VIEWS:
                    open_view(page, view_key)
                    shoot(page, OUT_DIR / f"{role_slug}-{view_slug}.png")
            ctx.close()

            # Phone size, owner only.
            ctx = browser.new_context(viewport=PHONE, reduced_motion="reduce")
            page = open_page(ctx)
            switch_role(page, "garg")
            for view_key, _vlabel, view_slug in VIEWS:
                open_view(page, view_key)
                shoot(page, OUT_DIR / f"garg-{view_slug}-phone.png")
            ctx.close()

            # Dark mode, owner only.
            ctx = browser.new_context(
                viewport=DESKTOP, color_scheme="dark", reduced_motion="reduce"
            )
            page = open_page(ctx)
            switch_role(page, "garg")
            for view_key, _vlabel, view_slug in VIEWS:
                open_view(page, view_key)
                shoot(page, OUT_DIR / f"garg-{view_slug}-dark.png")
            ctx.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
