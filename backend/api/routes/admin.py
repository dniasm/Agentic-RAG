"""The owner's Admin page: how much each API key is used, against its limits. Accounts listed in ADMIN_USERNAMES
only (everyone else gets 404), read only, never cached. Counts only: no message content, no document titles.

Sources (backend/config/api_meter.py): our own counts per UTC hour, and the rate-limit headers Groq sent with its
last response per key and model, which are Groq's own word on what is left. Whether Groq's daily limits run on a
calendar day or a rolling 24 hours isn't documented to us, so counts are given both ways: "last 24 hours" and
"today (UTC)". Gemini's day is Pacific time (backend/library/embed_budget.py).
"""
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend import chat_limits
from backend.api.dependencies import require_admin
from backend.config import api_meter, gemini, llm
from backend.config.settings import settings
from backend.library import catalog, embed_budget

router = APIRouter(prefix="/admin", tags=["Admin"])

_BLANK = {"calls": 0, "tokens": 0, "limited": 0}


def _add(into: dict, row: dict) -> None:
    for k in _BLANK:
        into[k] = into.get(k, 0) + row[k]


def _hour_start(hour: str) -> datetime:
    return datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def _groq(rows: list[dict], now: datetime) -> dict:
    today, last_24h = now.strftime("%Y-%m-%d"), api_meter.hour_of(now.replace(minute=0) - timedelta(hours=23))
    per = defaultdict(lambda: {"today": dict(_BLANK), "last_24h": dict(_BLANK), "last_7d": dict(_BLANK)})
    for r in rows:
        if r["provider"] != "groq":
            continue
        slot = per[(r["key"], r["model"])]
        _add(slot["last_7d"], r)
        if r["hour"] >= last_24h:
            _add(slot["last_24h"], r)
        if r["hour"].startswith(today):
            _add(slot["today"], r)
    headers = api_meter.latest_headers()
    cooldowns = llm.key_pool.cooldowns()
    keys = []
    for index in range(max(1, len(llm.groq_keys()))):
        models = sorted({m for k, m in per if k == index} | {m for k, m in headers if k == index}
                        | {m for m, k in cooldowns if k == index})
        entries = []
        for model in models:
            seen = headers.get((index, model))
            entries.append({
                "model": model,
                **per.get((index, model), {"today": dict(_BLANK), "last_24h": dict(_BLANK), "last_7d": dict(_BLANK)}),
                "headers": None if not seen else {
                    **{k.replace("-", "_"): seen.get(k) for k in api_meter._HEADERS},
                    "status": seen.get("status"), "seconds_ago": round(time.time() - seen["at"]),
                },
                "cooldown_seconds": cooldowns.get((model, index)),
            })
        total = {"today": dict(_BLANK), "last_24h": dict(_BLANK)}
        for e in entries:
            _add(total["today"], e["today"])
            _add(total["last_24h"], e["last_24h"])
        keys.append({"key": index + 1, "models": entries, **total})
    return {"keys": keys, "header_names": sorted(api_meter._header_names)}


def _gemini(rows: list[dict], now: datetime) -> dict:
    day = embed_budget.provider_day(now)
    per_key = defaultdict(lambda: {"documents": 0, "questions": 0, "limited": 0})
    for r in rows:
        if r["provider"] == "gemini" and r["model"] in ("documents", "questions")                 and embed_budget.provider_day(_hour_start(r["hour"])) == day:
            per_key[r["key"]][r["model"]] += r["calls"]
            per_key[r["key"]]["limited"] += r["limited"]
    cooldowns, shared, rejected = gemini.pool.cooldowns(), gemini.pool.shared(), gemini.pool.rejected()
    keys = [{"key": s["index"] + 1, **per_key[s["index"]], "left": s["left"], "out_for_day": s["out_for_day"],
             "cooldown_seconds": cooldowns.get(s["index"]), "shares_quota": s["index"] in shared,
             "rejected": s["index"] in rejected}
            for s in embed_budget.key_states(now)]
    site = embed_budget.site_day(now)
    documents = sum(k["documents"] for k in keys)
    questions = sum(k["questions"] for k in keys)
    return {"day": day, "documents": documents, "questions": questions, "total": documents + questions,
            "site_limit": site["total"], "site_left": site["left"], "fixed_total": settings.embed_site_per_day is not None,
            "per_key": settings.embed_per_key_day, "index_limit": embed_budget.index_limit(site),
            "indexed_charged": embed_budget.used(day=day), "keys": keys}


def _voyage(rows: list[dict], now: datetime) -> dict:
    last_24h, today = api_meter.hour_of(now.replace(minute=0) - timedelta(hours=23)), now.strftime("%Y-%m-%d")
    by_use, total_today = defaultdict(int), 0
    for r in rows:
        if r["provider"] != "voyage":
            continue
        if r["hour"] >= last_24h:
            by_use[r["model"]] += r["calls"]
        if r["hour"].startswith(today):
            total_today += r["calls"]
    return {"per_minute": settings.voyage_requests_per_minute, "last_24h": dict(by_use), "today": total_today}


def _accounts(now: datetime) -> dict:
    windows = chat_limits.allowance.windows()
    chats = chat_limits.allowance.counts()
    day = embed_budget.provider_day(now)
    embed_budget.used(day=day)   # (makes sure the table exists)
    with catalog.tx() as c:
        names = {r[0]: r[1] for r in c.execute("SELECT id, username FROM users").fetchall()}
        embedded = {r[0]: r[1] for r in c.execute(
            "SELECT user_id, chunks FROM embed_usage WHERE day = ?", (day,)).fetchall()}
    rows = []
    for user_id in set(chats) | set(embedded):
        if user_id not in names:
            continue
        counts = chats.get(user_id, [0] * len(windows))
        rows.append({"username": names[user_id], "chat": counts, "embedded_today": embedded.get(user_id, 0)})
    rows.sort(key=lambda r: (-sum(r["chat"]), -r["embedded_today"], r["username"].casefold()))
    return {"total": len(names), "rows": rows,
            "chat_windows": [{"seconds": s, "allowed": n} for s, n, _d in windows],
            "embed_per_user": settings.embed_per_user_day}


@router.get("/usage")
def usage(_admin: dict = Depends(require_admin)):
    now = datetime.now(timezone.utc)
    rows = api_meter.rows_since(7 * 24)
    body = {"generated_at": now.isoformat(timespec="seconds"), "groq": _groq(rows, now),
            "gemini": _gemini(rows, now), "voyage": _voyage(rows, now), "accounts": _accounts(now)}
    return JSONResponse(body, headers={"Cache-Control": "no-store"})
