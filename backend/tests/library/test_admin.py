"""The owner's Admin page (backend/api/routes/admin.py) and the API meter behind it (backend/config/api_meter.py):
only listed accounts see it, a listed name can't be signed up for, Groq calls are counted per key from headers and
callbacks without touching the answer path, and the counts survive a restart."""
import json
import re
from pathlib import Path

import groq
import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import api_meter, gemini, llm
from backend.config.settings import settings
from backend.library import config, embed_budget

INVITE = "let-me-in-42"
SECRET = "gsk_test_secret_key_never_shown"


@pytest.fixture(autouse=True)
def fresh_meter(monkeypatch, user_db):
    monkeypatch.setattr(api_meter, "FLUSH_SECONDS", 3600.0)
    with api_meter._lock:
        api_meter._pending.clear()
        api_meter._latest.clear()
    with user_db.tx() as c:
        c.execute("DELETE FROM api_usage")
    yield
    with api_meter._lock:
        api_meter._pending.clear()
        api_meter._latest.clear()


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_usernames", "Tester, someone-else")


def browser():
    from backend.app import app
    return TestClient(app)


# ---------------------------------------------------------------- access
def test_signed_out_and_non_admins_get_404(signed_in, monkeypatch):
    assert browser().get("/api/admin/usage").status_code == 404
    monkeypatch.setattr(settings, "admin_usernames", "")
    assert signed_in.get("/api/admin/usage").status_code == 404
    monkeypatch.setattr(settings, "admin_usernames", "somebody")
    assert signed_in.get("/api/admin/usage").status_code == 404
    assert signed_in.get("/api/auth/me").json()["is_admin"] is False


def test_a_listed_account_sees_it_uncached(signed_in, admin):
    r = signed_in.get("/api/admin/usage")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert set(body) >= {"groq", "gemini", "voyage", "accounts"}
    assert len(body["groq"]["keys"]) == max(1, len(llm.groq_keys()))
    assert body["gemini"]["site_limit"] == embed_budget.site_day()["total"]
    assert len(body["gemini"]["keys"]) == gemini.key_count()
    assert signed_in.get("/api/auth/me").json()["is_admin"] is True     # names are compared without case
    assert browser().get("/api/auth/state").json()["user"] is None


def test_a_listed_name_cant_be_signed_up_for(signed_in, admin, monkeypatch, user_db):
    monkeypatch.setattr(config, "SIGNUP_CODE", INVITE)
    r = browser().post("/api/auth/signup",
                       json={"username": "SOMEONE-ELSE", "password": "correct horse", "invite": INVITE})
    assert r.status_code == 409
    assert user_db.connection().execute("SELECT 1 FROM users WHERE username = ?", ("someone-else",)).fetchone() is None


def test_the_panel_counts_accounts_without_content(signed_in, admin):
    from backend import chat_limits
    chat_limits.allowance.reserve(signed_in.user["id"])
    rows = signed_in.get("/api/admin/usage").json()["accounts"]["rows"]
    assert rows == [{"username": "tester", "chat": [1, 1], "embedded_today": 0}]


# ---------------------------------------------------------------- the Groq meter
def completion(model="openai/gpt-oss-20b", tokens=(11, 4)):
    return {"id": "x", "object": "chat.completion", "created": 1, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1], "total_tokens": sum(tokens)}}


HEADERS = {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "998",
           "x-ratelimit-reset-requests": "2m52.8s", "x-ratelimit-limit-tokens": "8000",
           "x-ratelimit-remaining-tokens": "7985", "x-ratelimit-reset-tokens": "112ms"}


def metered(monkeypatch, handler):
    """groq_chat's clients, on a fake transport (no network), with the same hooks as the real ones."""
    def clients(key):
        def hook(response):
            api_meter._on_groq_response(key, response)

        async def ahook(response):
            api_meter._on_groq_response(key, response)
        return (groq.DefaultHttpxClient(transport=httpx.MockTransport(handler), event_hooks={"response": [hook]}),
                groq.DefaultAsyncHttpxClient(transport=httpx.MockTransport(handler),
                                             event_hooks={"response": [ahook]}))
    monkeypatch.setattr(api_meter, "groq_http_clients", clients)
    monkeypatch.setattr(llm, "api_key", lambda key: SECRET)


