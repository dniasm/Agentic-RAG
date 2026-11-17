"""Gemini embedding keys: GEMINI_API_KEY, then the optional GEMINI_API_KEY_2 ... _4.

Every embedding request goes through with_keys(): the first key with capacity is used, and a rate-limit refusal
(429 / RESOURCE_EXHAUSTED) moves the request to the next key. It is a fallback only: one key at a time, never
several in parallel, and each batch goes whole to one key. All keys use the same model and settings (embeddings()
builds every client), so their vectors are interchangeable and nothing needs re-indexing.

How long a refused key stays out:
  * a per-day quota named in the refusal: until Google's next day (midnight Pacific time);
  * a per-minute quota: as long as Google says ("retry in 23s"), else 60 s;
  * a refusal that names no period: the short wait, but after three of those in a row on one key, an hour, so a
    key that is really out for the day isn't retried every minute if Google's wording changes.

The free quota is per Google Cloud project: keys from the same project share it. When a key is refused for the day
while our count for it is well under EMBED_PER_KEY_DAY, that is logged once and flagged on the Admin page.

Key states are kept in memory: a restart forgets them (the next refusal marks the key again).
"""
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from backend.config import api_meter, redact
from backend.config.settings import settings

log = logging.getLogger(__name__)

SHORT_WAIT = 60.0
STRIKE_WAIT = 3600.0
STRIKES = 3
_RATE_LIMIT = re.compile(r"RESOURCE_EXHAUSTED|\b429\b")
_PER_DAY = re.compile(r"PerDay|per[ _-]day|daily", re.I)
_PER_MINUTE = re.compile(r"PerMinute|per[ _-]minute", re.I)
_RETRY_IN = re.compile(r"retry in ([\d.]+)\s*s", re.I)


class GeminiError(RuntimeError):
    """An embedding error, its text with any key removed."""


class AllKeysLimited(GeminiError):
    """Every key is rate-limited. for_day: all of them until Google's next day; wait: seconds until the first is back."""

    def __init__(self, message: str, for_day: bool, wait: float):
        super().__init__(message)
        self.for_day, self.wait = for_day, wait


