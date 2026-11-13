"""Batch fact extraction per conversation pause.

Each memory-model call carries ~1,000 tokens of rules and tool format. Instead of one call per message, user
messages that may hold a fact are collected per user and extracted together once the user pauses
(MEMORY_BATCH_IDLE_SECONDS without a new message), or as soon as MEMORY_BATCH_MAX are waiting.

A fact is saved a little later this way. Within the same chat the conversation history already covers it;
before a request from another conversation reads the user's memories, that user's pending messages are
extracted first (flush_user), and on shutdown everything still pending is extracted (flush_all).
"""
import logging
import threading
from collections.abc import Callable

from backend.tools.memory import _may_contain_memory

log = logging.getLogger(__name__)


class MemoryBatcher:
    def __init__(self, extract: Callable[[str, list[dict]], None], idle_seconds: float, max_pending: int,
                 max_age: int, schedule: Callable[[Callable, str], object] | None = None):
        """extract(user_id, turns) does the work, on the thread that calls flush_user. When the idle timer fires,
        the flush is handed to schedule(flush_user, user_id) (the app's single memory worker, so memory updates
        never run at the same time); without one it runs on the timer thread."""
        self._extract = extract
        self._schedule = schedule or (lambda fn, user_id: fn(user_id))
        self._idle = idle_seconds
        self._max = max_pending
        self._max_age = max_age  # later user messages after which the oldest waiting one is flushed anyway
        self._lock = threading.Lock()
        # user_id -> {"conversation_id", "turns": [{"message", "history"}], "timer", "messages_since_first"}
        self._pending: dict[str, dict] = {}

    def add(self, user_id: str, conversation_id: str, message: str, history: list[dict]) -> bool:
        """Note a user message (call this for every message). Queues it if the pre-check finds something personal
        in it; returns whether it was queued. Flushes first when the message is from another conversation, and
        after it when MAX messages are waiting or the oldest one is about to leave the chat history window."""
        queue_it = bool(message) and _may_contain_memory(message)
        with self._lock:
            entry = self._pending.get(user_id)
            other_conversation = bool(entry) and entry["conversation_id"] != conversation_id
        if other_conversation:
            self.flush_user(user_id)

        with self._lock:
            entry = self._pending.get(user_id)
            if not queue_it and not entry:
                return False
            if entry is None:
                entry = self._pending[user_id] = {"conversation_id": conversation_id, "turns": [], "timer": None,
                                                  "messages_since_first": 0}
            else:
                entry["messages_since_first"] += 1
            if queue_it:
                entry["turns"].append({"message": message, "history": list(history[-2:])})
            if entry["timer"]:
                entry["timer"].cancel()
                entry["timer"] = None
            ready = len(entry["turns"]) >= self._max or entry["messages_since_first"] >= self._max_age
            if not ready:
                entry["timer"] = threading.Timer(self._idle, self._schedule, args=(self.flush_user, user_id))
                entry["timer"].daemon = True
                entry["timer"].start()
        if ready:
            self.flush_user(user_id)
        return queue_it

    def has_pending(self, user_id: str, other_than_conversation: str | None = None) -> bool:
        with self._lock:
            entry = self._pending.get(user_id)
            return bool(entry) and entry["conversation_id"] != other_than_conversation

    def flush_user(self, user_id: str) -> None:
        with self._lock:
            entry = self._pending.pop(user_id, None)
        if not entry:
            return
        if entry["timer"]:
            entry["timer"].cancel()
        try:
            self._extract(user_id, entry["turns"])
        except Exception:
            log.exception("Memory batch for user %s failed", user_id)

    def flush_all(self) -> None:
        with self._lock:
            users = list(self._pending)
        for user_id in users:
            self.flush_user(user_id)
