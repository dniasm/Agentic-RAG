"""How many passages (chunks) may be embedded a day: per account, and for indexing across the whole site.

Every new or changed chunk is one Gemini embedding request, and the free tier allows about EMBED_PER_KEY_DAY a day
per key (per Google project) for everyone (uploads, edits, and the questions a chat embeds to search documents). So:
  * each account may have EMBED_PER_USER_DAY chunks embedded a day for its uploads and edits;
  * indexing (uploads, edits, undo, redo, queued tasks) stops once the site has used EMBED_INDEX_SHARE of the day, so
    the rest stays for questions. Searching is never refused here.

The site's day (site_day) comes from each key's real state, not from the number of keys: what a key has left is
EMBED_PER_KEY_DAY minus our count of its requests today, and nothing once Google has refused it for the day. Keys
that turn out to share a project therefore shrink the day by themselves instead of letting indexing use up what
questions need. EMBED_SITE_PER_DAY, when set, is a fixed total instead.

An operation is checked before anything is embedded, on the number it really needs (stored vectors are reused), and
refused whole if it doesn't fit: no partial indexing. Only embeddings that succeeded are charged. The counts are kept
in the User service (embed_usage for indexing per account, api_usage for every request per key), so a restart keeps them.

The day is Google's: Gemini's per-day quotas reset at midnight Pacific time (America/Los_Angeles, with US daylight
saving), not at local midnight (config/gemini.provider_day).
"""
from datetime import datetime

from agentic_rag_proto import user_pb2 as pb
from backend import usage_limits
from backend.clients import get_clients
from backend.config import gemini
from backend.config.settings import settings

provider_day = gemini.provider_day


class EmbedBudgetExceeded(Exception):
    """Refused before embedding; the message says how many passages were needed and how many are left today."""


def _call(rpc_name: str, request):
    clients = get_clients()
    return clients.call(getattr(clients.user, rpc_name), request)


def used(user_id: str | None = None, day: str | None = None) -> int:
    """Chunks embedded for indexing today: by one account, or (None) by the whole site."""
    request = pb.GetEmbedUsageRequest(day=day or provider_day())
    if user_id is not None:
        request.user_id = user_id
    return _call("GetEmbedUsage", request).chunks


def used_by_user(day: str) -> dict[str, int]:
    """Every account's chunks embedded for indexing on `day` (the admin panel)."""
    return {r.user_id: r.chunks for r in _call("ListEmbedUsage", pb.ListEmbedUsageRequest(day=day)).rows}


def key_states(now: datetime | None = None) -> list[dict]:
    """Per Gemini key: requests sent today (our count), what is left of EMBED_PER_KEY_DAY, and whether Google refused
    it for the day."""
    from backend.documents import rag   # (imported here: rag imports this module for the allowance)
    counted, per_key = gemini.used_today(now), settings.embed_per_key_day
    pool = {s["index"]: s for s in rag.key_states()}   # the key pool lives in the Document service, next to the embedding
    states = []
    for k in range(gemini.key_count()):
        out = pool.get(k, {}).get("out_for_day", False)
        used_k = counted.get(k, 0)
        states.append({"index": k, "used": used_k, "out_for_day": out,
                       "left": 0 if out else (max(per_key - used_k, 0) if per_key > 0 else None)})
    return states


def site_day(now: datetime | None = None) -> dict:
    """{"total", "used", "left"} for the site's day; total and left are None when there is no limit."""
    states = key_states(now)
    counted = sum(s["used"] for s in states)
    indexed = used(day=provider_day(now))
    sent = max(counted, indexed)            # (indexing is charged separately; never count less than it)
    fixed = settings.embed_site_per_day
    if fixed is not None:
        if fixed <= 0:
            return {"total": None, "used": sent, "left": None}
        return {"total": fixed, "used": sent, "left": max(fixed - sent, 0)}
    if settings.embed_per_key_day <= 0:
        return {"total": None, "used": sent, "left": None}
    left = max(sum(s["left"] for s in states) - max(indexed - counted, 0), 0)
    return {"total": sent + left, "used": sent, "left": left}


def index_limit(day: dict | None = None) -> int | None:
    """How many passages the site may index today (EMBED_INDEX_SHARE of its day); None: no limit."""
    day = day or site_day()
    return None if day["total"] is None else int(day["total"] * settings.embed_index_share)


def allowance(user_id: str, safety: bool = False) -> int | None:
    """The most passages that could be embedded for this account right now (None: no limit). It is what check() accepts,
    as a number: the Document service gets it as max_passages and refuses a file that needs more, before embedding."""
    day = site_day()
    if safety:
        return day["left"]
    lefts = []
    per_user = usage_limits.limits_for(user_id)["index_per_day"]    # 0 for an admin account: no per-account cap
    if per_user > 0:
        lefts.append(max(per_user - used(user_id), 0))
    limit = index_limit(day)
    if limit is not None:
        lefts.append(max(min(limit - used(), day["left"]), 0))
    return min(lefts) if lefts else None


def check(user_id: str, needed: int, safety: bool = False) -> None:
    """Raise EmbedBudgetExceeded unless `needed` more chunks fit both the account's and the site's allowance.

    safety: an undo or redo, the way back from a change. It isn't held to the account's cap or the indexing share
    (someone who used up their day on edits can still undo one), only to the site's whole day."""
    if needed <= 0:
        return
    if safety:
        day = site_day()
        if day["left"] is not None and needed > day["left"]:
            raise EmbedBudgetExceeded(
                f"This needs {needed} passages indexed, and the site's embedding quota for today is used up "
                f"({day['left']} left). Try again tomorrow.")
        return
    per_user = usage_limits.limits_for(user_id)["index_per_day"]    # 0 for an admin account: no per-account cap
    mine = used(user_id)
    if per_user > 0 and mine + needed > per_user:
        left = max(per_user - mine, 0)
        raise EmbedBudgetExceeded(
            f"This needs {needed} passages indexed, and your account has {left} of {per_user} left today. "
            "Try again tomorrow, or with a smaller document.")
    day = site_day()
    limit = index_limit(day)
    if limit is not None:
        everyone = used()
        left = max(min(limit - everyone, day["left"]), 0)
        if needed > left:
            raise EmbedBudgetExceeded(
                f"This needs {needed} passages indexed, and the site has {left} left for indexing today (the rest "
                "is kept for answering questions). Try again tomorrow.")


def charge(user_id: str, chunks: int) -> None:
    """Count chunks that were embedded (after the embedding succeeded). The User service adds them atomically."""
    if chunks <= 0:
        return
    _call("AddEmbedUsage", pb.AddEmbedUsageRequest(day=provider_day(), user_id=user_id, chunks=chunks))

