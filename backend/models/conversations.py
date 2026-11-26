"""Conversations and their messages, plus the per-user stats computed from them."""
import json
import uuid

from backend.models.connection import _all, _now, _one, tx

SCHEMA = """
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
    created_at TEXT NOT NULL,
    -- Copied out of meta for /stats, which used to parse every message's JSON (see _STAT_COLUMNS).
    latency_ms INTEGER,
    rewritten INTEGER NOT NULL DEFAULT 0,
    sufficient INTEGER
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, updated_at);
"""


# Stat columns of `messages`, taken from each answer's meta when it is saved.
_STAT_COLUMNS = {"latency_ms": "INTEGER", "rewritten": "INTEGER NOT NULL DEFAULT 0", "sufficient": "INTEGER"}


def _stats_from_meta(meta: dict) -> dict:
    """latency_ms; rewritten (0/1: the question was reworded to search); sufficient (None if the answer wasn't
    evaluated, else 0/1)."""
    evaluation = meta.get("evaluation")
    return {
        "latency_ms": meta.get("latency_ms") or None,
        "rewritten": 1 if meta.get("rewritten_queries") else 0,
        "sufficient": None if not evaluation else int(bool(evaluation.get("sufficient"))),
    }


def _add_message_stat_columns(c) -> None:
    """Databases from before 14 November: add the stat columns and fill them from the stored meta, once."""
    have = {row["name"] for row in c.execute("PRAGMA table_info(messages)")}
    for name, kind in _STAT_COLUMNS.items():
        if name not in have:
            c.execute(f"ALTER TABLE messages ADD COLUMN {name} {kind}")
    if c.execute("SELECT 1 FROM migrations WHERE name = 'message_stats_v1'").fetchone():
        return
    for row in c.execute("SELECT id, meta FROM messages WHERE role = 'assistant'").fetchall():
        try:
            meta = json.loads(row["meta"] or "{}")
        except ValueError:
            continue
        c.execute("UPDATE messages SET latency_ms = :latency_ms, rewritten = :rewritten, sufficient = :sufficient "
                  "WHERE id = :id", {**_stats_from_meta(meta), "id": row["id"]})
    c.execute("INSERT INTO migrations (name, applied_at) VALUES ('message_stats_v1', ?)", (_now(),))



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
    """Changes the title only: updated_at is left alone, so a renamed chat keeps its place in the list."""
    with tx() as c:
        c.execute("UPDATE conversations SET title = ? WHERE id = ? AND user_id = ?",
                  (title.strip()[:60], conv_id, user_id))


def delete_conversation(user_id: str, conv_id: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id))


def add_message(conv_id: str, role: str, content: str, tool: str | None = None, meta: dict | None = None) -> dict:
    msg = {"id": uuid.uuid4().hex, "conversation_id": conv_id, "role": role, "content": content,
           "tool": tool, "meta": json.dumps(meta or {}), "created_at": _now()}
    with tx() as c:
        c.execute(
            "INSERT INTO messages (id, conversation_id, role, content, tool, meta, created_at, latency_ms, rewritten, "
            "sufficient) VALUES (:id, :conversation_id, :role, :content, :tool, :meta, :created_at, :latency_ms, "
            ":rewritten, :sufficient)",
            {**msg, **_stats_from_meta(meta or {})},
        )
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (msg["created_at"], conv_id))
    return {**msg, "meta": meta or {}}


def list_tool_messages(conv_id: str, tool: str) -> list[dict]:
    """One conversation's answers from one tool, oldest first, with their meta."""
    with tx() as c:
        rows = _all(c.execute("SELECT id, meta, created_at FROM messages WHERE conversation_id = ? AND tool = ? "
                              "ORDER BY created_at, rowid", (conv_id, tool)))
    for r in rows:
        r["meta"] = json.loads(r["meta"] or "{}")
    return rows


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
            "SELECT COUNT(*) n, COALESCE(SUM(chunks), 0) chunks FROM documents WHERE owner = ? AND status = 'indexed'",
            (user_id,),
        ))
        convs = _one(c.execute("SELECT COUNT(*) n FROM conversations WHERE user_id = ?", (user_id,)))
        answers = _one(c.execute(
            "SELECT COUNT(*) n, SUM(m.rewritten) rewritten, COUNT(m.sufficient) evaluated, "
            "COALESCE(SUM(m.sufficient), 0) sufficient, AVG(m.latency_ms) latency "
            "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.user_id = ? AND m.role = 'assistant'",
            (user_id,),
        ))
        by_tool = {r["tool"]: r["n"] for r in c.execute(
            "SELECT COALESCE(m.tool, 'unknown') tool, COUNT(*) n FROM messages m "
            "JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.user_id = ? AND m.role = 'assistant' GROUP BY 1",
            (user_id,),
        )}

    return {
        "documents": docs["n"],
        "chunks": docs["chunks"],
        "conversations": convs["n"],
        "queries": answers["n"],
        "by_tool": by_tool,
        "queries_rewritten": answers["rewritten"] or 0,
        "answers_evaluated": answers["evaluated"],
        "answers_sufficient": answers["sufficient"],
        "avg_latency_ms": int(answers["latency"] or 0),
    }