def test_a_call_is_counted_on_its_key_with_tokens_and_headers(monkeypatch):
    metered(monkeypatch, lambda request: httpx.Response(200, json=completion(), headers=HEADERS))
    reply = llm.groq_chat(2, model="openai/gpt-oss-20b", max_retries=0).invoke("hi")
    assert reply.content == "hello"
    rows = api_meter.rows_since(1)
    assert [(r["provider"], r["key"], r["model"], r["calls"], r["tokens"]) for r in rows] == \
        [("groq", 2, "openai/gpt-oss-20b", 1, 15)]
    seen = api_meter.latest_headers()[(2, "openai/gpt-oss-20b")]
    assert seen["remaining-requests"] == "998" and seen["reset-tokens"] == "112ms"
    assert "x-ratelimit-remaining-tokens" in api_meter._header_names
    stored = json.dumps([rows, {str(k): v for k, v in api_meter.latest_headers().items()}])
    assert SECRET not in stored                                                   # the key text is never kept


def test_a_rate_limited_response_is_counted_as_limited(monkeypatch):
    err = {"error": {"message": "Rate limit reached. Please try again in 7.5s.", "type": "tokens",
                     "code": "rate_limit_exceeded"}}
    metered(monkeypatch, lambda request: httpx.Response(429, json=err, headers=HEADERS))
    with pytest.raises(groq.RateLimitError):
        llm.groq_chat(0, model="openai/gpt-oss-120b", max_retries=0).invoke("hi")
    (row,) = api_meter.rows_since(1)
    assert (row["calls"], row["limited"], row["tokens"]) == (1, 1, 0)


def test_a_fault_in_the_meter_changes_nothing_for_the_caller(monkeypatch):
    metered(monkeypatch, lambda request: httpx.Response(200, json=completion(), headers=HEADERS))

    def broken(*args, **kwargs):
        raise RuntimeError("meter broke")
    monkeypatch.setattr(api_meter, "count", broken)
    assert llm.groq_chat(1, model="openai/gpt-oss-20b", max_retries=0).invoke("hi").content == "hello"


def test_a_streamed_response_still_arrives_in_pieces():
    pieces = [b"data: one\n\n", b"data: two\n\n", b"data: [DONE]\n\n"]

    def handler(request):
        return httpx.Response(200, headers=HEADERS, content=iter(pieces))

    client = groq.DefaultHttpxClient(transport=httpx.MockTransport(handler),
                                     event_hooks={"response": [lambda r: api_meter._on_groq_response(3, r)]})
    body = json.dumps({"model": "openai/gpt-oss-120b", "stream": True}).encode()
    with client.stream("POST", "https://api.groq.com/openai/v1/chat/completions", content=body) as r:
        got = list(r.iter_raw())
    assert got == pieces                                   # the hook didn't read (or join) the body
    assert api_meter.rows_since(1)[0]["calls"] == 1


def test_counts_survive_a_restart():
    api_meter.count("gemini", 0, "questions", calls=2)
    api_meter.count("voyage", 0, "rerank", calls=1)
    api_meter.flush()
    got = {(r["provider"], r["model"]): r["calls"] for r in api_meter.rows_since(24)}
    assert got == {("gemini", "questions"): 2, ("voyage", "rerank"): 1}


def test_nothing_else_builds_a_groq_client():
    backend = Path(__file__).resolve().parents[2]
    offenders = [str(p.relative_to(backend)) for p in backend.rglob("*.py")
                 if "tests" not in p.parts and re.search(r"\bChatGroq\(", p.read_text(encoding="utf-8"))]
    assert offenders == [str(Path("config/llm.py"))]
