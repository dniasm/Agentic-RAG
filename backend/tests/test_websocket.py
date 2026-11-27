"""The WebSocket (backend/api/routes/ws.py) and the event hub behind it (backend/realtime/): login required, several
tabs, replay after a reconnect, publishing from threads, the queue's and the indexing's pushes (no API calls).

    python -m pytest backend/tests/test_websocket.py
"""
import asyncio
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.api.routes import ws
from backend.library import auth, catalog, library
from backend.realtime import hub as hub_module
from backend.realtime import schema
from backend.realtime.hub import Hub

USERS = {"tok-u1": {"id": "u1", "username": "one"}, "tok-u2": {"id": "u2", "username": "two"}}


def cookie(token):
    return {"cookie": f"{auth.COOKIE}={token}"}


@pytest.fixture
def env(monkeypatch):
    hub = Hub()
    monkeypatch.setattr(ws, "hub", hub)
    monkeypatch.setattr(catalog, "hub", hub)
    monkeypatch.setattr(library, "hub", hub)
    monkeypatch.setattr(auth, "user_for", lambda token: USERS.get(token))
    monkeypatch.setattr(auth, "public", lambda user: dict(user))
    app = FastAPI()
    app.include_router(ws.router)
    return TestClient(app), hub


def chat_final(n):
    return {"conversation_id": "c1", "turn_id": "turn-0001", "message_id": f"m{n}", "answer": "A"}


# ---- who may connect

def test_no_login_is_closed_with_4401(env):
    client, _ = env
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws") as sock:
            sock.receive_json()
    assert e.value.code == ws.UNAUTHENTICATED_CLOSE
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws", headers=cookie("forged")) as sock:
            sock.receive_json()
    assert e.value.code == ws.UNAUTHENTICATED_CLOSE


def test_a_page_on_another_site_is_refused_even_with_the_cookie(env):
    client, hub = env
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws", headers={**cookie("tok-u1"), "origin": "https://evil.example"}) as sock:
            sock.receive_json()
    assert e.value.code == ws.POLICY_CLOSE
    assert hub.socket_count("u1") == 0
    with client.websocket_connect("/ws", headers={**cookie("tok-u1"), "origin": "http://testserver"}) as sock:
        assert sock.receive_json()["type"] == "hello"   # the site's own page is let in


def test_hello_names_the_user_and_the_latest_seq(env):
    client, hub = env
    hub.publish("u1", "queue", "changed", {})
    with client.websocket_connect("/ws", headers=cookie("tok-u1")) as sock:
        hello = sock.receive_json()
    assert hello["channel"] == "system" and hello["type"] == "hello" and hello["seq"] == 0
    assert hello["data"]["user"]["id"] == "u1" and hello["data"]["seq"] == 1
    assert hello["request_id"]


def test_ping_is_answered_and_the_server_sends_heartbeats(env, monkeypatch):
    monkeypatch.setattr(ws, "HEARTBEAT_SECONDS", 0.05)
    client, _ = env
    with client.websocket_connect("/ws", headers=cookie("tok-u1")) as sock:
        sock.receive_json()
        assert sock.receive_json()["type"] == "heartbeat"
        sock.send_json({"type": "ping"})
        seen = [sock.receive_json()["type"] for _ in range(3)]
    assert "pong" in seen


# ---- tabs and accounts

def test_two_tabs_both_receive_and_another_account_does_not(env):
    client, hub = env
    with client.websocket_connect("/ws", headers=cookie("tok-u1")) as a, \
         client.websocket_connect("/ws", headers=cookie("tok-u1")) as b, \
         client.websocket_connect("/ws", headers=cookie("tok-u2")) as other:
        for s in (a, b, other):
            s.receive_json()
        assert hub.socket_count("u1") == 2
        hub.publish("u1", "chat", "final", chat_final(1), request_id="req-1")
        hub.publish("u2", "queue", "changed", {})
        for s in (a, b):
            got = s.receive_json()
            assert got["type"] == "final" and got["request_id"] == "req-1" and got["data"]["message_id"] == "m1"
        assert other.receive_json()["channel"] == "queue"   # its own event, not u1's answer
    assert hub.socket_count("u1") == 0   # closing a tab detaches it


# ---- reconnect

def test_a_reconnect_replays_what_it_missed(env):
    client, hub = env
    with client.websocket_connect("/ws", headers=cookie("tok-u1")) as sock:
        last = sock.receive_json()["data"]["seq"]
    hub.publish("u1", "chat", "status", {"conversation_id": "c1", "turn_id": "turn-0001", "node": "n", "label": "L"})
    hub.publish("u1", "chat", "final", chat_final(1))
    with client.websocket_connect(f"/ws?after={last}", headers=cookie("tok-u1")) as sock:
        types = [sock.receive_json()["type"] for _ in range(3)]
    assert types == ["hello", "status", "final"]
    with client.websocket_connect("/ws?after=2", headers=cookie("tok-u1")) as sock:
        assert [sock.receive_json()["type"] for _ in range(1)] == ["hello"]   # nothing newer: nothing replayed


