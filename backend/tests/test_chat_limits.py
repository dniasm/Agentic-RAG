"""How many chat messages one account may send (backend/chat_limits.py): a short window and a day, per user id, counted
only when a run starts, refused (429) before the stream opens or the chat's lock is taken."""
import threading
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import chat_limits
from backend.api.dependencies import get_current_user
from backend.api.routes import chat


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_a_short_window_and_a_day():
    clock = Clock()
    a = chat_limits.Allowance(windows=[(300, 3, False), (86400, 5, True)], clock=clock)
    for _ in range(3):
        a.reserve("u1")
    with pytest.raises(chat_limits.ChatLimited) as e:
        a.reserve("u1")
    assert not e.value.daily and 1 <= e.value.retry_after <= 301
    clock.now += 301                                  # the burst window has passed
    a.reserve("u1")
    a.reserve("u1")
    with pytest.raises(chat_limits.ChatLimited) as e:  # 5 today
        a.reserve("u1")
    assert e.value.daily and e.value.retry_after > 3600
    assert "today" in e.value.message()
    a.reserve("u2")                                   # per account: another user isn't affected


def test_a_refunded_message_doesnt_count():
    a = chat_limits.Allowance(windows=[(300, 1, False)], clock=Clock())
    token = a.reserve("u1")
    a.refund("u1", token)
    a.reserve("u1")                                   # the place came back


def test_a_limit_of_zero_is_off():
    a = chat_limits.Allowance(windows=[(300, 0, False), (86400, 0, True)], clock=Clock())
    for _ in range(100):
        a.reserve("u1")


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "tester"}
    monkeypatch.setattr(chat_limits, "allowance", chat_limits.Allowance(windows=[(300, 2, False)], clock=Clock()))
    monkeypatch.setattr(chat.usage_limits, "is_unlimited", lambda uid: False)   # (no User service in these tests)
    conv = {"id": "c1", "title": "t"}
    final = {"answer": "A.", "tool": "direct", "intent": "direct", "trace": []}
    with mock.patch.object(chat, "_prepare", return_value=(conv, [], [], [], {"conversation_id": "c1"})), \
         mock.patch.object(chat, "run_agent", return_value=final), \
         mock.patch.object(chat, "stream_agent", side_effect=lambda *a, **k: iter([("final", final)])), \
         mock.patch.object(chat, "_persist", return_value={"conversation_id": "c1", "message_id": "m1", "answer": "A.",
                                                            "tool": "direct", "intent": "direct", "latency_ms": 1}):
        yield TestClient(app)


