"""Content studio link: signed one-time sign-in links, owner-only access, and frames allowed only for the studio."""
import base64
import hashlib
import hmac
import json
import time

from app.services import studio


def test_sign_in_links_are_signed_short_lived_and_unique(monkeypatch):
    monkeypatch.setenv("WALNUT_CONTENTO_SECRET", "s" * 64)
    monkeypatch.setenv("WALNUT_PUBLIC_URL", "https://desk.example")
    a, b = studio.sso_url("Dr. Garg"), studio.sso_url("Dr. Garg")
    assert a.startswith("https://contento.desk.example/api/desk/sso?t=") and a != b
    token = a.split("t=")[1].split("&")[0]
    payload, sig = token.split(".")
    want = base64.urlsafe_b64encode(hmac.new(b"s" * 64, payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    assert sig == want
    data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert data["scope"] == "contento-admin" and 0 < data["exp"] - time.time() <= 120 and data["name"] == "Dr. Garg"


def test_no_secret_means_no_link(monkeypatch):
    monkeypatch.delenv("WALNUT_CONTENTO_SECRET", raising=False)
    assert studio.sso_url("x") is None and studio.linked() is False


def test_only_owners_open_the_studio_and_share_keys(env, garg, priya, monkeypatch):
    monkeypatch.setattr(studio, "is_up", lambda: False)
    assert garg.get("/api/website/studio").json()["up"] is False
    assert priya.get("/api/website/studio").status_code == 403
    assert priya.post("/api/website/studio/share-key", {}).status_code == 403


def test_frames_only_for_the_studio_and_the_microphone_for_the_desk(env, garg):
    r = garg.get("/api/website/studio")
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "frame-src 'self'" in csp
    assert "microphone=(self)" in r.headers["permissions-policy"]
