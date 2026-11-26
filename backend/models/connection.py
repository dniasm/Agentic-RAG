"""The one shared SQLite connection (and its lock) behind the users, conversations and memory modules.

Tests and scripts that point the app at another file set `backend.models.database._conn` (see that facade).
"""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from backend.config.settings import settings

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(settings.database_path, check_same_thread=False, timeout=30)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.execute("PRAGMA journal_mode = WAL")   # the library's batch worker writes while chats read
        _conn.execute("PRAGMA synchronous = NORMAL")
    return _conn


@contextmanager
def tx():
    """Serialized transaction on the shared connection (FastAPI runs sync routes in a threadpool)."""
    with _lock:
        conn = _get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _one(cur) -> dict | None:
    row = cur.fetchone()
    return dict(row) if row else None


def _all(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]

