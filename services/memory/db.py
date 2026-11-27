"""Memory service SQL: remembered facts, one row per value (a key such as "hobbies" can hold many values), every read
scoped by user id. Plain sync functions; the servicer runs them on a thread.

memory.db is this service's own file (services.common.sqlite_db). `user_id` is the User service's id kept as a plain
value: there is no foreign key to a table that lives in another service.
"""
import uuid
from datetime import datetime, timezone

from services.common.sqlite_db import ServiceDB

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_facts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    value TEXT NOT NULL COLLATE NOCASE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, memory_key, value)
);
CREATE INDEX IF NOT EXISTS idx_facts_user ON user_facts(user_id, memory_key);
"""


def open_db(path=None) -> ServiceDB:
    return ServiceDB("memory", SCHEMA, path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _need(**fields: str) -> None:
    for name, value in fields.items():
        if not value or not value.strip():
            raise ValueError(f"{name} must not be empty")


def list_facts(db: ServiceDB, user_id: str) -> list[dict]:
    """The user's facts grouped by key, most recently updated key first: [{"memory_key", "values", "updated_at"}]."""
    _need(user_id=user_id)
    with db.tx() as c:
        rows = c.execute("SELECT memory_key, value, updated_at FROM user_facts WHERE user_id = ? "
                         "ORDER BY created_at, rowid", (user_id,)).fetchall()
    grouped: dict[str, dict] = {}
    for r in rows:
        g = grouped.setdefault(r["memory_key"], {"memory_key": r["memory_key"], "values": [], "updated_at": ""})
        g["values"].append(r["value"])
        g["updated_at"] = max(g["updated_at"], r["updated_at"])
    return sorted(grouped.values(), key=lambda g: g["updated_at"], reverse=True)


def _add(c, user_id: str, memory_key: str, value: str) -> None:
    now = _now()
    cur = c.execute("UPDATE user_facts SET updated_at = ? WHERE user_id = ? AND memory_key = ? AND value = ?",
                    (now, user_id, memory_key, value))
    if cur.rowcount == 0:
        c.execute("INSERT INTO user_facts (id, user_id, memory_key, value, created_at, updated_at) "
                  "VALUES (?, ?, ?, ?, ?, ?)", (uuid.uuid4().hex, user_id, memory_key, value, now, now))


def add_fact(db: ServiceDB, user_id: str, memory_key: str, value: str) -> None:
    _need(user_id=user_id, memory_key=memory_key, value=value)
    with db.tx() as c:
        _add(c, user_id, memory_key, value)


def set_fact(db: ServiceDB, user_id: str, memory_key: str, value: str) -> None:
    """Make `value` the key's only value, in one transaction."""
    _need(user_id=user_id, memory_key=memory_key, value=value)
    with db.tx() as c:
        c.execute("DELETE FROM user_facts WHERE user_id = ? AND memory_key = ? AND value != ?",
                  (user_id, memory_key, value))
        _add(c, user_id, memory_key, value)


def delete_fact(db: ServiceDB, user_id: str, memory_key: str, value: str | None) -> int:
    _need(user_id=user_id, memory_key=memory_key)
    with db.tx() as c:
        if value is None:
            cur = c.execute("DELETE FROM user_facts WHERE user_id = ? AND memory_key = ?", (user_id, memory_key))
        else:
            cur = c.execute("DELETE FROM user_facts WHERE user_id = ? AND memory_key = ? AND value = ?",
                            (user_id, memory_key, value))
        return cur.rowcount


def import_facts(db: ServiceDB, facts: list[dict]) -> int:
    """Rows as they are (ids and timestamps kept); one already there (same id, or same user, key and value ignoring
    case) is left alone. Returns how many were created."""
    created = 0
    with db.tx() as c:
        for f in facts:
            _need(id=f["id"], user_id=f["user_id"], memory_key=f["memory_key"], value=f["value"])
            created += c.execute(
                "INSERT OR IGNORE INTO user_facts (id, user_id, memory_key, value, created_at, updated_at) "
                "VALUES (:id, :user_id, :memory_key, :value, :created_at, :updated_at)", f).rowcount
    return created


def count_facts(db: ServiceDB) -> int:
    with db.tx() as c:
        return c.execute("SELECT COUNT(*) FROM user_facts").fetchone()[0]