def test_a_cursor_the_log_cannot_serve_gets_resync(env):
    client, hub = env
    small = Hub(max_events=3)
    ws.hub = small
    try:
        for i in range(10):
            small.publish("u1", "queue", "changed", {})
        with client.websocket_connect("/ws?after=2", headers=cookie("tok-u1")) as sock:
            assert [sock.receive_json()["type"] for _ in range(2)] == ["hello", "resync"]
        with client.websocket_connect("/ws?after=99", headers=cookie("tok-u1")) as sock:   # newer than anything: a restart
            assert [sock.receive_json()["type"] for _ in range(2)] == ["hello", "resync"]
    finally:
        ws.hub = hub


def test_only_the_newest_draft_of_a_turn_is_kept_for_replay():
    hub = Hub()
    for text in ("a", "ab", "abc"):
        hub.publish("u1", "chat", "draft", {"conversation_id": "c1", "turn_id": "t-1", "text": text})
    hub.publish("u1", "chat", "draft", {"conversation_id": "c1", "turn_id": "t-2", "text": "other"})

    async def go():
        _, backlog, resync, latest = hub.attach("u1", 0)
        return backlog, resync, latest

    backlog, resync, latest = asyncio.run(go())
    assert [(e["data"]["turn_id"], e["data"]["text"]) for e in backlog] == [("t-1", "abc"), ("t-2", "other")]
    assert not resync and latest == 4


# ---- publishing from threads

def test_many_threads_publishing_reach_the_socket_complete_and_in_order():
    hub = Hub(max_events=10_000)
    threads_n, per_thread = 8, 100

    async def go():
        queue, _, _, _ = hub.attach("u1", None)
        workers = [threading.Thread(target=lambda i=i: [hub.publish("u1", "chat", "status", {
            "conversation_id": "c1", "turn_id": f"turn-{i:04d}", "node": "n", "label": str(k)})
            for k in range(per_thread)]) for i in range(threads_n)]
        for w in workers:
            w.start()
        got = [await asyncio.wait_for(queue.get(), 5) for _ in range(threads_n * per_thread)]
        for w in workers:
            w.join()
        return got

    got = asyncio.run(go())
    assert [e["seq"] for e in got] == list(range(1, threads_n * per_thread + 1))
    for i in range(threads_n):   # each thread's own events kept their order
        labels = [int(e["data"]["label"]) for e in got if e["data"]["turn_id"] == f"turn-{i:04d}"]
        assert labels == list(range(per_thread))


def test_a_socket_that_stops_reading_is_dropped_not_buffered_forever(monkeypatch):
    monkeypatch.setattr(hub_module, "MAX_BACKLOG", 5)
    hub = Hub()

    async def go():
        queue, _, _, _ = hub.attach("u1", None)
        for _ in range(20):
            hub.publish("u1", "queue", "changed", {})
        await asyncio.sleep(0.05)   # the loop runs the deliveries
        items = [queue.get_nowait() for _ in range(queue.qsize())]
        return items

    items = asyncio.run(go())
    assert hub_module.CLOSE in items and len([i for i in items if i is not hub_module.CLOSE]) <= 5


def test_a_malformed_event_is_dropped_without_raising():
    hub = Hub()
    assert hub.publish("u1", "chat", "final", {"answer": "no ids"}) is None
    assert hub.publish("u1", "nope", "nope", {}) is None
    assert hub.latest_seq("u1") == 0
    with pytest.raises(ValueError):
        schema.envelope("chat", "final", {})


# ---- the queue and the indexing push

def test_queue_progress_is_pushed_to_the_owner(env, tmp_path):
    client, hub = env
    created = catalog.add_batch("session-ws", ["tidy the notes", "rename the report"], owner="u1")
    task = created["tasks"][0]["id"]
    with client.websocket_connect("/ws", headers=cookie("tok-u1")) as sock, \
         client.websocket_connect("/ws", headers=cookie("tok-u2")) as other:
        sock.receive_json()
        other.receive_json()
        catalog.update_task(task, status="running")
        catalog.update_task(task, status="done", reply="Done.")
        catalog.cancel_batch(created["id"])
        first, second, third = sock.receive_json(), sock.receive_json(), sock.receive_json()
        assert (first["channel"], first["type"], first["data"]["status"]) == ("queue", "task", "running")
        assert second["data"] | {} == {"batch_id": created["id"], "task_id": task, "status": "done", "reply": "Done."}
        assert (third["channel"], third["type"]) == ("queue", "changed")
        assert hub.latest_seq("u2") == 0   # a batch of u1's is none of u2's business


def test_indexing_stages_are_pushed(env):
    client, hub = env
    with client.websocket_connect("/ws", headers=cookie("tok-u1")) as sock:
        sock.receive_json()
        library._push_doc({"id": "d1", "owner": "u1"}, "reading")
        library._push_doc({"id": "d1", "owner": "u1"}, "indexed", title="Report")
        a, b = sock.receive_json(), sock.receive_json()
    assert (a["channel"], a["type"], a["data"]["stage"]) == ("library", "document", "reading")
    assert b["data"]["title"] == "Report" and b["data"]["doc_id"] == "d1"


