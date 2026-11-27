"""The library catalog in document.db: documents, rename history, edits and undone edits, the library chat's sessions,
queued batches and their tasks. Moved here from the orchestrator's backend/library/catalog.py with the same semantics;
every function is one atomic operation (a transaction on the service's one connection). Dicts come back in the shapes
the orchestrator's catalog returned, with JSON columns decoded."""
import json
import uuid
from datetime import datetime, timedelta, timezone

from services.document import db

OWNER = "library"   # the owner of documents, chats and batches from before accounts existed

DOCUMENT_FIELDS = {"title", "original_name", "file_name", "ext", "sha256", "original_sha256", "size_bytes", "pages",
                   "chunks", "summary", "keywords", "owner", "status", "error", "conversation_id"}
TASK_FIELDS = {"status", "reply", "actions", "tokens", "started_at", "finished_at", "checkpoint"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _doc(row) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    d["keywords"] = json.loads(d["keywords"] or "[]")
    return d


def _task(row) -> dict:
    d = dict(row)
    d["actions"] = json.loads(d["actions"] or "[]")
    d["payload"] = json.loads(d["payload"]) if d.get("payload") else None
    d["checkpoint"] = json.loads(d["checkpoint"]) if d.get("checkpoint") else None
    return d


def _check_fields(fields: dict, allowed: set) -> None:
    bad = sorted(set(fields) - allowed)
    if bad or not fields:
        raise ValueError(f"cannot update {bad or 'nothing'}: allowed are {sorted(allowed)}")


def _update_document(c, doc_id: str, fields: dict) -> None:
    _check_fields(fields, DOCUMENT_FIELDS)
    fields = dict(fields)
    if "keywords" in fields:
        fields["keywords"] = json.dumps(fields["keywords"], ensure_ascii=False)
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    c.execute(f"UPDATE documents SET {cols} WHERE id = ?", (*fields.values(), doc_id))


def _read(sql: str, args=()) -> list:
    with db.shared().tx() as c:
        return c.execute(sql, args).fetchall()


# ---------------------------------------------------------------- documents
def add_document(*, title: str, original_name: str, file_name: str, ext: str, sha256: str, size_bytes: int,
                 owner: str = OWNER, conversation_id: str | None = None) -> dict:
    doc_id = uuid.uuid4().hex[:12]
    t = now()
    with db.shared().tx() as c:
        c.execute(
            "INSERT INTO documents (id, title, original_name, file_name, ext, sha256, original_sha256, size_bytes, "
            "owner, status, created_at, updated_at, conversation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?, ?)",
            (doc_id, title, original_name, file_name, ext, sha256, sha256, size_bytes, owner, t, t, conversation_id))
        return _doc(c.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone())


def update_document(doc_id: str, fields: dict, rename: tuple | None = None) -> None:
    """Update columns; with `rename` (old_title, new_title, reason) also record it, in the same transaction."""
    with db.shared().tx() as c:
        _update_document(c, doc_id, fields)
        if rename:
            _record_rename(c, doc_id, *rename)


def get_document(doc_id: str) -> dict | None:
    rows = _read("SELECT * FROM documents WHERE id = ?", (doc_id,))
    return _doc(rows[0]) if rows else None


def find_by_hash(sha256: str, owner: str = OWNER) -> dict | None:
    """The owner's document stored with these bytes, or uploaded with them and edited since. Only the owner's own."""
    rows = _read("SELECT * FROM documents WHERE owner = ? AND (sha256 = ? OR original_sha256 = ?) "
                 "ORDER BY sha256 = ? DESC LIMIT 1", (owner, sha256, sha256, sha256))
    return _doc(rows[0]) if rows else None


def list_documents(status: str | None = None, owner: str | None = None) -> list[dict]:
    where, args = [], []
    if status:
        where.append("status = ?"); args.append(status)
    if owner is not None:
        where.append("owner = ?"); args.append(owner)
    sql = "SELECT * FROM documents" + (" WHERE " + " AND ".join(where) if where else "")
    return [_doc(r) for r in _read(sql + " ORDER BY created_at, id", args)]


def delete_document(doc_id: str) -> None:
    with db.shared().tx() as c:
        c.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        c.execute("DELETE FROM renames WHERE doc_id = ?", (doc_id,))
        c.execute("DELETE FROM edits WHERE doc_id = ?", (doc_id,))


def _record_rename(c, doc_id: str, old_title: str, new_title: str, reason: str) -> None:
    c.execute("INSERT INTO renames (doc_id, old_title, new_title, reason, at) VALUES (?, ?, ?, ?, ?)",
              (doc_id, old_title, new_title, reason, now()))


def record_rename(doc_id: str, old_title: str, new_title: str, reason: str) -> None:
    with db.shared().tx() as c:
        _record_rename(c, doc_id, old_title, new_title, reason)


def rename_histories() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in _read("SELECT doc_id, old_title, new_title, reason, at FROM renames ORDER BY id"):
        out.setdefault(r["doc_id"], []).append({k: r[k] for k in ("old_title", "new_title", "reason", "at")})
    return out


def rename_history(doc_id: str) -> list[dict]:
    return [dict(r) for r in _read("SELECT old_title, new_title, reason, at FROM renames WHERE doc_id = ? ORDER BY id",
                                   (doc_id,))]


def used_file_names(exclude_doc: str | None = None) -> set[str]:
    rows = _read("SELECT id, file_name FROM documents")
    return {r["file_name"].replace("\\", "/").lower() for r in rows if r["id"] != exclude_doc}


# ---------------------------------------------------------------- edits and undo
def record_edit(doc_id: str, operation: str, detail: str, backup: str, document_fields: dict | None = None) -> int:
    with db.shared().tx() as c:
        edit_id = c.execute("INSERT INTO edits (doc_id, operation, detail, backup, at) VALUES (?, ?, ?, ?, ?)",
                            (doc_id, operation, detail, backup, now())).lastrowid
        if document_fields:
            _update_document(c, doc_id, document_fields)
    return edit_id


def last_edit(doc_id: str) -> dict | None:
    rows = _read("SELECT id, operation, detail, backup, at FROM edits WHERE doc_id = ? ORDER BY id DESC LIMIT 1", (doc_id,))
    return dict(rows[0]) if rows else None


def edit_exists(edit_id: int) -> bool:
    return bool(_read("SELECT 1 FROM edits WHERE id = ?", (edit_id,)))


def edit_history(doc_id: str) -> list[dict]:
    return [dict(r) for r in _read("SELECT operation, detail, at FROM edits WHERE doc_id = ? ORDER BY id", (doc_id,))]


def delete_edit(edit_id: int) -> None:
    with db.shared().tx() as c:
        c.execute("DELETE FROM edits WHERE id = ?", (edit_id,))


def move_edit_to_undone(edit: dict, redo: str, ext: str, document_fields: dict | None = None) -> None:
    """An undone edit leaves the edit history but is kept, with the edited file (`redo`), until a new edit."""
    with db.shared().tx() as c:
        c.execute("DELETE FROM edits WHERE id = ?", (edit["id"],))
        c.execute("INSERT OR REPLACE INTO undone_edits (id, doc_id, operation, detail, backup, redo, ext, at, "
                  "undone_at, undo_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
                  "(SELECT COALESCE(MAX(undo_order), 0) + 1 FROM undone_edits))",
                  (edit["id"], edit["doc_id"], edit["operation"], edit["detail"], edit["backup"], redo, ext,
                   edit["at"], now()))
        if document_fields:
            _update_document(c, edit["doc_id"], document_fields)


def last_undone(doc_id: str) -> dict | None:
    rows = _read("SELECT * FROM undone_edits WHERE doc_id = ? ORDER BY undo_order DESC, undone_at DESC LIMIT 1", (doc_id,))
    return dict(rows[0]) if rows else None


def restore_undone(undone_id: int, document_fields: dict | None = None) -> None:
    """A redo: the edit is back in the history under its own id (so its Undo button applies again)."""
    with db.shared().tx() as c:
        row = c.execute("SELECT doc_id FROM undone_edits WHERE id = ?", (undone_id,)).fetchone()
        c.execute("INSERT INTO edits (id, doc_id, operation, detail, backup, at) SELECT id, doc_id, operation, "
                  "detail, backup, at FROM undone_edits WHERE id = ?", (undone_id,))
        c.execute("DELETE FROM undone_edits WHERE id = ?", (undone_id,))
        if document_fields and row:
            _update_document(c, row["doc_id"], document_fields)


def clear_undone(doc_id: str) -> list[dict]:
    """A new edit ends the redo history. Returns what was dropped, so its files can be deleted."""
    with db.shared().tx() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM undone_edits WHERE doc_id = ?", (doc_id,)).fetchall()]
        c.execute("DELETE FROM undone_edits WHERE doc_id = ?", (doc_id,))
    return rows


# ---------------------------------------------------------------- sessions
def load_session(session_id: str) -> dict:
    rows = _read("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if not rows:
        return {"id": session_id, "turn": 0, "llm_messages": [], "display": [], "guard": {}, "owner": None}
    row = rows[0]
    return {"id": row["id"], "turn": row["turn"], "llm_messages": json.loads(row["llm_messages"]),
            "display": json.loads(row["display"]), "guard": json.loads(row["guard"]), "owner": row["owner"]}


def save_session(session: dict) -> None:
    """Insert or update (an existing session keeps its owner)."""
    t = now()
    with db.shared().tx() as c:
        c.execute(
            "INSERT INTO sessions (id, turn, llm_messages, display, guard, owner, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET turn = excluded.turn, "
            "llm_messages = excluded.llm_messages, display = excluded.display, guard = excluded.guard, "
            "updated_at = excluded.updated_at",
            (session["id"], session["turn"], json.dumps(session["llm_messages"], ensure_ascii=False),
             json.dumps(session["display"], ensure_ascii=False), json.dumps(session["guard"]),
             session.get("owner") or OWNER, t, t))


# ---------------------------------------------------------------- batches
def _batch(c, batch_id: str) -> dict | None:
    row = c.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if row is None:
        return None
    tasks = c.execute("SELECT * FROM batch_tasks WHERE batch_id = ? ORDER BY position", (batch_id,)).fetchall()
    return {**dict(row), "tasks": [_task(t) for t in tasks]}


def add_batch(session_id: str, items: list, owner: str = OWNER) -> dict:
    """items: (instruction, change dict or None) pairs."""
    batch_id = uuid.uuid4().hex[:10]
    with db.shared().tx() as c:
        c.execute("INSERT INTO batches (id, session_id, owner, created_at) VALUES (?, ?, ?, ?)",
                  (batch_id, session_id, owner, now()))
        for i, (text, change) in enumerate(items, start=1):
            c.execute("INSERT INTO batch_tasks (batch_id, position, instruction, payload, status) "
                      "VALUES (?, ?, ?, ?, 'queued')",
                      (batch_id, i, text, json.dumps(change, ensure_ascii=False) if change else None))
        return _batch(c, batch_id)


def get_batch(batch_id: str) -> dict | None:
    with db.shared().tx() as c:
        return _batch(c, batch_id)


def active_or_recent_batches(hours: float = 1.0, owner: str | None = None) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with db.shared().tx() as c:
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM batches b WHERE (? IS NULL OR owner = ?) AND (created_at >= ? OR EXISTS (SELECT 1 "
            "FROM batch_tasks t WHERE t.batch_id = b.id AND t.status IN ('queued', 'running'))) "
            "ORDER BY created_at DESC, rowid DESC", (owner, owner, since)).fetchall()]
        return [_batch(c, i) for i in ids]


def list_batches(limit: int = 10, owner: str | None = None) -> list[dict]:
    with db.shared().tx() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM batches WHERE (? IS NULL OR owner = ?) "
                                          "ORDER BY created_at DESC, rowid DESC LIMIT ?", (owner, owner, limit)).fetchall()]
        return [_batch(c, i) for i in ids]


