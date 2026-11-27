"""Short-lived, single-use tickets for opening the WebSocket when the page's host can't carry it.

Behind a host that proxies REST but not WebSockets (Vercel in front of Render), the browser can't send the login
cookie to the socket's own host. It asks POST /api/ws/ticket (cookie-authenticated, over the proxy) and opens
`wss://<backend>/api/ws?ticket=...` instead. A ticket is random, lives TTL_SECONDS, works once, and is bound to the
account and to the login session that asked for it, so logging out still closes the socket it opened. The store is
in memory (one orchestrator process). Tickets are looked up by their SHA-256 digest, so the comparison that decides
a match is on hashes of an unguessable value, never on the ticket text (nothing to time).
"""
import hashlib
import secrets
import threading
import time
from collections.abc import Callable

TTL_SECONDS = 60.0
MAX_PER_USER = 20      # unredeemed tickets one account may hold; the oldest goes first
MAX_TOTAL = 5000


def _digest(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


class TicketStore:
    def __init__(self, ttl: float = TTL_SECONDS, clock: Callable[[], float] = time.monotonic):
        self.ttl, self._clock = ttl, clock
        self._items: dict[str, tuple[float, str, str]] = {}   # digest -> (expires, user id, login token)
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for key in [k for k, v in self._items.items() if v[0] <= now]:
            del self._items[key]

    def issue(self, user_id: str, token: str) -> str:
        ticket = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            self._purge(now)
            mine = sorted((v[0], k) for k, v in self._items.items() if v[1] == user_id)
            for _, key in mine[:max(0, len(mine) - MAX_PER_USER + 1)]:
                del self._items[key]
            if len(self._items) >= MAX_TOTAL:
                del self._items[min(self._items, key=lambda k: self._items[k][0])]
            self._items[_digest(ticket)] = (now + self.ttl, user_id, token)
        return ticket

    def redeem(self, ticket: str | None) -> tuple[str, str] | None:
        """(user id, login token) for a live ticket, which is used up; None for an unknown, used or expired one."""
        if not ticket or len(ticket) > 256:
            return None
        with self._lock:
            self._purge(self._clock())
            item = self._items.pop(_digest(ticket), None)
        return (item[1], item[2]) if item else None


tickets = TicketStore()
