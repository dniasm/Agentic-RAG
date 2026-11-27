"""Conversation service SQL: conversations and messages, every read of a conversation scoped by its user id (a
conversation that is not yours is simply not found). Plain sync functions; the servicer runs them on a thread.

conversation.db is this service's own file (services.common.sqlite_db). `user_id` is the User service's id kept as
a plain value: there is no foreign key to a table that lives in another service.
"""
import json
import uuid
from datetime import datetime, timezone

from services.common.sqlite_db import ServiceDB

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
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
    created_at TEXT NOT NULL,
    -- Copied out of meta for the stats, which would otherwise parse every message's JSON (see stats_from_meta).
    latency_ms INTEGER,
    rewritten INTEGER NOT NULL DEFAULT 0,
    sufficient INTEGER
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, updated_at);
"""

TITLE_MAX = 60


class NotFound(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stats_from_meta(meta: dict) -> dict:
    """latency_ms; rewritten (0/1: the question was reworded to search); sufficient (None if the answer wasn't
    evaluated, else 0/1)."""
    evaluation = meta.get("evaluation")
    return {
        "latency_ms": meta.get("latency_ms") or None,
        "rewritten": 1 if meta.get("rewritten_queries") else 0,
        "sufficient": None if not evaluation else int(bool(evaluation.get("sufficient"))),
    }


def open_db(path=None) -> ServiceDB:
    return ServiceDB("conversation", SCHEMA, path)


def _row(cur) -> dict | None:
    row = cur.fetchone()
    return dict(row) if row else None


def create_conversation(db: ServiceDB, user_id: str, title: str) -> dict:
    now = _now()
    conv = {"id": uuid.uuid4().hex, "user_id": user_id, "title": (title.strip()[:TITLE_MAX] or "New chat"),
            "created_at": now, "updated_at": now}
    with db.tx() as c:
        c.execute("INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
                  "VALUES (:id, :user_id, :title, :created_at, :updated_at)", conv)
    return conv


def get_conversation(db: ServiceDB, user_id: str, conv_id: str) -> dict:
    with db.tx() as c:
        conv = _row(c.execute("SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id)))
    if conv is None:
        raise NotFound(conv_id)
    return conv


def list_conversations(db: ServiceDB, user_id: str) -> list[dict]:
    with db.tx() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, user_id, title, created_at, updated_at FROM conversations WHERE user_id = ? "
            "ORDER BY updated_at DESC, rowid DESC", (user_id,))]


def rename_conversation(db: ServiceDB, user_id: str, conv_id: str, title: str) -> dict:
    """Title only: updated_at is left alone, so a renamed chat keeps its place in the list."""
    with db.tx() as c:
        c.execute("UPDATE conversations SET title = ? WHERE id = ? AND user_id = ?",
                  (title.strip()[:TITLE_MAX], conv_id, user_id))
        conv = _row(c.execute("SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id)))
    if conv is None:
        raise NotFound(conv_id)
    return conv


def delete_conversation(db: ServiceDB, user_id: str, conv_id: str) -> int:
    """Returns how many messages went with it."""
    with db.tx() as c:
        gone = c.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND EXISTS "
                         "(SELECT 1 FROM conversations WHERE id = ? AND user_id = ?)",
                         (conv_id, conv_id, user_id)).fetchone()[0]
        if c.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id)).rowcount == 0:
            raise NotFound(conv_id)
    return gone


def add_message(db: ServiceDB, conv_id: str, role: str, content: str, tool: str | None, meta_json: str) -> dict:
    meta = json.loads(meta_json or "{}")   # invalid JSON is the caller's error (a ValueError)
    if not isinstance(meta, dict):
        raise ValueError("meta must be a JSON object")
    msg = {"id": uuid.uuid4().hex, "conversation_id": conv_id, "role": role, "content": content, "tool": tool,
           "meta": json.dumps(meta), "created_at": _now()}
    with db.tx() as c:
        if c.execute("SELECT 1 FROM conversations WHERE id = ?", (conv_id,)).fetchone() is None:
            raise NotFound(conv_id)
        c.execute(
            "INSERT INTO messages (id, conversation_id, role, content, tool, meta, created_at, latency_ms, rewritten, "
            "sufficient) VALUES (:id, :conversation_id, :role, :content, :tool, :meta, :created_at, :latency_ms, "
            ":rewritten, :sufficient)", {**msg, **stats_from_meta(meta)})
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (msg["created_at"], conv_id))
        return dict(c.execute("SELECT * FROM messages WHERE id = ?", (msg["id"],)).fetchone())


def list_messages(db: ServiceDB, conv_id: str, limit: int = 0) -> list[dict]:
    with db.tx() as c:
        if limit > 0:
            cur = c.execute("SELECT * FROM (SELECT rowid AS _r, * FROM messages WHERE conversation_id = ? "
                            "ORDER BY created_at DESC, rowid DESC LIMIT ?) ORDER BY created_at, _r", (conv_id, limit))
        else:
            cur = c.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid", (conv_id,))
        return [{k: v for k, v in dict(r).items() if k != "_r"} for r in cur]


def list_tool_messages(db: ServiceDB, conv_id: str, tool: str) -> list[dict]:
    with db.tx() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, meta, created_at FROM messages WHERE conversation_id = ? AND tool = ? "
            "ORDER BY created_at, rowid", (conv_id, tool))]


def user_stats(db: ServiceDB, user_id: str) -> dict:
    with db.tx() as c:
        convs = c.execute("SELECT COUNT(*) FROM conversations WHERE user_id = ?", (user_id,)).fetchone()[0]
        a = c.execute(
            "SELECT COUNT(*) n, SUM(m.rewritten) rewritten, COUNT(m.sufficient) evaluated, "
            "COALESCE(SUM(m.sufficient), 0) sufficient, AVG(m.latency_ms) latency "
            "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.user_id = ? AND m.role = 'assistant'", (user_id,)).fetchone()
        by_tool = {r["tool"]: r["n"] for r in c.execute(
            "SELECT COALESCE(m.tool, 'unknown') tool, COUNT(*) n FROM messages m "
            "JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.user_id = ? AND m.role = 'assistant' GROUP BY 1", (user_id,))}
    return {"conversations": convs, "queries": a["n"], "by_tool": by_tool, "queries_rewritten": a["rewritten"] or 0,
            "answers_evaluated": a["evaluated"], "answers_sufficient": a["sufficient"],
            "avg_latency_ms": int(a["latency"] or 0)}


def import_conversation(db: ServiceDB, conv: dict, messages: list[dict]) -> tuple[bool, int]:
    """Copy a conversation and its messages as they are (ids, timestamps); rows already there are left alone, so a
    second run copies nothing. Returns (conversation created, messages created)."""
    with db.tx() as c:
        created = c.execute("INSERT OR IGNORE INTO conversations (id, user_id, title, created_at, updated_at) "
                            "VALUES (:id, :user_id, :title, :created_at, :updated_at)", conv).rowcount
        n = 0
        for m in messages:
            n += c.execute(
                "INSERT OR IGNORE INTO messages (id, conversation_id, role, content, tool, meta, created_at, "
                "latency_ms, rewritten, sufficient) VALUES (:id, :conversation_id, :role, :content, :tool, :meta, "
                ":created_at, :latency_ms, :rewritten, :sufficient)", m).rowcount
    return bool(created), n