def test_close_all_ends_the_sockets_with_a_normal_close(env):
    client, hub = env
    with client.websocket_connect("/ws", headers=cookie("tok-u1")) as sock:
        sock.receive_json()
        hub.close_all()
        with pytest.raises(WebSocketDisconnect) as e:
            sock.receive_json()
    assert e.value.code == ws.GOING_AWAY_CLOSE


# ---- tickets: the socket for a page whose host can't carry the cookie (Vercel in front of Render)

from backend.realtime.tickets import TicketStore  # noqa: E402


@pytest.fixture
def ticketed(env, monkeypatch):
    client, hub = env
    fresh = TicketStore()
    monkeypatch.setattr(ws, "tickets", fresh)
    return client, hub, fresh


def test_a_ticket_needs_a_login(ticketed):
    client, _, _ = ticketed
    assert client.post("/ws/ticket").status_code == 401
    assert client.post("/ws/ticket", headers=cookie("forged")).status_code == 401
    body = client.post("/ws/ticket", headers=cookie("tok-u1")).json()
    assert body["expires_in"] == 60 and len(body["ticket"]) >= 40


def test_a_ticket_opens_one_socket_without_the_cookie_and_only_once(ticketed):
    client, hub, _ = ticketed
    t = client.post("/ws/ticket", headers=cookie("tok-u1")).json()["ticket"]
    with client.websocket_connect(f"/ws?ticket={t}") as sock:
        assert sock.receive_json()["data"]["user"]["id"] == "u1"
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(f"/ws?ticket={t}") as sock:
            sock.receive_json()
    assert e.value.code == ws.UNAUTHENTICATED_CLOSE


def test_a_wrong_or_missing_ticket_is_not_signed_in_even_with_a_cookie(ticketed):
    client, _, _ = ticketed
    for query in ("ticket=nope", "ticket="):
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect(f"/ws?{query}", headers=cookie("tok-u1")) as sock:
                sock.receive_json()
        assert e.value.code == ws.UNAUTHENTICATED_CLOSE


def test_an_expired_ticket_is_refused():
    now = [0.0]
    store = TicketStore(ttl=60, clock=lambda: now[0])
    t = store.issue("u1", "tok-u1")
    now[0] = 59
    assert store.redeem(t) == ("u1", "tok-u1")
    t = store.issue("u1", "tok-u1")
    now[0] = 59 + 61
    assert store.redeem(t) is None


def test_a_ticket_stands_for_its_login_session_and_a_dead_login_gets_nothing(ticketed):
    client, hub, store = ticketed
    t = store.issue("u2", "tok-u1")               # bound to u2, but the login behind it is u1's
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(f"/ws?ticket={t}") as sock:
            sock.receive_json()
    assert e.value.code == ws.UNAUTHENTICATED_CLOSE
    t = store.issue("u1", "tok-ended")            # the login ended after the ticket was issued
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws?ticket={t}") as sock:
            sock.receive_json()


def test_logout_closes_the_socket_a_ticket_opened(ticketed):
    client, hub, _ = ticketed
    t = client.post("/ws/ticket", headers=cookie("tok-u1")).json()["ticket"]
    with client.websocket_connect(f"/ws?ticket={t}") as sock:
        sock.receive_json()
        assert hub.close_session("u1", "tok-u1") == 1
        with pytest.raises(WebSocketDisconnect) as e:
            sock.receive_json()
        assert e.value.code == ws.UNAUTHENTICATED_CLOSE
    t = client.post("/ws/ticket", headers=cookie("tok-u1")).json()["ticket"]
    with client.websocket_connect(f"/ws?ticket={t}") as sock:   # another login's close leaves it open
        sock.receive_json()
        assert hub.close_session("u1", "tok-other") == 0


def test_the_vercel_origin_is_let_in_only_when_cors_origins_lists_it(ticketed, monkeypatch):
    from backend.config.settings import settings
    client, hub, _ = ticketed
    headers = {"origin": "https://app.vercel.app", "host": "api.onrender.com"}
    monkeypatch.setattr(settings, "allowed_hosts", "api.onrender.com")
    monkeypatch.setattr(settings, "cors_origins", "http://localhost:5173")
    t = client.post("/ws/ticket", headers=cookie("tok-u1")).json()["ticket"]
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(f"/ws?ticket={t}", headers=headers) as sock:
            sock.receive_json()
    assert e.value.code == ws.POLICY_CLOSE
    monkeypatch.setattr(settings, "cors_origins", "http://localhost:5173,https://app.vercel.app")
    t = client.post("/ws/ticket", headers=cookie("tok-u1")).json()["ticket"]
    with client.websocket_connect(f"/ws?ticket={t}", headers=headers) as sock:
        assert sock.receive_json()["type"] == "hello"


def test_the_store_caps_what_one_account_can_hold():
    store = TicketStore()
    first = store.issue("u1", "t")
    for _ in range(25):
        store.issue("u1", "t")
    assert store.redeem(first) is None

