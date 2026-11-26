"""Remembered facts about a user: key -> many values."""
import json
import uuid

from backend.models.connection import _all, _now, tx

SCHEMA = """
-- One row per remembered value: a key such as "hobbies" can hold many values. Replaced user_memory
-- (one JSON-encoded value per key): databases from before 2026-11-01 still have that table, unused, as a
-- backup of the pre-migration data; new databases don't get it.
CREATE TABLE IF NOT EXISTS user_facts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_key TEXT NOT NULL,
    value TEXT NOT NULL COLLATE NOCASE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, memory_key, value)
);
CREATE INDEX IF NOT EXISTS idx_facts_user ON user_facts(user_id, memory_key);
"""


def _migrate_memory_to_facts(c) -> None:
    """Copy user_memory (one row per key, collections stored as JSON lists) into user_facts (one row
    per value). Runs once: recorded in `migrations`, so deleting facts later never resurrects old ones."""
    if c.execute("SELECT 1 FROM migrations WHERE name = 'user_facts_v1'").fetchone():
        return
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'user_memory'").fetchone():
        c.execute("INSERT INTO migrations (name, applied_at) VALUES ('user_facts_v1', ?)", (_now(),))
        return  # a database created after the migration: nothing to copy
    for row in c.execute("SELECT user_id, memory_key, memory_value, created_at, updated_at "
                         "FROM user_memory").fetchall():
        for value in _decode_values(row["memory_value"]):
            c.execute(
                "INSERT OR IGNORE INTO user_facts (id, user_id, memory_key, value, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, row["user_id"], row["memory_key"], value, row["created_at"], row["updated_at"]),
            )
    c.execute("INSERT INTO migrations (name, applied_at) VALUES ('user_facts_v1', ?)", (_now(),))




def _decode_values(raw: str) -> list[str]:
    """Old memory values: a JSON list of strings for collections, plain text otherwise."""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [v.strip() for v in parsed if isinstance(v, str) and v.strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    return [raw.strip()] if raw and raw.strip() else []



def list_memories(user_id: str) -> list[dict]:
    """The user's facts grouped by key, most recently updated key first:
    [{"memory_key", "memory_value" (values joined with "; "), "values", "updated_at"}].
    memory_value is what prompts show, e.g. "hobbies: trekking; futsal"."""
    with tx() as c:
        rows = _all(c.execute(
            "SELECT memory_key, value, updated_at FROM user_facts "
            "WHERE user_id = ? ORDER BY created_at, rowid",
            (user_id,),
        ))
    grouped: dict[str, dict] = {}
    for r in rows:
        g = grouped.setdefault(r["memory_key"], {"memory_key": r["memory_key"], "values": [], "updated_at": ""})
        g["values"].append(r["value"])
        g["updated_at"] = max(g["updated_at"], r["updated_at"])
    out = sorted(grouped.values(), key=lambda g: g["updated_at"], reverse=True)
    for g in out:
        g["memory_value"] = "; ".join(g["values"])
    return out


def add_fact(user_id: str, memory_key: str, value: str) -> None:
    """Add one value to a key (a duplicate, ignoring case, only refreshes its timestamp)."""
    now = _now()
    with tx() as c:
        cur = c.execute(
            "UPDATE user_facts SET updated_at = ? WHERE user_id = ? AND memory_key = ? AND value = ?",
            (now, user_id, memory_key, value),
        )
        if cur.rowcount == 0:
            c.execute(
                "INSERT INTO user_facts (id, user_id, memory_key, value, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, user_id, memory_key, value, now, now),
            )


def set_fact(user_id: str, memory_key: str, value: str) -> None:
    """Make `value` the key's only value (single-value keys such as preferred_name)."""
    with tx() as c:
        c.execute(
            "DELETE FROM user_facts WHERE user_id = ? AND memory_key = ? AND value != ?",
            (user_id, memory_key, value),
        )
    add_fact(user_id, memory_key, value)


def delete_fact(user_id: str, memory_key: str, value: str | None = None) -> int:
    """Delete one value of a key (ignoring case), or the whole key when value is None. Returns rows deleted."""
    with tx() as c:
        if value is None:
            cur = c.execute("DELETE FROM user_facts WHERE user_id = ? AND memory_key = ?", (user_id, memory_key))
        else:
            cur = c.execute(
                "DELETE FROM user_facts WHERE user_id = ? AND memory_key = ? AND value = ?",
                (user_id, memory_key, value),
            )
        return cur.rowcount


