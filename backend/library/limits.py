"""Groq's tokens-per-minute limit, tracked here so batches can wait it out instead of hitting rate-limit errors.

Every model call records the tokens it used. The window is the last WINDOW_SECONDS: tokens drop out of it as
they age. seconds_until(max_used) says how long until no more than max_used tokens are left in it.
"""
import threading
import time

from backend.library import config


class TokenWindow:
    def __init__(self, limit: int, window: float = 60.0, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._events: list[tuple[float, int]] = []
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        self._events = [(t, n) for t, n in self._events if t > now - self.window]

    def record(self, tokens: int) -> None:
        if tokens > 0:
            with self._lock:
                self._events.append((self.clock(), int(tokens)))

    def used(self) -> int:
        with self._lock:
            self._prune(self.clock())
            return sum(n for _, n in self._events)

    def seconds_until(self, max_used: int) -> float:
        """Seconds until at most `max_used` tokens remain in the window (0 if that is already so)."""
        max_used = max(0, max_used)
        with self._lock:
            now = self.clock()
            self._prune(now)
            remaining = sum(n for _, n in self._events)
            if remaining <= max_used:
                return 0.0
            for t, n in sorted(self._events):  # oldest first: each stops counting `window` seconds after it was spent
                remaining -= n
                if remaining <= max_used:
                    return max(0.0, t + self.window - now)
            return 0.0

    def snapshot(self) -> dict:
        used = self.used()
        return {"used": used, "limit": self.limit, "free_in": round(self.seconds_until(0), 1)}


def tokens_of(message) -> int:
    """Tokens a model reply used (prompt + answer), as reported by the provider."""
    usage = getattr(message, "usage_metadata", None) or {}
    return int(usage.get("total_tokens") or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)) or 0)


def key_count() -> int:
    """Groq keys from separate accounts (GROQ_API_KEY ... _4): each has its own per-model limit, so they add up."""
    from backend.config import llm
    return max(1, len(llm.groq_keys()))


# Pooled over every key: a call goes to a key with room (day7's key pool skips limited ones).
WINDOW = TokenWindow(config.TPM_LIMIT * key_count(), config.TPM_WINDOW_SECONDS)                # the 120B (and titles)
BACKUP_WINDOW = TokenWindow(config.BACKUP_TPM_LIMIT * key_count(), config.TPM_WINDOW_SECONDS)  # the 20B: limited apart


def _count_chat_tokens(step: str, model: str, tokens: int) -> None:
    """day7's chat calls the same models on the same keys: their tokens count against the same windows, or a batch
    would start while the chat has used the minute up. The document agent's own calls (step "manage") are
    recorded by the agent itself."""
    if step == "manage":
        return
    window = WINDOW if model == config.AGENT_MODEL else BACKUP_WINDOW if model == config.BACKUP_MODEL else None
    if window is not None:
        window.record(tokens)


def _listen() -> None:
    from backend.config import usage
    usage.on_tokens(_count_chat_tokens)


_listen()


def window_for(message) -> TokenWindow:
    """The window a reply's tokens count against: the backup model's own, or the main one."""
    model = (getattr(message, "response_metadata", None) or {}).get("model_name") or ""
    return BACKUP_WINDOW if model == config.BACKUP_MODEL else WINDOW


def counter():
    """A LangChain callback that adds every model call's tokens to WINDOW (for calls made outside the agent, such as
    the title and summary writer), so the batch pacing sees them."""
    from langchain_core.callbacks import BaseCallbackHandler

    class Counter(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):
            for generations in response.generations:
                for g in generations:
                    message = getattr(g, "message", None)
                    window_for(message).record(tokens_of(message))

    return Counter()
