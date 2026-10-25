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


# ---------------------------------------------------------------- memory
def save_memory(user_id: str, memory_key: str, memory_value: str) -> dict:
    now = _now()
    memory = {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "memory_key": memory_key,
        "memory_value": memory_value,
        "created_at": now,
        "updated_at": now,
    }

    with tx() as c:
        existing = _one(c.execute(
            "SELECT * FROM user_memory "
            "WHERE user_id = ? AND memory_key = ?",
            (user_id, memory_key),
        ))

        if existing:
            c.execute(
                "UPDATE user_memory "
                "SET memory_value = ?, updated_at = ? "
                "WHERE user_id = ? AND memory_key = ?",
                (memory_value, now, user_id, memory_key),
            )
            memory = {
                **existing,
                "memory_value": memory_value,
                "updated_at": now,
            }
        else:
            c.execute(
                "INSERT INTO user_memory "
                "(id, user_id, memory_key, memory_value, created_at, updated_at) "
                "VALUES (:id, :user_id, :memory_key, :memory_value, :created_at, :updated_at)",
                memory,
            )

    return memory


def list_memories(user_id: str) -> list[dict]:
    with tx() as c:
        return _all(c.execute(
            "SELECT * FROM user_memory "
            "WHERE user_id = ? "
            "ORDER BY updated_at DESC",
            (user_id,),
        ))


def delete_memory(user_id: str, memory_key: str) -> None:
    with tx() as c:
        c.execute(
            "DELETE FROM user_memory "
            "WHERE user_id = ? AND memory_key = ?",
            (user_id, memory_key),
        )

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
