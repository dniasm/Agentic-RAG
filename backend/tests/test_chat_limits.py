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
