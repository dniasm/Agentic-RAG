"""Web hardening (backend/security.py, backend/web.py): host and cross-site checks, security headers, the login
cookie's Secure flag, API docs off, and the page's Content-Security-Policy."""
import base64
import hashlib
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import security
from backend.config.settings import settings
from backend.web import content_security_policy

PUBLIC = "https://laptop.tail1234.ts.net"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "allowed_hosts", "*.ts.net")
    monkeypatch.setattr(settings, "api_docs", False)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(security.SecurityMiddleware)

    @app.get("/api/thing")
    def get_thing():
        return {"ok": True}

    @app.post("/api/thing")
    def post_thing():
        return {"ok": True}

    @app.get("/docs")
    def docs():   # stands in for a docs route that exists: the middleware still hides it
        return {"docs": True}

    return TestClient(app)


def test_only_this_sites_names_are_served(client):
    assert client.get("/api/thing", headers={"host": "laptop.tail1234.ts.net"}).status_code == 200
    for local in ("localhost:8000", "127.0.0.1:8000", "192.168.1.87:5173", "[::1]:8000"):
        assert client.get("/api/thing", headers={"host": local}).status_code == 200, local
    for other in ("evil.example.com", "ts.net", "laptop.ts.net.evil.com", ""):
        assert client.get("/api/thing", headers={"host": other}).status_code == 400, other


def test_a_change_from_another_site_is_refused(client):
    public = {"host": "laptop.tail1234.ts.net"}
    assert client.post("/api/thing", headers={**public, "origin": PUBLIC}).status_code == 200
    assert client.post("/api/thing", headers={**public, "origin": "https://evil.example.com"}).status_code == 403
    assert client.post("/api/thing", headers={**public, "referer": "https://evil.example.com/page"}).status_code == 403
    assert client.post("/api/thing", headers={**public, "origin": "null"}).status_code == 403
    assert client.post("/api/thing", headers=public).status_code == 200            # curl: no Origin, no Referer
    # development: the Vite page (another port, a LAN address) through its proxy
    dev = {"host": "127.0.0.1:8000", "origin": "http://192.168.1.87:5173"}
    assert client.post("/api/thing", headers=dev).status_code == 200
    # reading is never refused for its Origin
    assert client.get("/api/thing", headers={**public, "origin": "https://evil.example.com"}).status_code == 200


def test_security_headers_and_hsts_on_the_public_name_only(client):
    public = client.get("/api/thing", headers={"host": "laptop.tail1234.ts.net"})
    assert public.headers["x-content-type-options"] == "nosniff"
    assert public.headers["referrer-policy"] == "same-origin"
    assert public.headers["x-frame-options"] == "DENY"
    assert public.headers["strict-transport-security"] == security.HSTS
    local = client.get("/api/thing", headers={"host": "localhost:8000"})
    assert local.headers["x-content-type-options"] == "nosniff"
    assert "strict-transport-security" not in local.headers


def test_api_docs_are_off_unless_asked_for(client, monkeypatch):
    for host in ("laptop.tail1234.ts.net", "localhost"):   # not decided by the Host header a visitor sends
        assert client.get("/docs", headers={"host": host}).status_code == 404
    monkeypatch.setattr(settings, "api_docs", True)
    assert client.get("/docs", headers={"host": "localhost"}).status_code == 200


def test_the_login_cookie_is_secure_on_the_public_name(library_docs_free_signup):
    r = library_docs_free_signup(PUBLIC)
    assert r.status_code == 200, r.text
    cookie = r.headers["set-cookie"].lower()
    assert "; secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    r = library_docs_free_signup("http://localhost:8000")          # plain-HTTP development
    assert r.status_code == 200, r.text
    assert "; secure" not in r.headers["set-cookie"].lower()


@pytest.fixture
def library_docs_free_signup(monkeypatch):
    """Sign up through the real app at a given address (the first account needs no invite code)."""
    from backend.app import app
    from backend.library import config
    monkeypatch.setattr(config, "SIGNUP_CODE", "let-me-in")

    def sign_up(base_url):
        c = TestClient(app, base_url=base_url)
        return c.post("/api/auth/signup", json={"username": f"user_{uuid.uuid4().hex[:8]}", "password": "correct horse",
                                                "invite": "let-me-in"})
    return sign_up


def test_the_page_policy_allows_only_its_own_inline_script():
    theme = "\n  try { applyTheme(); } catch (e) {}\n"
    html = (f"<head><script>{theme}</script></head><body>"
            '<script type="module" crossorigin src="/assets/index-abc.js"></script></body>')
    policy = content_security_policy(html)
    digest = base64.b64encode(hashlib.sha256(theme.encode()).digest()).decode()
    script_src = next(p for p in policy.split("; ") if p.startswith("script-src"))
    assert script_src == f"script-src 'self' 'sha256-{digest}' https://cdn.jsdelivr.net"
    assert "'unsafe-inline'" not in script_src and "frame-ancestors 'none'" in policy
    assert "connect-src 'self'" in policy and "object-src 'none'" in policy
