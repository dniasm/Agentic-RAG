"""How many passages (chunks) may be embedded a day: per account, and for indexing across the whole site.

Every new or changed chunk is one Gemini embedding request, and the free tier allows EMBED_SITE_PER_DAY a day for
everyone (uploads, edits, and the questions a chat embeds to search documents). So:
  * each account may have EMBED_PER_USER_DAY chunks embedded a day for its uploads and edits;
  * indexing (uploads, edits, undo, redo, queued tasks) stops once the site has used EMBED_INDEX_SHARE of the day, so
    the rest stays for questions. Searching is never refused here.

An operation is checked before anything is embedded, on the number it really needs (stored vectors are reused), and
refused whole if it doesn't fit: no partial indexing. Only embeddings that succeeded are charged. The counts are kept
in app.db (embed_usage), so a restart keeps them.

The day is Google's: Gemini's per-day quotas reset at midnight Pacific time (America/Los_Angeles, with US daylight
saving), not at local midnight.
"""
from datetime import datetime, timedelta, timezone

from backend.config.settings import settings
from backend.library import catalog


class EmbedBudgetExceeded(Exception):
    """Refused before embedding; the message says how many passages were needed and how many are left today."""


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


def _ensure() -> None:
    with catalog.tx() as c:
        c.execute("CREATE TABLE IF NOT EXISTS embed_usage (day TEXT NOT NULL, user_id TEXT NOT NULL, "
                  "chunks INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, user_id))")


def used(user_id: str | None = None, day: str | None = None) -> int:
    """Chunks embedded for indexing today: by one account, or (None) by the whole site."""
    _ensure()
    day = day or provider_day()
    with catalog.tx() as c:
        if user_id is None:
            row = c.execute("SELECT COALESCE(SUM(chunks), 0) FROM embed_usage WHERE day = ?", (day,)).fetchone()
        else:
            row = c.execute("SELECT COALESCE(SUM(chunks), 0) FROM embed_usage WHERE day = ? AND user_id = ?",
                            (day, user_id)).fetchone()
    return int(row[0])


def check(user_id: str, needed: int) -> None:
    """Raise EmbedBudgetExceeded unless `needed` more chunks fit both the account's and the site's allowance."""
    if needed <= 0:
        return
    per_user, site = settings.embed_per_user_day, int(settings.embed_site_per_day * settings.embed_index_share)
    mine, everyone = used(user_id), used()
    if per_user > 0 and mine + needed > per_user:
        left = max(per_user - mine, 0)
        raise EmbedBudgetExceeded(
            f"This needs {needed} passages indexed, and your account has {left} of {per_user} left today. "
            "Try again tomorrow, or with a smaller document.")
    if site > 0 and everyone + needed > site:
        left = max(site - everyone, 0)
        raise EmbedBudgetExceeded(
            f"This needs {needed} passages indexed, and the site has {left} left for indexing today (the rest is "
            "kept for answering questions). Try again tomorrow.")


def charge(user_id: str, chunks: int) -> None:
    """Count chunks that were embedded (after the embedding succeeded)."""
    if chunks <= 0:
        return
    _ensure()
    with catalog.tx() as c:
        c.execute("INSERT INTO embed_usage (day, user_id, chunks) VALUES (?, ?, ?) "
                  "ON CONFLICT(day, user_id) DO UPDATE SET chunks = chunks + excluded.chunks",
                  (provider_day(), user_id, chunks))
