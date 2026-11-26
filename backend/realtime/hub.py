"""The in-process event hub behind the WebSocket: account id -> its open sockets (several tabs), plus a short log of
recent events per account so a tab that reconnects gets what it missed.

  * publish() may be called from any thread (the chat's answer thread, the library's workers, a request handler):
    it only appends to the log under a lock and hands each socket's asyncio queue the event with
    loop.call_soon_threadsafe, so the event loop does all the sending.
  * Every event gets the account's next seq. A reconnecting tab says the last seq it saw (`?after=`) and gets every
    later event; if that is older than the log reaches (or newer than anything we have: the server restarted) it is
    told `resync` and reads its state over REST instead. attach() takes the log and adds the socket in one step, so
    nothing falls between the replay and the live events.
  * Typing drafts are replaced, not appended: the log keeps only the newest draft of a turn, so a long answer
    can't push its own start (or the final) out of the log.
"""
import asyncio
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from backend.realtime import schema
from services.common.log import get_request_id

log = logging.getLogger(__name__)

MAX_EVENTS = 500          # kept per account
KEEP_SECONDS = 3600       # an account with no open socket is forgotten this long after its last event
MAX_BACKLOG = 2000        # events waiting for one socket that isn't reading: past this it is dropped, and reconnects
CLOSE = object()          # put on a socket's queue to end it (shutdown, or a socket too far behind)


@dataclass
class _Socket:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue


@dataclass
class _Account:
    events: deque
    seq: int = 0
    lost: int = 0      # events up to this seq were pushed out of the log (a replacement of a draft is not a loss)
    sockets: list = field(default_factory=list)
    touched: float = 0.0


def _deliver(sock: _Socket, item) -> None:   # runs on the socket's loop
    if sock.queue.qsize() >= MAX_BACKLOG and item is not CLOSE:
        item = CLOSE
    sock.queue.put_nowait(item)


class Hub:
    def __init__(self, max_events: int = MAX_EVENTS, keep_seconds: float = KEEP_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self.max_events, self.keep_seconds, self._clock = max_events, keep_seconds, clock
        self._accounts: dict[str, _Account] = {}
        self._lock = threading.Lock()

    def _account(self, user_id: str) -> _Account:
        acct = self._accounts.get(user_id)
        if acct is None:
            acct = self._accounts[user_id] = _Account(events=deque(maxlen=self.max_events), touched=self._clock())
        return acct

    def _purge(self) -> None:
        now = self._clock()
        for uid in [u for u, a in self._accounts.items() if not a.sockets and now - a.touched > self.keep_seconds]:
            del self._accounts[uid]

    def latest_seq(self, user_id: str) -> int:
        with self._lock:
            acct = self._accounts.get(user_id)
            return acct.seq if acct else 0

    def socket_count(self, user_id: str) -> int:
        with self._lock:
            acct = self._accounts.get(user_id)
            return len(acct.sockets) if acct else 0

    def publish(self, user_id: str, channel: str, type_: str, data: dict, request_id: str | None = None) -> dict | None:
        """Log the event for the account and hand it to every open socket. Thread-safe. The request id defaults to the
        caller's (the REST request that started the work, carried into its threads by in_context). A message that
        doesn't fit its schema is logged and dropped: a push must never break the work that made it."""
        rid = get_request_id() if request_id is None else request_id
        try:
            env = schema.envelope(channel, type_, data, request_id=rid)
        except ValueError:
            log.exception("Dropped a malformed %s/%s event", channel, type_)
            return None
        with self._lock:
            self._purge()
            acct = self._account(user_id)
            acct.seq += 1
            env["seq"] = acct.seq
            acct.touched = self._clock()
            if (channel, type_) == ("chat", "draft"):   # only the newest draft of a turn is worth replaying
                turn = data.get("turn_id")
                acct.events = deque((e for e in acct.events if not (
                    e["channel"] == "chat" and e["type"] == "draft" and e["data"].get("turn_id") == turn)),
                    maxlen=self.max_events)
            if len(acct.events) == acct.events.maxlen:
                acct.lost = acct.events[0]["seq"]
            acct.events.append(env)
            for sock in list(acct.sockets):
                try:
                    sock.loop.call_soon_threadsafe(_deliver, sock, env)
                except RuntimeError:   # its loop is closed: the socket is gone
                    acct.sockets.remove(sock)
        return env

    def attach(self, user_id: str, after: int | None) -> tuple[asyncio.Queue, list[dict], bool, int]:
        """Add a socket (call from its event loop). Returns (queue of events from now on, events with seq > after,
        resync, latest seq). `after=None` asks for no replay."""
        sock = _Socket(asyncio.get_running_loop(), asyncio.Queue())
        with self._lock:
            self._purge()
            acct = self._account(user_id)
            acct.sockets.append(sock)
            latest = acct.seq
            if after is None:
                return sock.queue, [], False, latest
            if after > latest or after < acct.lost:
                # newer than we have (the server restarted) or older than what the log still holds
                return sock.queue, [], True, latest
            return sock.queue, [e for e in acct.events if e["seq"] > after], False, latest

    def detach(self, user_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            acct = self._accounts.get(user_id)
            if acct:
                acct.sockets = [s for s in acct.sockets if s.queue is not queue]
                acct.touched = self._clock()

    def close_all(self) -> None:
        """Server shutdown: end every socket's loop with a normal close."""
        with self._lock:
            socks = [s for a in self._accounts.values() for s in a.sockets]
        for sock in socks:
            try:
                sock.loop.call_soon_threadsafe(_deliver, sock, CLOSE)
            except RuntimeError:
                pass


hub = Hub()