def test_over_the_limit_is_refused_before_the_stream_or_the_lock(client, monkeypatch):
    assert client.post("/chat", json={"message": "one"}).status_code == 200
    assert client.post("/chat/stream", json={"message": "two"}).status_code == 202
    taken = []
    monkeypatch.setattr(chat, "_conversation_lock", lambda conv_id: taken.append(conv_id) or threading.Lock())
    for path in ("/chat", "/chat/stream"):
        r = client.post(path, json={"message": "three"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
        assert "wait" in r.json()["detail"] and "turn_id" not in r.json()
    assert taken == []                                # no lock was taken for a refused message


def test_a_busy_chat_doesnt_use_up_the_allowance(client):
    lock = chat._conversation_lock("busy")
    lock.acquire()
    try:
        with mock.patch.object(chat, "BUSY_WAIT_SECONDS", 0.05):
            for _ in range(3):   # three 409s with an allowance of two
                assert client.post("/chat", json={"message": "x", "conversation_id": "busy"}).status_code == 409
    finally:
        lock.release()
    assert client.post("/chat", json={"message": "now", "conversation_id": "busy"}).status_code == 200


# ---- Allowance.status and GET /api/usage/me


def test_status_counts_left_and_resets_in():
    clock = Clock()
    a = chat_limits.Allowance(windows=[(300, 3, False), (86400, 5, True), (60, 0, False)], clock=clock)
    assert [w["used"] for w in a.status("u1")] == [0, 0] and a.status("u1")[0]["resets_in_seconds"] is None
    a.reserve("u1")
    clock.now += 100
    a.reserve("u1")
    a.reserve("u2")
    short, day = a.status("u1")                         # the limit-0 window is skipped
    assert (short["seconds"], short["allowed"], short["used"], short["left"], short["daily"]) == (300, 3, 2, 1, False)
    assert short["resets_in_seconds"] == 201            # the first message ages out of 300 s in 200 s (+1)
    assert (day["used"], day["left"], day["daily"]) == (2, 3, True)
    assert a.status("u2")[0]["used"] == 1               # per account
    assert a.status("u1", windows=[(300, 2, False)])[0]["left"] == 0   # numbers can come from outside
    clock.now += 301
    assert a.status("u1")[0]["used"] == 0 and a.status("u1")[1]["used"] == 2


@pytest.fixture
def usage_client(monkeypatch):
    from backend.api.routes import usage
    from backend.config.settings import settings
    app = FastAPI()
    app.include_router(usage.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "tester", "is_admin": False}
    a = chat_limits.Allowance(windows=[(300, 2, False)], clock=Clock())
    monkeypatch.setattr(chat_limits, "allowance", a)
    monkeypatch.setattr(usage.usage_limits, "chat_windows", lambda uid: [(300, 2, False)])
    monkeypatch.setattr(usage.usage_limits, "is_unlimited", lambda uid: uid == "admin1")
    monkeypatch.setattr(usage.embed_budget, "used", lambda uid=None, day=None: {"u1": 120, None: 500}[uid])
    monkeypatch.setattr(usage.embed_budget, "site_day", lambda now=None: {"total": None, "used": 0, "left": None})
    monkeypatch.setattr(settings, "embed_per_user_day", 800)
    return TestClient(app), a, usage


def test_usage_me_is_only_the_signed_in_users_own(usage_client):
    client, a, _ = usage_client
    a.reserve("u1")
    a.reserve("someone-else")
    a.reserve("someone-else")
    r = client.get("/usage/me")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["unlimited"] is False
    assert [(w["used"], w["left"], w["allowed"]) for w in body["chat"]] == [(1, 1, 2)]
    assert body["chat"][0]["resets_in_seconds"] == 301
    assert (body["indexing"]["used"], body["indexing"]["left"], body["indexing"]["allowed"]) == (120, 680, 800)
    assert body["indexing"]["resets_at"] and "site_limited" not in body["indexing"]


def test_usage_me_site_quota_nearly_gone(usage_client, monkeypatch):
    client, _a, usage = usage_client
    monkeypatch.setattr(usage.embed_budget, "site_day", lambda now=None: {"total": 1000, "used": 990, "left": 10})
    ix = client.get("/usage/me").json()["indexing"]
    assert ix["site_limited"] is True and ix["left"] == 10


def test_usage_me_survives_the_indexing_numbers_failing(usage_client, monkeypatch):
    client, _a, usage = usage_client

    def boom(*a, **k):
        raise RuntimeError("user service down")
    monkeypatch.setattr(usage.embed_budget, "used", boom)
    r = client.get("/usage/me")
    assert r.status_code == 200 and r.json()["indexing"] is None and len(r.json()["chat"]) == 1


def test_usage_me_needs_a_login(monkeypatch):
    from backend.api.routes import usage
    from backend.library import auth
    monkeypatch.setattr(auth, "user_for", lambda token: None)
    app = FastAPI()
    app.include_router(usage.router)
    assert TestClient(app).get("/usage/me").status_code == 401


def test_an_admin_is_never_limited_or_counted(client, monkeypatch):
    monkeypatch.setattr(chat.usage_limits, "is_unlimited", lambda uid: True)
    for _ in range(5):                                # the window allows 2
        assert client.post("/chat", json={"message": "x"}).status_code == 200
    assert chat_limits.allowance.counts() == {}


def test_usage_me_for_an_admin_says_unlimited(usage_client, monkeypatch):
    client, a, usage = usage_client
    client.app.dependency_overrides[get_current_user] = lambda: {"id": "admin1", "username": "boss", "is_admin": True}
    monkeypatch.setattr(usage.embed_budget, "used", lambda uid=None, day=None: 7)
    monkeypatch.setattr(usage.usage_limits, "chat_windows", lambda uid: [(300, 0, False), (86400, 0, True)])
    monkeypatch.setattr(usage.usage_limits, "limits_for", lambda uid: {"index_per_day": 0})
    body = client.get("/usage/me").json()
    assert body["unlimited"] is True and body["chat"] == []
    assert body["indexing"]["allowed"] is None and body["indexing"]["left"] is None and body["indexing"]["used"] == 7
