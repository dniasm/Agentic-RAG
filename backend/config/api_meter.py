"""How much each outside API key is used: for the owner's admin panel (backend/api/routes/admin.py).

Three providers, one key each except Groq (GROQ_API_KEY ... _4):
  * Groq: every HTTP response is counted per (key, model) by an httpx event hook on the clients that
    llm.groq_chat() builds, and the rate-limit headers Groq sends back (x-ratelimit-*) are kept as the latest
    word on what is left. The hook reads headers only, never the response body, so streaming is untouched.
    Tokens are in the body, so they come from a LangChain callback on each client instead (KeyTokens), which
    knows its key because each client is built for one key.
  * Gemini: embedding requests, counted where they are sent (documents and questions; cache hits aren't sent).
  * Voyage: requests, counted in config/voyage.call by use (rerank, find, recall, save).

The key text is never stored or shown: keys are numbered 1-4 in the order of the settings.

Counting happens in memory; a background timer adds the counts to app.db (api_usage, one row per UTC hour,
provider, key and model) every FLUSH_SECONDS and when the admin panel reads them, so a model call never waits on
a database write. A crash loses at most the last few seconds of counts. The latest headers stay in memory only
(they are stale after a restart until the next call).
"""
import atexit
import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache

log = logging.getLogger(__name__)

FLUSH_SECONDS = 30.0
_HEADERS = ("limit-requests", "remaining-requests", "reset-requests",
            "limit-tokens", "remaining-tokens", "reset-tokens")

_lock = threading.Lock()
_pending: dict[tuple[str, str, int, str], list[int]] = defaultdict(lambda: [0, 0, 0])  # -> [calls, tokens, limited]
_latest: dict[tuple[int, str], dict] = {}       # (key index, model) -> the last rate-limit headers, and when
_header_names: set[str] = set()                 # logged once, so the panel's labels can be checked against them
_timer: threading.Thread | None = None


def hour_of(when: datetime | None = None) -> str:
    """The UTC hour a count belongs to, as 'YYYY-MM-DDTHH'."""
    return (when or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def count(provider: str, key: int, model: str, calls: int = 0, tokens: int = 0, limited: int = 0) -> None:
    """Add to this hour's counts (in memory; flushed to app.db in the background)."""
    if not (calls or tokens or limited):
        return
    with _lock:
        row = _pending[(hour_of(), provider, int(key), model or "?")]
        row[0] += calls
        row[1] += tokens
        row[2] += limited
    _start_timer()


# ---------------------------------------------------------------- Groq: headers (httpx hook) and tokens (callback)
def _on_groq_response(key: int, response) -> None:
    """Count one Groq response and keep its rate-limit headers. Headers only: the body is never touched. Never
    raises: a fault here must not change the model call."""
    try:
        request = response.request
        if not request.url.path.endswith("/chat/completions"):
            return
        try:
            model = json.loads(request.content or b"{}").get("model") or "?"
        except Exception:
            model = "?"
        count("groq", key, model, calls=1, limited=int(response.status_code == 429))
        headers = {h: response.headers.get("x-ratelimit-" + h) for h in _HEADERS}
        if any(headers.values()):
            with _lock:
                _latest[(key, model)] = {**headers, "status": response.status_code, "at": time.time()}
        names = {n.lower() for n in response.headers.keys()
                 if n.lower().startswith("x-ratelimit") or n.lower() == "retry-after"}
        if names - _header_names:
            _header_names.update(names)
            log.info("Groq rate-limit headers seen: %s", ", ".join(sorted(_header_names)))
    except Exception:
        log.debug("Couldn't read a Groq response's headers", exc_info=True)


@lru_cache(maxsize=None)
def groq_http_clients(key: int):
    """(sync, async) HTTP clients for Groq key `key`: the Groq SDK's own default clients (its timeouts and
    connection limits), with only the response hook added. One pair per key, shared by every model on it."""
    import groq

    def sync_hook(response):
        _on_groq_response(key, response)

    async def async_hook(response):
        _on_groq_response(key, response)

    return (groq.DefaultHttpxClient(event_hooks={"response": [sync_hook]}),
            groq.DefaultAsyncHttpxClient(event_hooks={"response": [async_hook]}))


@lru_cache(maxsize=None)
def key_tokens(key: int):
    """A LangChain callback that adds every reply's tokens (prompt + answer) to Groq key `key`'s count."""
    from langchain_core.callbacks import BaseCallbackHandler

    class KeyTokens(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):
            try:
                for generations in response.generations:
                    for g in generations:
                        message = getattr(g, "message", None)
                        usage = getattr(message, "usage_metadata", None) or {}
                        tokens = int(usage.get("total_tokens")
                                     or usage.get("input_tokens", 0) + usage.get("output_tokens", 0) or 0)
                        model = (getattr(message, "response_metadata", None) or {}).get("model_name") or "?"
                        count("groq", key, model, tokens=tokens)
            except Exception:
                log.debug("Couldn't count a reply's tokens", exc_info=True)

    return KeyTokens()


def latest_headers() -> dict[tuple[int, str], dict]:
    with _lock:
        return {k: dict(v) for k, v in _latest.items()}


# ---------------------------------------------------------------- storage
def _ensure(c) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS api_usage (hour TEXT NOT NULL, provider TEXT NOT NULL, "
              "key_index INTEGER NOT NULL, model TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0, "
              "tokens INTEGER NOT NULL DEFAULT 0, limited INTEGER NOT NULL DEFAULT 0, "
              "PRIMARY KEY (hour, provider, key_index, model))")


def flush() -> None:
    """Add the counts kept in memory to app.db."""
    with _lock:
        rows = [(*k, *v) for k, v in _pending.items()]
        _pending.clear()
    if not rows:
        return
    from backend.library import catalog
    try:
        with catalog.tx() as c:
            _ensure(c)
            c.executemany("INSERT INTO api_usage (hour, provider, key_index, model, calls, tokens, limited) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(hour, provider, key_index, model) DO UPDATE SET "
                          "calls = calls + excluded.calls, tokens = tokens + excluded.tokens, "
                          "limited = limited + excluded.limited", rows)
    except Exception:
        log.exception("Couldn't store API usage counts; keeping them for the next try")
        with _lock:
            for hour, provider, key, model, calls, tokens, limited in rows:
                row = _pending[(hour, provider, key, model)]
                row[0] += calls
                row[1] += tokens
                row[2] += limited


def rows_since(hours: int) -> list[dict]:
    """Stored counts (after a flush) for the last `hours` UTC hours, the current one included."""
    flush()
    from backend.library import catalog
    since = hour_of(datetime.now(timezone.utc) - timedelta(hours=hours - 1))
    with catalog.tx() as c:
        _ensure(c)
        found = c.execute("SELECT hour, provider, key_index, model, calls, tokens, limited FROM api_usage "
                          "WHERE hour >= ? ORDER BY hour", (since,)).fetchall()
    return [dict(zip(("hour", "provider", "key", "model", "calls", "tokens", "limited"), r)) for r in found]


def _run_timer() -> None:
    while True:
        time.sleep(FLUSH_SECONDS)
        flush()


def _start_timer() -> None:
    global _timer
    if _timer is None:
        with _lock:
            if _timer is None:
                _timer = threading.Thread(target=_run_timer, name="api-meter", daemon=True)
                _timer.start()


atexit.register(flush)
