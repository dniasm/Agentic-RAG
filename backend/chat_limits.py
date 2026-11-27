"""How many chat messages one account may send: a short window (a burst) and a day, both from settings.

The model quota is shared by every account (Groq's daily tokens per model, about 4.3k tokens a message), so one
invited user must not be able to use it all. Counted per user id, never per address. A message counts once it
starts a run: reserve() takes one before the conversation's lock, and the route gives it back (refund) when the
request goes no further (another message still running in that chat, a request it can't use).

Kept in memory: one backend process, and a restart clears the counts.
"""
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from backend.config.settings import settings


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


class Allowance:
    """Sliding windows of message times per user. `clock` is injectable for tests."""

    def __init__(self, windows=None, clock=time.monotonic):
        self._windows = windows   # None: read from settings each time (so tests and .env changes apply)
        self._clock = clock
        self._sent: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def windows(self) -> list[tuple[int, int, bool]]:
        """[(seconds, messages allowed, is the day's)]: a limit of 0 or less is off."""
        if self._windows is not None:
            return self._windows
        return [(settings.chat_limit_window_seconds, settings.chat_limit_per_window, False),
                (86400, settings.chat_limit_per_day, True)]

    def reserve(self, user_id: str) -> float:
        """Take one message for user_id, or raise ChatLimited. Returns the token to refund it with."""
        with self._lock:
            now = self._clock()
            sent = self._sent[user_id]
            longest = max((w for w, n, _d in self.windows() if n > 0), default=0)
            while sent and now - sent[0] >= longest:
                sent.popleft()
            for seconds, allowed, daily in self.windows():
                if allowed <= 0:
                    continue
                recent = [t for t in sent if now - t < seconds]
                if len(recent) >= allowed:
                    oldest = recent[-allowed]   # the message whose aging out frees a place
                    raise ChatLimited(retry_after=max(1, int(seconds - (now - oldest)) + 1), daily=daily)
            sent.append(now)
            return now

    def status(self, user_id: str, windows=None) -> list[dict]:
        """Read only, per active window (limit above 0): {seconds, allowed, used, left, resets_in_seconds, daily}.
        resets_in_seconds: until the oldest counted message leaves the window (None when none is counted)."""
        with self._lock:
            now = self._clock()
            sent = self._sent.get(user_id, ())
            out = []
            for seconds, allowed, daily in (windows if windows is not None else self.windows()):
                if allowed <= 0:
                    continue
                recent = [t for t in sent if now - t < seconds]
                out.append({"seconds": seconds, "allowed": allowed, "used": len(recent),
                            "left": max(allowed - len(recent), 0), "daily": daily,
                            "resets_in_seconds": max(1, int(seconds - (now - recent[0])) + 1) if recent else None})
            return out

    def counts(self) -> dict[str, list[int]]:
        """{user_id: [messages in each window of windows(), in order]} for the admin panel (read only)."""
        with self._lock:
            now = self._clock()
            return {user: [sum(1 for t in sent if now - t < seconds) for seconds, _n, _d in self.windows()]
                    for user, sent in self._sent.items() if sent}

    def refund(self, user_id: str, token: float) -> None:
        """Give back a reserved message that never started a run."""
        with self._lock:
            try:
                self._sent[user_id].remove(token)
            except ValueError:
                pass


allowance = Allowance()
