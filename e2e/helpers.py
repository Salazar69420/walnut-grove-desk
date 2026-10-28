"""Shared helpers. Credentials come from a TSV file (WALNUT_E2E_CREDS), never from code."""
import os
from pathlib import Path

BASE = os.environ.get("WALNUT_E2E_URL", "http://127.0.0.1:8001").rstrip("/")
CREDS = Path(os.environ.get("WALNUT_E2E_CREDS", "/tmp/claude-fe/creds.tsv"))


def load_creds():
    out = {}
    for line in CREDS.read_text().splitlines():
        if "\t" in line:
            u, p = line.split("\t", 1)
            out[u.strip()] = p.strip()
    return out


def save_password(user, pw):
    creds = load_creds()
    creds[user] = pw
    CREDS.write_text("".join(f"{u}\t{p}\n" for u, p in creds.items()))
    try:
        CREDS.chmod(0o600)
    except OSError:
        pass


def sign_in(page, user):
    """Sign in, handling the forced first password change. Returns after the app shell shows."""
    creds = load_creds()
    page.goto(BASE + "/login")
    page.get_by_label("Username").fill(user)
    page.get_by_label("Password").fill(creds[user])
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_selector("h1:has-text(\"Choose a new password\"), nav[aria-label=Sections]")
    if page.get_by_role("heading", name="Choose a new password").count():
        new = os.environ.get("WALNUT_E2E_NEWPASS")
        if not new:
            raise RuntimeError("WALNUT_E2E_NEWPASS is needed for the first sign in")
        page.get_by_label("Current password").fill(creds[user])
        page.get_by_label("New password", exact=True).fill(new)
        page.get_by_label("New password again").fill(new)
        page.get_by_role("button", name="Save new password").click()
        save_password(user, new)
    page.wait_for_selector("nav[aria-label=Sections]")
