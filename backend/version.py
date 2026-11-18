"""Which code this backend is running, read once when it starts: so a backend left running from earlier (serving old
code while the files on disk moved on) can be told apart from a fresh one.

current() is logged at startup and served to a signed-in user by GET /api/health/version; the public health route
doesn't show it.
"""
import logging
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_INDEX = ROOT / "frontend" / "dist" / "index.html"
log = logging.getLogger(__name__)


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


@lru_cache(maxsize=1)
def current() -> dict:
    """{commit, backend_changed, started_at, frontend_built_at}: the commit checked out when this process started,
    whether backend/ had changes beyond it then, when the process started and when the served build was made."""
    commit = _git("rev-parse", "--short", "HEAD")
    changed = _git("status", "--porcelain", "--", "backend")
    built = DIST_INDEX.stat().st_mtime if DIST_INDEX.exists() else None
    return {
        "commit": commit,
        "backend_changed": bool(changed) if changed is not None else None,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frontend_built_at": (datetime.fromtimestamp(built, timezone.utc).isoformat(timespec="seconds")
                              if built else None),
    }


def log_startup() -> None:
    v = current()
    log.info("Starting backend at commit %s%s (frontend built %s)", v["commit"] or "unknown",
             " with uncommitted backend changes" if v["backend_changed"] else "", v["frontend_built_at"] or "never")
