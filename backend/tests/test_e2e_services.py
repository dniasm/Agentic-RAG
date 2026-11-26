"""The whole split, end to end: the four services run in this process (conftest.py) on their own gRPC ports and the
FastAPI app talks to them like it does in production. One user signs up, opens the WebSocket, uploads a note and
sees it indexed, asks a question and sees the answer arrive, finds the chat listed, and logs out. Only the model
calls are stubbed (no API calls).

    python -m pytest backend/tests/test_e2e_services.py
"""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.api.routes import chat, ws
from conftest import fake_embedding
from backend.library import auth, titles
from services.document import ingestion

NOTE = "Support vector machines\nA support vector machine finds the maximum margin hyperplane between two classes."


def _fake_profile(prompt: str) -> titles.DocProfile:
    first = prompt.split("<<<", 1)[1].strip().splitlines()[0].strip()
    return titles.DocProfile(title=first, summary=first, keywords=[])


def _scripted_agent(*args, **kwargs):
    yield "status", {"node": "router", "label": "Reading your message…"}
    yield "draft", {"node": "direct", "text": "A margin"}
    yield "final", {"answer": "A margin is the gap between the classes.", "tool": "direct", "intent": "direct",
                    "trace": [], "sources": [], "notices": []}


def _until(sock, done, limit=60):
    """Messages from the socket up to and including the first one `done` accepts."""
    seen = []
    for _ in range(limit):
        seen.append(sock.receive_json())
        if done(seen[-1]):
            return seen
    raise AssertionError(f"no matching message in {limit}: {[(m['channel'], m['type']) for m in seen]}")


@pytest.fixture
def clean_accounts(user_db):
    def wipe():
        with user_db.tx() as c:
            c.execute("DELETE FROM users")
    wipe()
    yield
    wipe()


def test_sign_up_upload_chat_and_log_out_through_the_four_services(clean_accounts, monkeypatch):
    monkeypatch.setattr(titles, "_ask_model", _fake_profile)
    monkeypatch.setattr(ingestion, "embed_paced", lambda texts, title: [fake_embedding(t, 768) for t in texts])
    monkeypatch.setattr(chat, "stream_agent", _scripted_agent)
    from backend.app import app
    client = TestClient(app)

    # sign up: the User service issues the login cookie
    r = client.post("/api/auth/signup", json={"username": "e2e_user", "password": "correct horse"})
    assert r.status_code == 200, r.text
    assert client.get("/api/auth/me").json()["username"] == "e2e_user"
    token = client.cookies.get(auth.COOKIE)

    with client.websocket_connect("/api/ws") as sock:
        hello = sock.receive_json()
        assert (hello["channel"], hello["type"]) == ("system", "hello")

        # upload: the file is stored here, the Document service indexes it, progress comes over the socket
        up = client.post("/api/library/documents/upload", files={"files": ("svm.txt", NOTE.encode(), "text/plain")})
        assert up.status_code == 200, up.text
        assert up.json()[0]["status"] == "queued", up.json()
        doc_id = up.json()[0]["document"]["id"]
        events = _until(sock, lambda m: m["channel"] == "library" and m["data"].get("stage") == "indexed")
        assert all(e["data"]["doc_id"] == doc_id for e in events if e["channel"] == "library")
        assert [d["id"] for d in client.get("/api/library/documents").json()] == [doc_id]

        # chat: 202 at once, the answer arrives over the socket and ends with final
        r = client.post("/api/chat/stream", json={"message": "what is a margin?", "turn_id": "turn-e2e-0001"})
        assert r.status_code == 202, r.text
        conversation_id = r.json()["conversation_id"]
        events = _until(sock, lambda m: m["channel"] == "chat" and m["type"] == "final")
        chat_events = [e for e in events if e["channel"] == "chat"]
        assert chat_events[0]["type"] == "meta" and chat_events[-1]["type"] == "final"
        assert chat_events[-1]["data"]["answer"] == "A margin is the gap between the classes."
        assert all(e["data"]["turn_id"] == "turn-e2e-0001" for e in chat_events)

        # the Conversation service kept the chat
        assert [c["id"] for c in client.get("/api/conversations").json()] == [conversation_id]

        # log out: the User service forgets the login
        assert client.post("/api/auth/logout").status_code == 200

    # a new socket with the old cookie, as a stale tab would send it, is refused
    stale = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as refused:
        with stale.websocket_connect("/api/ws", headers={"cookie": f"{auth.COOKIE}={token}"}) as sock:
            sock.receive_json()
    assert refused.value.code == ws.UNAUTHENTICATED_CLOSE


def test_the_health_page_reports_every_service_up_and_one_down(monkeypatch):
    from backend import clients
    from backend.app import app
    client = TestClient(app)
    body = client.get("/api/health/services").json()
    assert body["all_up"] is True and [s["name"] for s in body["services"]] == ["user", "conversation", "memory", "document"]
    real = clients.Clients.probe
    monkeypatch.setattr(clients.Clients, "probe", lambda self, name: {**real(self, name), "status": "down"} if name == "memory" else real(self, name))
    body = client.get("/api/health/services").json()
    assert body["all_up"] is False and [s["name"] for s in body["services"] if s["status"] == "down"] == ["memory"]
