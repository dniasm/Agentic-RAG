"""SQLite persistence for users, documents, conversations and messages."""
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from backend.config.settings import settings

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_memory (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_key TEXT NOT NULL,
    memory_value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, memory_key)
);
CREATE INDEX IF NOT EXISTS idx_memory_user ON user_memory(user_id);
-- One row per remembered value: a key such as "hobbies" can hold many values. Replaces user_memory
-- (one JSON-encoded value per key), which is kept, unused, as a backup of the pre-migration data.
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
CREATE TABLE IF NOT EXISTS migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    pages INTEGER NOT NULL DEFAULT 0,
    chunks INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, file_hash)
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool TEXT,
    meta TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, updated_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(settings.database_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
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


def init_db() -> None:
    with tx() as c:
        c.executescript(SCHEMA)
        _migrate_memory_to_facts(c)


def _migrate_memory_to_facts(c) -> None:
    """Copy user_memory (one row per key, collections stored as JSON lists) into user_facts (one row
    per value). Runs once: recorded in `migrations`, so deleting facts later never resurrects old ones."""
    if c.execute("SELECT 1 FROM migrations WHERE name = 'user_facts_v1'").fetchone():
        return
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


def _one(cur) -> dict | None:
    row = cur.fetchone()
    return dict(row) if row else None


def _all(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- users
def create_user(username: str, password_hash: str) -> dict:
    user = {"id": uuid.uuid4().hex, "username": username, "password_hash": password_hash, "created_at": _now()}
    with tx() as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, created_at) "
            "VALUES (:id, :username, :password_hash, :created_at)",
            user,
        )
    return user


def get_user_by_username(username: str) -> dict | None:
    with tx() as c:
        return _one(c.execute("SELECT * FROM users WHERE username = ?", (username,)))


def get_user(user_id: str) -> dict | None:
    with tx() as c:
        return _one(c.execute("SELECT * FROM users WHERE id = ?", (user_id,)))


# ---------------------------------------------------------------- memory (key-value facts)
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


# ---------------------------------------------------------------- documents
def add_document(user_id: str, filename: str, file_hash: str, size_bytes: int) -> dict:
    doc = {
        "id": uuid.uuid4().hex, "user_id": user_id, "filename": filename, "file_hash": file_hash,
        "size_bytes": size_bytes, "pages": 0, "chunks": 0, "status": "processing", "created_at": _now(),
    }
    with tx() as c:
        c.execute(
            "INSERT INTO documents (id, user_id, filename, file_hash, size_bytes, pages, chunks, status, created_at) "
            "VALUES (:id, :user_id, :filename, :file_hash, :size_bytes, :pages, :chunks, :status, :created_at)",
            doc,
        )
    return doc


_DOC_FIELDS = {"pages", "chunks", "status", "error"}


def update_document(doc_id: str, **fields) -> None:
    fields = {k: v for k, v in fields.items() if k in _DOC_FIELDS}
    if not fields:
        return
    cols = ", ".join(f"{k} = :{k}" for k in fields)
    with tx() as c:
        c.execute(f"UPDATE documents SET {cols} WHERE id = :id", {**fields, "id": doc_id})


def find_document_by_hash(user_id: str, file_hash: str) -> dict | None:
    with tx() as c:
        return _one(c.execute("SELECT * FROM documents WHERE user_id = ? AND file_hash = ?", (user_id, file_hash)))


def list_documents(user_id: str, status: str | None = None) -> list[dict]:
    with tx() as c:
        if status:
            return _all(c.execute(
                "SELECT * FROM documents WHERE user_id = ? AND status = ? ORDER BY created_at DESC", (user_id, status)
            ))
        return _all(c.execute("SELECT * FROM documents WHERE user_id = ? ORDER BY created_at DESC", (user_id,)))


def get_document(user_id: str, doc_id: str) -> dict | None:
    with tx() as c:
        return _one(c.execute("SELECT * FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id)))


def delete_document(user_id: str, doc_id: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id))


# ---------------------------------------------------------------- conversations
def create_conversation(user_id: str, title: str) -> dict:
    now = _now()
    conv = {"id": uuid.uuid4().hex, "user_id": user_id, "title": (title.strip()[:60] or "New chat"),
            "created_at": now, "updated_at": now}
    with tx() as c:
        c.execute(
            "INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
            "VALUES (:id, :user_id, :title, :created_at, :updated_at)",
            conv,
        )
    return conv


def get_conversation(user_id: str, conv_id: str) -> dict | None:
    with tx() as c:
        return _one(c.execute("SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id)))


def list_conversations(user_id: str) -> list[dict]:
    with tx() as c:
        return _all(c.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ))


def rename_conversation(user_id: str, conv_id: str, title: str) -> None:
    with tx() as c:
        c.execute("UPDATE conversations SET title = ? WHERE id = ? AND user_id = ?", (title[:60], conv_id, user_id))


def delete_conversation(user_id: str, conv_id: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id))


def add_message(conv_id: str, role: str, content: str, tool: str | None = None, meta: dict | None = None) -> dict:
    msg = {"id": uuid.uuid4().hex, "conversation_id": conv_id, "role": role, "content": content,
           "tool": tool, "meta": json.dumps(meta or {}), "created_at": _now()}
    with tx() as c:
        c.execute(
            "INSERT INTO messages (id, conversation_id, role, content, tool, meta, created_at) "
            "VALUES (:id, :conversation_id, :role, :content, :tool, :meta, :created_at)",
            msg,
        )
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (msg["created_at"], conv_id))
    return {**msg, "meta": meta or {}}


def list_messages(conv_id: str, limit: int | None = None) -> list[dict]:
    with tx() as c:
        if limit:
            rows = _all(c.execute(
                "SELECT * FROM (SELECT * FROM messages WHERE conversation_id = ? "
                "ORDER BY created_at DESC LIMIT ?) ORDER BY created_at",
                (conv_id, limit),
            ))
        else:
            rows = _all(c.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at", (conv_id,)))
    for r in rows:
        r["meta"] = json.loads(r["meta"] or "{}")
    return rows


# ---------------------------------------------------------------- stats
def user_stats(user_id: str) -> dict:
    with tx() as c:
        docs = _one(c.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(chunks), 0) chunks FROM documents WHERE user_id = ? AND status = 'indexed'",
            (user_id,),
        ))
        convs = _one(c.execute("SELECT COUNT(*) n FROM conversations WHERE user_id = ?", (user_id,)))
        rows = _all(c.execute(
            "SELECT m.tool, m.meta FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.user_id = ? AND m.role = 'assistant'",
            (user_id,),
        ))

    by_tool: dict[str, int] = {}
    rewritten = evaluated = sufficient = 0
    latencies = []
    for r in rows:
        by_tool[r["tool"] or "unknown"] = by_tool.get(r["tool"] or "unknown", 0) + 1
        meta = json.loads(r["meta"] or "{}")
        if meta.get("rewritten_queries"):
            rewritten += 1
        ev = meta.get("evaluation")
        if ev:
            evaluated += 1
            sufficient += 1 if ev.get("sufficient") else 0
        if meta.get("latency_ms"):
            latencies.append(meta["latency_ms"])

    return {
        "documents": docs["n"],
        "chunks": docs["chunks"],
        "conversations": convs["n"],
        "queries": len(rows),
        "by_tool": by_tool,
        "queries_rewritten": rewritten,
        "answers_evaluated": evaluated,
        "answers_sufficient": sufficient,
        "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
    }
