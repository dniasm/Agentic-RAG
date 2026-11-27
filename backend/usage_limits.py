"""The one place a per-account limit number is read from: the global settings for everyone, except that accounts
listed in ADMIN_USERNAMES are never limited per account. (The site's own Gemini quota still applies to them.)

Read by /api/usage/me, the chat message allowance (api/routes/chat.py) and the per-account indexing check
(library/embed_budget.check)."""
from backend.config.settings import settings


def is_unlimited(user_id: str) -> bool:
    """True for an admin account. Anything unknown or failing counts as a normal (limited) account."""
    try:
        from backend.library import auth
        return any(u["id"] == user_id and auth.is_admin_name(u["username"]) for u in auth.list_users())
    except Exception:
        return False


def limits_for(user_id: str) -> dict:
    """{"chat_per_window", "chat_window_seconds", "chat_per_day", "index_per_day", "unlimited"}; for an admin the
    three counts are 0 (off, as in settings)."""
    if is_unlimited(user_id):
        return {"chat_per_window": 0, "chat_window_seconds": settings.chat_limit_window_seconds,
                "chat_per_day": 0, "index_per_day": 0, "unlimited": True}
    return {"chat_per_window": settings.chat_limit_per_window,
            "chat_window_seconds": settings.chat_limit_window_seconds,
            "chat_per_day": settings.chat_limit_per_day,
            "index_per_day": settings.embed_per_user_day, "unlimited": False}


def chat_windows(user_id: str) -> list[tuple[int, int, bool]]:
    """[(seconds, messages allowed, is the day's)] for this account, in chat_limits.Allowance.windows() form."""
    lim = limits_for(user_id)
    return [(lim["chat_window_seconds"], lim["chat_per_window"], False), (86400, lim["chat_per_day"], True)]
