"""Your own usage and limits, for the Overview page: chat messages per window, passages indexed today, login expiry.
Signed-in accounts only, own numbers only, never cached."""
import logging
from datetime import timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend import chat_limits, usage_limits
from backend.api.dependencies import get_current_user
from backend.config import gemini
from backend.library import embed_budget

router = APIRouter(prefix="/usage", tags=["Usage"])
log = logging.getLogger(__name__)


def _indexing(user_id: str) -> dict:
    allowed = usage_limits.limits_for(user_id)["index_per_day"]
    used = embed_budget.used(user_id)
    left = max(allowed - used, 0) if allowed > 0 else None
    out = {"allowed": allowed if allowed > 0 else None, "used": used, "left": left,
           "day": embed_budget.provider_day(),
           "resets_at": gemini.next_day_start().astimezone(timezone.utc).isoformat(timespec="seconds")}
    can = embed_budget.allowance(user_id)     # the lower of this account's left and the site's (what check() accepts)
    if can is not None and (left is None and can <= 0 or left is not None and can < left):
        out["left"], out["site_limited"] = can, True
    return out


@router.get("/me")
def my_usage(user: dict = Depends(get_current_user)):
    body = {"unlimited": bool(user.get("is_admin")), "indexing": None,
            "chat": chat_limits.allowance.status(user["id"], usage_limits.chat_windows(user["id"]))}
    try:
        body["indexing"] = _indexing(user["id"])
    except Exception:
        log.warning("usage: indexing numbers unavailable", exc_info=True)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})
