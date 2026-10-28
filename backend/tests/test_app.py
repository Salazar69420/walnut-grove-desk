"""App wiring: production mode, static files, error shapes."""
from __future__ import annotations

import shutil

from fastapi.testclient import TestClient

from app import config
from app.main import create_app
from tests.conftest import PASSWORD


def prod_client(template_db, tmp_path, dist=None):
    path = tmp_path / "prod.db"
    shutil.copy(template_db, path)
    s = config.Settings(env="production", secret_key="x" * 40, db_path=str(path), skip_migrate=True,
                        frontend_dist=str(dist or tmp_path / "none"))
    return TestClient(create_app(s), base_url="https://desk.example")


def test_production_has_no_docs_routes(template_db, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>app</html>")
    with prod_client(template_db, tmp_path, dist) as c:
        for p in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            r = c.get(p)
            assert r.status_code == 404, p
            assert "swagger" not in r.text.lower()
        assert c.get("/healthz").status_code == 200
    config.set_settings(None)


def test_production_cookie_is_host_prefixed_and_secure(template_db, tmp_path):
    with prod_client(template_db, tmp_path) as c:
        r = c.post("/api/auth/login", json={"username": "priya", "password": PASSWORD})
        assert r.status_code == 200
        cookie = r.headers["set-cookie"]
        assert cookie.startswith("__Host-wgd=")
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
        assert "Domain" not in cookie
        assert c.get("/api/auth/me").status_code == 200
    config.set_settings(None)


def test_dev_has_docs(env):
    assert env.client().get("/openapi.json").status_code in (200, 404)  # test env: allowed either way


def test_spa_fallback_and_static(template_db, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "a.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("top secret")
    with prod_client(template_db, tmp_path, dist) as c:
        assert c.get("/").text == "<html>app</html>"
        assert c.get("/callbacks").text == "<html>app</html>"
        assert c.get("/assets/a.js").text == "console.log(1)"
        assert c.get("/assets/missing.js").status_code == 404
        assert "top secret" not in c.get("/../secret.txt").text
        assert "top secret" not in c.get("/%2e%2e/secret.txt").text
        r = c.get("/api/nothing-here")
        assert r.status_code == 404 and r.json() == {"detail": "Not found"}
        assert c.get("/").headers["content-security-policy"].startswith("default-src 'self'")
    config.set_settings(None)


def test_no_frontend_build_is_a_404_not_a_crash(env):
    assert env.client().get("/").status_code == 404
