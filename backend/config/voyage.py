"""The one Voyage AI client, behind a shared rate limit.

Reranking (services/document/rerank.py, the Document service), conversation-memory recall and episode saves (memory/embeddings.py) all use the same
Voyage account. Its free tier (no payment method) allows 3 requests a minute in total, across all of them. With a
client each, they didn't know about one another: a recall and a save could use up the minute, and the next rerank
got a 429 and fell back to the LLM relevance check.

Each call names its priority:

    RERANK  documents for the answer being written: waits up to VOYAGE_RERANK_MAX_WAIT seconds, then the caller
            falls back to the LLM relevance check (as on any Voyage failure)
    FIND    the document agent looking for a document ("rename the GDPR doc"): never waits; busy means it ranks
            by cosine similarity at once (VOYAGE_FIND_MAX_WAIT, 0)
    RECALL  past conversations for the message being answered: waits up to VOYAGE_RECALL_MAX_WAIT, then the
            message is answered without them
    SAVE    storing an exchange in the background: waits up to VOYAGE_SAVE_MAX_WAIT

Within one limiter a lower priority never goes ahead of a waiting higher one. The account's budget is split between
the two processes that call Voyage: the orchestrator (rerank, find) takes VOYAGE_RERANK_RESERVE requests a minute and
the Memory service (recall, save) the rest of VOYAGE_REQUESTS_PER_MINUTE (see shares()), so together they cannot
exceed it and the reranker always finds a slot. With a payment method Voyage allows far more: raise VOYAGE_REQUESTS_PER_MINUTE.
"""
import threading
import time
from collections import deque
from functools import lru_cache
from typing import Callable, TypeVar

from backend.config.settings import settings

RERANK, FIND, RECALL, SAVE = 0, 1, 2, 3
_NAMES = ("rerank", "find", "recall", "save")

T = TypeVar("T")


class VoyageBusy(RuntimeError):
    """No request slot became free within the caller's wait."""


class RateLimiter:
    """At most `per_minute` requests in any `window` seconds; lower priorities leave `reserve` slots free."""

    def __init__(self, per_minute: int, reserve: int = 1, window: float = 60.0):
        self.per_minute = max(1, per_minute)
        self.reserve = max(0, min(reserve, self.per_minute - 1))
        self.window = window
        self._sent: deque[float] = deque()
        self._waiting = [0] * len(_NAMES)   # callers waiting, per priority
        self._cond = threading.Condition()

    def _free(self, now: float) -> int:
        while self._sent and now - self._sent[0] >= self.window:
            self._sent.popleft()
        return self.per_minute - len(self._sent)

    def acquire(self, priority: int, timeout: float) -> bool:
        """Take a request slot; False if none was free within `timeout` seconds."""
        need = 1 if priority == RERANK else 1 + self.reserve
        deadline = time.monotonic() + timeout
        with self._cond:
            self._waiting[priority] += 1
            try:
                while True:
                    now = time.monotonic()
                    if self._free(now) >= need and not any(self._waiting[:priority]):
                        self._sent.append(now)
                        return True
                    remaining = deadline - now
                    if remaining <= 0:
                        return False
                    next_free = self._sent[0] + self.window - now if self._sent else remaining
                    self._cond.wait(min(remaining, max(next_free, 0.01)))
            finally:
                self._waiting[priority] -= 1
                self._cond.notify_all()


def shares() -> tuple[int, int]:
    """(document share, memory share) of the account's requests per minute: the Document service (rerank and find) gets
    VOYAGE_RERANK_RESERVE, the Memory service (embeddings) the rest, at least 1 each. They are separate processes with
    a limiter each, so together they never exceed VOYAGE_REQUESTS_PER_MINUTE (but for a total below 2)."""
    total = settings.voyage_requests_per_minute
    rerank = min(max(1, settings.voyage_rerank_reserve), max(1, total - 1))
    return rerank, max(1, total - rerank)


_role = "document"


def use_document_share() -> None:
    """Called by the Document service process before its first request: its limiter takes the document (rerank) share."""
    global _role
    _role = "document"
    if hasattr(limiter, "cache_clear"):
        limiter.cache_clear()


def use_memory_share() -> None:
    """Called by the Memory service process before its first request: its limiter takes the memory share."""
    global _role
    _role = "memory"
    if hasattr(limiter, "cache_clear"):
        limiter.cache_clear()


def make_limiter() -> RateLimiter:
    rerank, memory = shares()
    return RateLimiter(memory if _role == "memory" else rerank, reserve=0)


limiter = lru_cache(maxsize=1)(make_limiter)


@lru_cache(maxsize=1)
def client():
    import voyageai

    if not settings.voyage_api_key:
        raise RuntimeError("VOYAGE_API_KEY is missing from the configuration.")
    # No retries inside the client: a retry would be a request the limiter didn't count.
    return voyageai.Client(api_key=settings.voyage_api_key, max_retries=0, timeout=settings.voyage_rerank_timeout)


def call(priority: int, fn: Callable[[object], T]) -> T:
    """Run fn(client) once a request slot is free, or raise VoyageBusy."""
    wait = (settings.voyage_rerank_max_wait, settings.voyage_find_max_wait, settings.voyage_recall_max_wait,
            settings.voyage_save_max_wait)[priority]
    if not limiter().acquire(priority, wait):
        raise VoyageBusy(f"no Voyage request slot free within {wait:g} s ({_NAMES[priority]})")
    from backend.config import api_meter
    api_meter.count("voyage", 0, _NAMES[priority], calls=1)   # sent (whether or not it succeeds)
    return fn(client())