def tasks_run_for(session_id: str) -> list[dict]:
    rows = _read("SELECT t.actions, t.finished_at FROM batch_tasks t JOIN batches b ON b.id = t.batch_id "
                 "WHERE b.session_id = ? AND t.finished_at IS NOT NULL ORDER BY t.finished_at, t.id", (session_id,))
    return [{"actions": json.loads(r["actions"] or "[]"), "finished_at": r["finished_at"]} for r in rows]


def pending_tasks_for(session_id: str) -> int:
    return _read("SELECT COUNT(*) FROM batch_tasks t JOIN batches b ON b.id = t.batch_id "
                 "WHERE b.session_id = ? AND t.status IN ('queued', 'running')", (session_id,))[0][0]


_NEXT_QUEUED = ("SELECT t.* FROM batch_tasks t JOIN batches b ON b.id = t.batch_id "
                "WHERE t.status = 'queued' ORDER BY b.created_at, b.rowid, t.position LIMIT 1")


def next_queued_task() -> dict | None:
    rows = _read(_NEXT_QUEUED)
    return _task(rows[0]) if rows else None


def claim_next_task() -> dict | None:
    """The next queued task, marked running with its start time, in one transaction (no two workers get the same)."""
    with db.shared().tx() as c:
        row = c.execute(_NEXT_QUEUED).fetchone()
        if row is None:
            return None
        c.execute("UPDATE batch_tasks SET status = 'running', started_at = ? WHERE id = ?", (now(), row["id"]))
        return _task(c.execute("SELECT * FROM batch_tasks WHERE id = ?", (row["id"],)).fetchone())