# ---------------------------------------------------------------- Google's day
def _nth_sunday(year: int, month: int, n: int) -> datetime:
    first = datetime(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def provider_day(now: datetime | None = None) -> str:
    """Today's date in Pacific time (the day Gemini's quotas count), from a UTC time. US daylight saving: from 2 a.m.
    on the second Sunday of March to 2 a.m. on the first Sunday of November (UTC-7), else UTC-8."""
    now = now or datetime.now(timezone.utc)
    utc = now.astimezone(timezone.utc).replace(tzinfo=None)
    year = utc.year
    dst_start = _nth_sunday(year, 3, 2) + timedelta(hours=2 + 8)    # 2 a.m. PST, as UTC
    dst_end = _nth_sunday(year, 11, 1) + timedelta(hours=2 + 7)     # 2 a.m. PDT, as UTC
    offset = 7 if dst_start <= utc < dst_end else 8
    return (utc - timedelta(hours=offset)).date().isoformat()


def day_start(now: datetime | None = None) -> datetime:
    """The UTC time Google's current day began (midnight Pacific; always on the hour)."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    hour, today = now.replace(minute=0, second=0, microsecond=0), provider_day(now)
    while provider_day(hour - timedelta(hours=1)) == today:
        hour -= timedelta(hours=1)
    return hour


def next_day_start(now: datetime | None = None) -> datetime:
    return day_start((day_start(now) + timedelta(hours=25)))


# ---------------------------------------------------------------- keys and clients
def gemini_keys() -> list[str]:
    keys = (settings.gemini_api_key, settings.gemini_api_key_2, settings.gemini_api_key_3, settings.gemini_api_key_4)
    return [k for k in keys if k]


def key_count() -> int:
    return max(1, len(gemini_keys()))


def client_options() -> dict:
    """Everything a client is built with apart from its key: the same for every key."""
    return {"model": settings.embedding_model, "output_dimensionality": settings.embedding_dim}


@lru_cache(maxsize=None)
def embeddings(key: int = 0):
    """The embeddings client for key `key` (0 = GEMINI_API_KEY). The only place one is built."""
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    keys = gemini_keys()
    return GoogleGenerativeAIEmbeddings(google_api_key=keys[key] if key < len(keys) else settings.gemini_api_key,
                                        **client_options())


def used_today(now: datetime | None = None) -> dict[int, int]:
    """Requests sent today (Google's day) per key index, by our own count."""
    return api_meter.calls_by_key("gemini", api_meter.hour_of(day_start(now)))


# ---------------------------------------------------------------- the pool
class KeyPool:
    def __init__(self):
        self._lock = threading.Lock()
        self._until: dict[int, float] = {}     # key -> wall-clock time it may be used again
        self._for_day: dict[int, str] = {}     # key -> the Google day it was refused for
        self._strikes: dict[int, int] = {}     # key -> refusals in a row that named no period
        self._shared: dict[int, int] = {}      # key -> our count when it was refused for the day (if well under)

    def available(self, count: int, now: float | None = None) -> list[int]:
        now = time.time() if now is None else now
        with self._lock:
            return [i for i in range(count) if self._until.get(i, 0) <= now]

    def mark(self, key: int, error: Exception, now: datetime | None = None) -> str:
        """Take a refused key out (see the module docstring). Returns "day", "minute" or "unexplained"."""
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        text = str(error)
        retry = _RETRY_IN.search(text)
        short = float(retry.group(1)) + 1 if retry else SHORT_WAIT
        with self._lock:
            if _PER_DAY.search(text):
                kind = "day"
                self._until[key] = next_day_start(now).timestamp()
                self._for_day[key] = provider_day(now)
                self._strikes[key] = 0
            elif _PER_MINUTE.search(text):
                kind = "minute"
                self._until[key] = now.timestamp() + short
                self._strikes[key] = 0
            else:
                kind = "unexplained"
                self._strikes[key] = self._strikes.get(key, 0) + 1
                self._until[key] = now.timestamp() + (STRIKE_WAIT if self._strikes[key] >= STRIKES else short)
            wait = self._until[key] - now.timestamp()
        log.warning("Gemini key %d is rate-limited (%s) for %.0f s: %s", key + 1, kind, wait, redact.scrub(text)[:200])
        if kind == "day":
            self._check_shared(key, now)
        return kind

    def _check_shared(self, key: int, now: datetime) -> None:
        """Refused for the day well under its count: probably sharing a Google project with another key."""
        try:
            used = used_today(now).get(key, 0)
        except Exception:
            return
        if settings.embed_per_key_day > 0 and used < settings.embed_per_key_day / 2:
            with self._lock:
                first = key not in self._shared
                self._shared[key] = used
            if first:
                from backend.library.logs import event
                event("gemini_key_shares_quota", key=key + 1, used_today=used, per_key=settings.embed_per_key_day)

    def ok(self, key: int) -> None:
        with self._lock:
            self._strikes.pop(key, None)

    def out_for_day(self, key: int, now: datetime | None = None) -> bool:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with self._lock:
            return self._for_day.get(key) == provider_day(now) and self._until.get(key, 0) > now.timestamp()

    def cooldowns(self, now: float | None = None) -> dict[int, float]:
        """{key index: seconds until it may be used again} (the Admin page)."""
        now = time.time() if now is None else now
        with self._lock:
            return {k: round(t - now, 1) for k, t in self._until.items() if t > now}

    def shared(self) -> dict[int, int]:
        with self._lock:
            return dict(self._shared)

    def reset(self) -> None:
        with self._lock:
            self._until.clear()
            self._for_day.clear()
            self._strikes.clear()
            self._shared.clear()


pool = KeyPool()


def is_rate_limit(error: Exception) -> bool:
    return bool(_RATE_LIMIT.search(str(error)))


def with_keys(call, use: str, calls: int = 1):
    """call(embeddings client) on the first key with capacity; on a rate limit, the next key. Counts `calls`
    requests (or one refusal) under the key that was used, as `use` ("documents" or "questions").
    Raises AllKeysLimited when every key is limited, and errors with key text only after removing it."""
    count = key_count()
    last = None
    for key in pool.available(count):
        try:
            result = call(embeddings(key))
        except Exception as e:
            if not is_rate_limit(e):
                clean = redact.scrub(str(e))
                if clean != str(e):
                    raise GeminiError(clean) from None
                raise
            api_meter.count("gemini", key, use, limited=1)
            pool.mark(key, e)
            last = e
            continue
        pool.ok(key)
        api_meter.count("gemini", key, use, calls=calls)
        return result
    for_day = all(pool.out_for_day(k) for k in range(count))
    waits = pool.cooldowns()
    wait = min((waits.get(k, 0.0) for k in range(count)), default=0.0)
    detail = f" Last refusal: {redact.scrub(str(last))[:300]}" if last else ""
    raise AllKeysLimited(f"Every Gemini key is rate-limited {'for today' if for_day else 'for now'} "
                         f"(RESOURCE_EXHAUSTED).{detail}", for_day=for_day, wait=max(wait, 0.0))
