"""Which code a backend runs (backend/version.py): its commit, read when it starts, for signed-in users only."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import version
from backend.api.dependencies import get_current_user
from backend.api.routes import health


def _client(signed_in: bool) -> TestClient:
    app = FastAPI()
    app.include_router(health.router, prefix="/api")
    if signed_in:
        app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "tester"}
    return TestClient(app)


def test_the_running_commit_is_shown_to_a_signed_in_user():
    v = _client(signed_in=True).get("/api/health/version").json()
    assert v == version.current()
    assert v["commit"] and len(v["commit"]) >= 7 and v["started_at"]


def test_it_is_read_once_when_the_backend_starts(monkeypatch):
    first = version.current()
    monkeypatch.setattr(version, "_git", lambda *a: "different")
    assert version.current() == first   # later changes on disk don't change what this process reports


def test_not_for_visitors_and_not_on_the_public_route():
    assert _client(signed_in=False).get("/api/health/version").status_code == 401
    assert "commit" not in _client(signed_in=False).get("/api/health").json()