def get_task(task_id: int) -> dict | None:
    rows = _read("SELECT * FROM batch_tasks WHERE id = ?", (task_id,))
    return _task(rows[0]) if rows else None


def update_task(task_id: int, fields: dict) -> dict | None:
    """Update columns of a task; returns {"batch_id", "owner"} (for the queue push), or None when there is no such task."""
    _check_fields(fields, TASK_FIELDS)
    fields = dict(fields)
    for key in ("actions", "checkpoint"):
        if fields.get(key) is not None:
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db.shared().tx() as c:
        c.execute(f"UPDATE batch_tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))
        row = c.execute("SELECT t.batch_id, b.owner FROM batch_tasks t JOIN batches b ON b.id = t.batch_id "
                        "WHERE t.id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def cancel_batch(batch_id: str) -> tuple[int, str | None]:
    """Queued tasks of the batch become cancelled. Returns (how many, the batch's owner or None when unknown)."""
    with db.shared().tx() as c:
        n = c.execute("UPDATE batch_tasks SET status = 'cancelled', finished_at = ? "
                      "WHERE batch_id = ? AND status = 'queued'", (now(), batch_id)).rowcount
        row = c.execute("SELECT owner FROM batches WHERE id = ?", (batch_id,)).fetchone()
    return n, (row["owner"] if row else None)


def count_queued_tasks(owner: str | None = None) -> int:
    return _read("SELECT COUNT(*) FROM batch_tasks t JOIN batches b ON b.id = t.batch_id "
                 "WHERE t.status = 'queued' AND (? IS NULL OR b.owner = ?)", (owner, owner))[0][0]


def tasks_with_status(status: str) -> list[dict]:
    return [_task(r) for r in _read("SELECT * FROM batch_tasks WHERE status = ? ORDER BY id", (status,))]


def requeue_running_tasks() -> int:
    with db.shared().tx() as c:
        return c.execute("UPDATE batch_tasks SET status = 'queued', started_at = NULL WHERE status = 'running'").rowcount


def give_legacy_chats_and_batches_to(user_id: str) -> None:
    with db.shared().tx() as c:
        c.execute("UPDATE sessions SET owner = ? WHERE owner = ?", (user_id, OWNER))
        c.execute("UPDATE batches SET owner = ? WHERE owner = ?", (user_id, OWNER))


# ---------------------------------------------------------------- migration
def import_rows(tables: dict[str, list[dict]]) -> dict[str, int]:
    """Copy rows from the orchestrator's app.db under their own ids; rows already here are kept (INSERT OR IGNORE).
    Returns how many were created per table."""
    made = {}
    with db.shared().tx() as c:
        for table, rows in tables.items():
            made[table] = 0
            for row in rows:
                cols = ", ".join(row)
                marks = ", ".join("?" for _ in row)
                made[table] += c.execute(f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({marks})",
                                         tuple(row.values())).rowcount
    return made


def counts() -> dict[str, int]:
    with db.shared().tx() as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("documents", "renames", "edits", "undone_edits", "batches", "batch_tasks", "sessions")}
