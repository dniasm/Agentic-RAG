"""How many chat messages one account may send: a short window (a burst) and a day, both from settings.

The model quota is shared by every account (Groq's daily tokens per model, about 4.3k tokens a message), so one
invited user must not be able to use it all. Counted per user id, never per address. A message counts once it
starts a run: reserve() takes one before the conversation's lock, and the route gives it back (refund) when the
request goes no further (another message still running in that chat, a request it can't use).

The counts are kept by the User service (its user.db, wall-clock times), so a restart of the backend or of the service
keeps them; check-and-take is one transaction there. With the service unreachable a reservation is refused (fail
closed: ChatLimited after 30 s) and status()/counts() answer as if nothing were counted. An Allowance built with a
`clock` (tests) keeps its counts in memory instead.
"""
import logging
import threading
from collections import defaultdict, deque
from dataclasses import dataclass

import grpc

from agentic_rag_proto import user_pb2 as pb
from backend.clients import get_clients
from backend.config.settings import settings

log = logging.getLogger(__name__)
UNAVAILABLE_RETRY_SECONDS = 30


@dataclass
class ChatLimited(Exception):
    """Over an allowance: `retry_after` seconds until a message is allowed again; `daily` if it was the day's."""
    retry_after: int
    daily: bool

    def message(self) -> str:
        if self.daily:
            hours = max(1, round(self.retry_after / 3600))
            return (f"You've reached today's message limit for this account. It frees up again in about "
                    f"{hours} hour{'s' if hours != 1 else ''}.")
        minutes = max(1, round(self.retry_after / 60))
        return (f"That's a lot of messages in a short time. Please wait about {minutes} minute"
                f"{'s' if minutes != 1 else ''} and try again.")


def _row(seconds: int, allowed: int, daily: bool, used: int, oldest_age: float) -> dict:
    return {"seconds": seconds, "allowed": allowed, "used": used, "left": max(allowed - used, 0), "daily": daily,
            "resets_in_seconds": max(1, int(seconds - oldest_age) + 1) if used else None}


class _MemoryStore:
    """Sliding windows of message times per user, in this process. `clock` is injectable for tests."""

    def __init__(self, clock):
        self._clock = clock
        self._sent: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def reserve(self, user_id, windows):
        with self._lock:
            now = self._clock()
            sent = self._sent[user_id]
            longest = max((w for w, n, _d in windows if n > 0), default=0)
            while sent and now - sent[0] >= longest:
                sent.popleft()
            for seconds, allowed, daily in windows:
                if allowed <= 0:
                    continue
                recent = [t for t in sent if now - t < seconds]
                if len(recent) >= allowed:
                    oldest = recent[-allowed]   # the message whose aging out frees a place
                    raise ChatLimited(retry_after=max(1, int(seconds - (now - oldest)) + 1), daily=daily)
            sent.append(now)
            return now

    def status(self, user_id, windows):
        with self._lock:
            now = self._clock()
            sent = self._sent.get(user_id, ())
            out = []
            for seconds, allowed, daily in windows:
                recent = [t for t in sent if now - t < seconds]
                out.append(_row(seconds, allowed, daily, len(recent), now - recent[0] if recent else 0))
            return out

    def counts(self, windows):
        with self._lock:
            now = self._clock()
            return {user: [sum(1 for t in sent if now - t < seconds) for seconds, _n, _d in windows]
                    for user, sent in self._sent.items() if sent}

    def refund(self, user_id, token):
        with self._lock:
            try:
                self._sent[user_id].remove(token)
            except ValueError:
                pass


class _RemoteStore:
    """The same, kept by the User service (ReserveChatMessage, RefundChatMessage, ChatUsage over gRPC)."""

    @staticmethod
    def _pb_windows(windows):
        return [pb.ChatWindow(seconds=s, allowed=n, daily=d) for s, n, d in windows]

    @staticmethod
    def _call(name, request):
        clients = get_clients()
        return clients.call(getattr(clients.user, name), request)

    def reserve(self, user_id, windows):
        try:
            reply = self._call("ReserveChatMessage", pb.ReserveChatMessageRequest(
                user_id=user_id, windows=self._pb_windows(windows)))
        except grpc.RpcError as e:
            log.warning("chat allowance: User service unavailable (%s); refusing the message", e.code().name)
            raise ChatLimited(retry_after=UNAVAILABLE_RETRY_SECONDS, daily=False) from e
        if not reply.ok:
            raise ChatLimited(retry_after=int(reply.retry_after), daily=reply.daily)
        return reply.token

    def status(self, user_id, windows):
        try:
            reply = self._call("ChatUsage", pb.ChatUsageRequest(user_id=user_id, windows=self._pb_windows(windows)))
            usage = list(reply.rows[0].windows) if reply.rows else []
        except grpc.RpcError as e:
            log.warning("chat allowance: User service unavailable (%s); showing nothing counted", e.code().name)
            usage = []
        return [_row(s, n, d, usage[i].used if i < len(usage) else 0,
                     usage[i].oldest_age_seconds if i < len(usage) else 0)
                for i, (s, n, d) in enumerate(windows)]

    def counts(self, windows):
        try:
            reply = self._call("ChatUsage", pb.ChatUsageRequest(windows=self._pb_windows(windows)))
        except grpc.RpcError as e:
            log.warning("chat allowance: User service unavailable (%s); no counts", e.code().name)
            return {}
        return {r.user_id: [w.used for w in r.windows] for r in reply.rows}

    def refund(self, user_id, token):
        if not str(token).isdigit():
            return   # an admin's "not counted" token, or nothing the service issued
        try:
            self._call("RefundChatMessage", pb.RefundChatMessageRequest(user_id=user_id, token=str(token)))
        except grpc.RpcError as e:
            log.warning("chat allowance: could not refund a message (%s)", e.code().name)


class Allowance:
    """Sliding windows of message times per user. The counts are in the User service unless a `clock` is given
    (then in memory, for tests)."""

    def __init__(self, windows=None, clock=None):
        self._windows = windows   # None: read from settings each time (so tests and .env changes apply)
        self._store = _MemoryStore(clock) if clock is not None else _RemoteStore()

    def windows(self) -> list[tuple[int, int, bool]]:
        """[(seconds, messages allowed, is the day's)]: a limit of 0 or less is off."""
        if self._windows is not None:
            return self._windows
        return [(settings.chat_limit_window_seconds, settings.chat_limit_per_window, False),
                (86400, settings.chat_limit_per_day, True)]

    def reserve(self, user_id: str):
        """Take one message for user_id, or raise ChatLimited. Returns the token to refund it with."""
        return self._store.reserve(user_id, self.windows())

    def status(self, user_id: str, windows=None) -> list[dict]:
        """Read only, per active window (limit above 0): {seconds, allowed, used, left, resets_in_seconds, daily}.
        resets_in_seconds: until the oldest counted message leaves the window (None when none is counted)."""
        active = [w for w in (windows if windows is not None else self.windows()) if w[1] > 0]
        return self._store.status(user_id, active)

    def counts(self) -> dict[str, list[int]]:
        """{user_id: [messages in each window of windows(), in order]} for the admin panel (read only)."""
        return self._store.counts(self.windows())

    def refund(self, user_id: str, token) -> None:
        """Give back a reserved message that never started a run."""
        self._store.refund(user_id, token)


allowance = Allowance()
