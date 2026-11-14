"""The one Voyage AI client, behind a shared rate limit.

Reranking (rag/rerank.py), conversation-memory recall and episode saves (memory/embeddings.py) all use the same
Voyage account. Its free tier (no payment method) allows 3 requests a minute in total, across all of them. With a
client each, they didn't know about one another: a recall and a save could use up the minute, and the next rerank
got a 429 and fell back to the LLM relevance check.

Each call names its priority:

    RERANK  documents for the answer being written: waits up to VOYAGE_RERANK_MAX_WAIT seconds, then the caller
            falls back to the LLM relevance check (as on any Voyage failure)
    RECALL  past conversations for the message being answered: waits up to VOYAGE_RECALL_MAX_WAIT, then the
            message is answered without them
    SAVE    storing an exchange in the background: waits up to VOYAGE_SAVE_MAX_WAIT

RECALL and SAVE only take a slot while VOYAGE_RERANK_RESERVE more are free, and never ahead of a waiting RERANK, so
the reranker always finds a slot. With a payment method Voyage allows far more: raise VOYAGE_REQUESTS_PER_MINUTE.
"""
import threading
import time
from collections import deque
from functools import lru_cache
from typing import Callable, TypeVar

from backend.config.settings import settings

RERANK, RECALL, SAVE = 0, 1, 2
_NAMES = ("rerank", "recall", "save")

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
        self._waiting = [0, 0, 0]
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


@lru_cache(maxsize=1)
def limiter() -> RateLimiter:
    return RateLimiter(settings.voyage_requests_per_minute, settings.voyage_rerank_reserve)


@lru_cache(maxsize=1)
def client():
    import voyageai

    if not settings.voyage_api_key:
        raise RuntimeError("VOYAGE_API_KEY is missing from the configuration.")
    # No retries inside the client: a retry would be a request the limiter didn't count.
    return voyageai.Client(api_key=settings.voyage_api_key, max_retries=0, timeout=settings.voyage_rerank_timeout)


def call(priority: int, fn: Callable[[object], T]) -> T:
    """Run fn(client) once a request slot is free, or raise VoyageBusy."""
    wait = (settings.voyage_rerank_max_wait, settings.voyage_recall_max_wait, settings.voyage_save_max_wait)[priority]
    if not limiter().acquire(priority, wait):
        raise VoyageBusy(f"no Voyage request slot free within {wait:g} s ({_NAMES[priority]})")
    return fn(client())
