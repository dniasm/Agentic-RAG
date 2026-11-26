"""A chat answer is produced in a background thread and pushed over the WebSocket; it is saved and delivered
whether or not anyone is listening (no API calls).

    python -m pytest backend/tests/test_stream.py
"""
import threading
import time
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import chat, ws
from backend.library import auth
from backend.realtime.hub import Hub

USERS = {"tok-u1": {"id": "u1", "username": "tester"}, "tok-u2": {"id": "u2", "username": "other"}}
COOKIE = {"cookie": f"{auth.COOKIE}=tok-u1"}


def slow_agent(*args, **kwargs):
    yield "status", {"node": "router", "label": "Reading your message…"}
    time.sleep(0.2)   # whoever is listening may leave while the agent is still working
    yield "step", {"id": "s1", "node": "router", "detail": "done", "data": {}}
    yield "draft", {"node": "direct", "text": "The ans"}
    yield "final", {"answer": "The answer.", "tool": "direct", "intent": "direct", "trace": []}


@pytest.fixture
def env(monkeypatch):
    hub = Hub()
    monkeypatch.setattr(chat, "hub", hub)
    monkeypatch.setattr(ws, "hub", hub)
    monkeypatch.setattr(auth, "user_for", lambda token: USERS.get(token))
    monkeypatch.setattr(auth, "public", lambda user: dict(user))
    app = FastAPI()
    app.include_router(chat.router)
    app.include_router(ws.router)
    saved = threading.Event()
    states = []

    def fake_persist(conv, state, *args, **kwargs):
        states.append(state)
        saved.set()
        return {"conversation_id": conv["id"], "message_id": "m1", "answer": state["answer"]}

    conv = {"id": "c1", "title": "t"}
    with mock.patch.object(chat, "_prepare", return_value=(conv, [], [], [], {"conversation_id": "c1"})), \
         mock.patch.object(chat, "stream_agent", side_effect=slow_agent), \
         mock.patch.object(chat, "_persist", side_effect=fake_persist):
        client = TestClient(app, headers=COOKIE)
        yield client, hub, saved, states
        for t in threading.enumerate():   # a turn still running must not publish into the next test's hub
            if t.name.startswith("chat-"):
                t.join(5)


def test_the_post_answers_at_once_and_the_events_arrive_in_order_over_the_socket(env):
    client, hub, saved, _ = env
    with client.websocket_connect("/ws") as sock:
        assert sock.receive_json()["type"] == "hello"
        r = client.post("/chat/stream", json={"message": "hi", "turn_id": "turn-abc-123"})
        assert r.status_code == 202
        assert r.json() == {"conversation_id": "c1", "title": "t", "turn_id": "turn-abc-123"}
        events = []
        while not events or events[-1]["type"] != "final":
            events.append(sock.receive_json())
    assert [e["type"] for e in events] == ["meta", "status", "step", "draft", "final"]
    assert all(e["channel"] == "chat" and e["data"]["turn_id"] == "turn-abc-123" and e["data"]["conversation_id"] == "c1"
               for e in events)
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    assert events[-1]["data"]["answer"] == "The answer."


def test_a_turn_id_is_made_when_the_browser_sends_none_and_a_bad_one_is_refused(env):
    client, *_ = env
    assert len(client.post("/chat/stream", json={"message": "hi"}).json()["turn_id"]) == 32
    assert client.post("/chat/stream", json={"message": "hi", "turn_id": "no spaces!"}).status_code == 422


def test_the_answer_is_saved_with_nobody_connected(env):
    client, hub, saved, states = env
    assert client.post("/chat/stream", json={"message": "hi"}).status_code == 202
    assert saved.wait(timeout=5), "the answer was not saved with no socket open"
    assert states[0]["answer"] == "The answer."
    assert hub.latest_seq("u1") >= 5   # and its events are kept for a tab that connects later


def test_an_agent_that_fails_pushes_an_error_event_and_the_saved_error_answer(env):
    client, hub, saved, states = env
    with mock.patch.object(chat, "stream_agent", side_effect=RuntimeError("no keys")):
        with client.websocket_connect("/ws") as sock:
            sock.receive_json()
            client.post("/chat/stream", json={"message": "hi"})
            types = []
            while "final" not in types:
                types.append(sock.receive_json()["type"])
    assert types == ["meta", "error", "step", "final"]
