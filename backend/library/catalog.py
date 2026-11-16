"""SQLite catalog: documents, their rename history, and chat sessions."""
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.config.settings import settings
from backend.library import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_path: Path | None = None
_version = 0   # goes up with every change to documents or their names: cached search results are stale after it


def version() -> int:
    return _version


def _changed() -> None:
    global _version
    with _lock:
        _version += 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    original_name TEXT NOT NULL,
    file_name TEXT NOT NULL,          -- name of the stored file in data/library (follows the title)
    ext TEXT NOT NULL,
    sha256 TEXT NOT NULL,             -- the stored file now (changes with every edit)
    original_sha256 TEXT,             -- the file as uploaded: re-uploading it is a duplicate, edited or not
    size_bytes INTEGER NOT NULL,
    pages INTEGER,
    chunks INTEGER,
    summary TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '[]',
    owner TEXT NOT NULL DEFAULT 'library',   -- the user whose library it is ("library": before accounts existed)
    status TEXT NOT NULL,             -- processing | indexed | failed
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS renames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    old_title TEXT NOT NULL,
    new_title TEXT NOT NULL,
    reason TEXT NOT NULL,             -- titled (on ingest) | user (through the agent)
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    operation TEXT NOT NULL,          -- replace | append
    detail TEXT NOT NULL,
    backup TEXT NOT NULL,             -- the file as it was before, in data/versions/<doc_id>/
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,         -- the chat that queued it
    owner TEXT NOT NULL DEFAULT 'library',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batch_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    instruction TEXT NOT NULL,
    payload TEXT,                     -- a structured change (run in code, no model), or NULL for free text
    checkpoint TEXT,                  -- the document as it was when a structured change began (doc_id, sha256, title)
    status TEXT NOT NULL,             -- queued | running | done | not_done | failed | cancelled
    reply TEXT NOT NULL DEFAULT '',
    actions TEXT NOT NULL DEFAULT '[]',
    tokens INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    turn INTEGER NOT NULL DEFAULT 0,
    llm_messages TEXT NOT NULL DEFAULT '[]',   -- the model's conversation, tool calls included
    display TEXT NOT NULL DEFAULT '[]',        -- what the chat window shows
    guard TEXT NOT NULL DEFAULT '{}',          -- what the rename guard remembers (see agent.Guard)
    owner TEXT NOT NULL DEFAULT 'library',     -- the user the chat belongs to
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,      -- pbkdf2_sha256$iterations$salt$hash (see backend/library/auth.py)
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash TEXT PRIMARY KEY,      -- sha256 of the login cookie: a stolen database holds no usable tokens
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS undone_edits (
    id INTEGER PRIMARY KEY,           -- the edit's own id: a redo brings the edit back as it was
    doc_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    detail TEXT NOT NULL,
    backup TEXT NOT NULL,             -- the file before the edit (what the undo put back), in data/versions/<doc_id>/
    redo TEXT NOT NULL,               -- the file with the edit (what a redo puts back), same folder
    ext TEXT NOT NULL,                -- the edited file's type (a PDF edit may have made it Markdown)
    at TEXT NOT NULL,                 -- when the edit was made
    undone_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS renames_doc ON renames (doc_id);
CREATE INDEX IF NOT EXISTS edits_doc ON edits (doc_id);
CREATE INDEX IF NOT EXISTS tasks_status ON batch_tasks (status);
CREATE INDEX IF NOT EXISTS tasks_batch ON batch_tasks (batch_id, position);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db() -> sqlite3.Connection:
    """The catalog's tables are in day7's database (settings.database_path), on a connection of their own. It
    follows that setting, as day7's does: tests point it at a temporary file."""
    global _conn, _conn_path
    path = Path(settings.database_path)
    if _conn is not None and _conn_path != path:
        _conn.close()
        _conn = None
        _titles.clear()
    if _conn is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn_path = path
        _conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode = WAL")   # the batch worker writes while chats read
        _conn.execute("PRAGMA synchronous = NORMAL")
        columns = {r[1] for r in _conn.execute("PRAGMA table_info(documents)")}
        if columns and "title" not in columns:
            _conn.close()
            _conn = None
            raise RuntimeError(f"{path} still has day7's old documents table: "
                               "run python -m backend.scripts.merge_library first")
        _conn.executescript(SCHEMA)
        try:  # databases made before structured changes existed
            _conn.execute("ALTER TABLE batch_tasks ADD COLUMN payload TEXT")
            _conn.commit()
        except sqlite3.OperationalError:
            pass
        try:  # databases made before originals were remembered: what is stored now is the best guess
            _conn.execute("ALTER TABLE documents ADD COLUMN original_sha256 TEXT")
            _conn.execute("UPDATE documents SET original_sha256 = sha256 WHERE original_sha256 IS NULL")
            _conn.commit()
        except sqlite3.OperationalError:
            pass
        try:  # databases made before restart recovery existed
            _conn.execute("ALTER TABLE batch_tasks ADD COLUMN checkpoint TEXT")
            _conn.commit()
        except sqlite3.OperationalError:
            pass
        for table in ("documents", "batches", "sessions"):   # databases made before accounts existed
            try:
                _conn.execute(f"ALTER TABLE {table} ADD COLUMN owner TEXT NOT NULL DEFAULT 'library'")
                _conn.commit()
            except sqlite3.OperationalError:
                pass
        _conn.execute("CREATE INDEX IF NOT EXISTS documents_owner ON documents (owner)")
        _conn.commit()
    return _conn


def init() -> None:
    """Open the catalog now (day7's init_db calls this), so a database that needs migrating fails at startup."""
    with _lock:
        _db()


def reset_connection() -> None:
    """Close the connection (the next call reopens it)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
        _titles.clear()


@contextmanager
def tx():
    with _lock:
        conn = _db()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _doc(row) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    d["keywords"] = json.loads(d["keywords"] or "[]")
    return d


# ---------------------------------------------------------------- documents
def add_document(*, title: str, original_name: str, file_name: str, ext: str, sha256: str, size_bytes: int,
                 owner: str = config.OWNER) -> dict:
    doc_id = uuid.uuid4().hex[:12]
    t = now()
    with tx() as c:
        _changed()   # under the transaction's lock: no one reads the new version with the old rows
        c.execute(
            "INSERT INTO documents (id, title, original_name, file_name, ext, sha256, original_sha256, size_bytes, "
            "owner, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?)",
            (doc_id, title, original_name, file_name, ext, sha256, sha256, size_bytes, owner, t, t),
        )
    return get_document(doc_id)


def update_document(doc_id: str, **fields) -> None:
    if "keywords" in fields:
        fields["keywords"] = json.dumps(fields["keywords"], ensure_ascii=False)
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with tx() as c:
        _changed()   # under the transaction's lock: no one reads the new version with the old rows
        c.execute(f"UPDATE documents SET {cols} WHERE id = ?", (*fields.values(), doc_id))


def get_document(doc_id: str) -> dict | None:
    with _lock:
        return _doc(_db().execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone())


def find_by_hash(sha256: str, owner: str = config.OWNER) -> dict | None:
    """The owner's document stored with these bytes, or uploaded with them and edited since. Only the owner's own:
    another user uploading the same file gets their own copy, not a look at someone else's."""
    with _lock:
        return _doc(_db().execute("SELECT * FROM documents WHERE owner = ? AND (sha256 = ? OR original_sha256 = ?) "
                                  "ORDER BY sha256 = ? DESC LIMIT 1", (owner, sha256, sha256, sha256)).fetchone())


def list_documents(status: str | None = None, owner: str | None = None) -> list[dict]:
    """Documents, oldest first: one user's (owner), or everyone's (owner=None, for the server's own work)."""
    where, args = [], []
    if status:
        where.append("status = ?"); args.append(status)
    if owner is not None:
        where.append("owner = ?"); args.append(owner)
    sql = "SELECT * FROM documents" + (" WHERE " + " AND ".join(where) if where else "")
    with _lock:
        return [_doc(r) for r in _db().execute(sql + " ORDER BY created_at, id", args).fetchall()]


_titles: dict[str, tuple[int, list[str]]] = {}   # owner -> (catalog version, titles of indexed documents)


def indexed_titles(owner: str) -> list[str]:
    """Titles of the owner's indexed documents. day7's chat reads them on every message; they are read again
    only after the catalog changed (version())."""
    with _lock:   # the same lock as every write: the version and the rows are read together
        cached = _titles.get(owner)
        if not cached or cached[0] != _version:
            cached = _titles[owner] = (_version, [d["title"] for d in list_documents(status="indexed", owner=owner)])
        return list(cached[1])


def delete_document(doc_id: str) -> None:
    with tx() as c:
        _changed()   # under the transaction's lock: no one reads the new version with the old rows
        c.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        c.execute("DELETE FROM renames WHERE doc_id = ?", (doc_id,))
        c.execute("DELETE FROM edits WHERE doc_id = ?", (doc_id,))


def record_rename(doc_id: str, old_title: str, new_title: str, reason: str) -> None:
    with tx() as c:
        _changed()   # under the transaction's lock: no one reads the new version with the old rows
        c.execute("INSERT INTO renames (doc_id, old_title, new_title, reason, at) VALUES (?, ?, ?, ?, ?)",
                  (doc_id, old_title, new_title, reason, now()))


def rename_histories() -> dict[str, list[dict]]:
    """Every document's rename history in one query (doc_id -> oldest first), for searches and the library list."""
    with _lock:
        rows = _db().execute("SELECT doc_id, old_title, new_title, reason, at FROM renames ORDER BY id").fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["doc_id"], []).append({k: r[k] for k in ("old_title", "new_title", "reason", "at")})
    return out


def rename_history(doc_id: str) -> list[dict]:
    with _lock:
        rows = _db().execute("SELECT old_title, new_title, reason, at FROM renames WHERE doc_id = ? ORDER BY id",
                             (doc_id,)).fetchall()
    return [dict(r) for r in rows]


def record_edit(doc_id: str, operation: str, detail: str, backup: str) -> None:
    with tx() as c:
        c.execute("INSERT INTO edits (doc_id, operation, detail, backup, at) VALUES (?, ?, ?, ?, ?)",
                  (doc_id, operation, detail, backup, now()))


def last_edit(doc_id: str) -> dict | None:
    with _lock:
        row = _db().execute("SELECT id, operation, detail, backup, at FROM edits WHERE doc_id = ? "
                            "ORDER BY id DESC LIMIT 1", (doc_id,)).fetchone()
    return dict(row) if row else None


def edit_exists(edit_id: int) -> bool:
    """False once the edit was undone (undo deletes its record)."""
    with _lock:
        return _db().execute("SELECT 1 FROM edits WHERE id = ?", (edit_id,)).fetchone() is not None


def move_edit_to_undone(edit: dict, redo: str, ext: str) -> None:
    """An undone edit leaves the edit history but is kept, with the edited file (`redo`), until a new edit."""
    with tx() as c:
        c.execute("DELETE FROM edits WHERE id = ?", (edit["id"],))
        c.execute("INSERT OR REPLACE INTO undone_edits (id, doc_id, operation, detail, backup, redo, ext, at, "
                  "undone_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (edit["id"], edit["doc_id"], edit["operation"], edit["detail"], edit["backup"], redo, ext,
                   edit["at"], now()))


def last_undone(doc_id: str) -> dict | None:
    """The edit a redo would bring back: the one undone most recently."""
    with _lock:
        row = _db().execute("SELECT * FROM undone_edits WHERE doc_id = ? ORDER BY undone_at DESC, rowid DESC "
                            "LIMIT 1", (doc_id,)).fetchone()
    return dict(row) if row else None


def restore_undone(undone_id: int) -> None:
    """A redo: the edit is back in the history under its own id (so its Undo button applies again)."""
    with tx() as c:
        c.execute("INSERT INTO edits (id, doc_id, operation, detail, backup, at) SELECT id, doc_id, operation, "
                  "detail, backup, at FROM undone_edits WHERE id = ?", (undone_id,))
        c.execute("DELETE FROM undone_edits WHERE id = ?", (undone_id,))


def clear_undone(doc_id: str) -> list[dict]:
    """A new edit ends the redo history (as a new page does a browser's Forward). Returns what was dropped, so its
    files can be deleted."""
    with tx() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM undone_edits WHERE doc_id = ?", (doc_id,)).fetchall()]
        c.execute("DELETE FROM undone_edits WHERE doc_id = ?", (doc_id,))
    return rows


def delete_edit(edit_id: int) -> None:
    with tx() as c:
        c.execute("DELETE FROM edits WHERE id = ?", (edit_id,))


def edit_history(doc_id: str) -> list[dict]:
    with _lock:
        rows = _db().execute("SELECT operation, detail, at FROM edits WHERE doc_id = ? ORDER BY id", (doc_id,)).fetchall()
    return [dict(r) for r in rows]


def used_file_names(exclude_doc: str | None = None) -> set[str]:
    """Every stored file's path relative to the library folder, lower case."""
    with _lock:
        rows = _db().execute("SELECT id, file_name FROM documents").fetchall()
    return {r["file_name"].replace("\\", "/").lower() for r in rows if r["id"] != exclude_doc}


# ---------------------------------------------------------------- sessions
def load_session(session_id: str) -> dict:
    with _lock:
        row = _db().execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return {"id": session_id, "turn": 0, "llm_messages": [], "display": [], "guard": {}, "owner": None}
    return {"id": row["id"], "turn": row["turn"], "llm_messages": json.loads(row["llm_messages"]),
            "display": json.loads(row["display"]), "guard": json.loads(row["guard"]), "owner": row["owner"]}


def save_session(session: dict) -> None:
    t = now()
    with tx() as c:
        c.execute(
            "INSERT INTO sessions (id, turn, llm_messages, display, guard, owner, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET turn = excluded.turn, "
            "llm_messages = excluded.llm_messages, display = excluded.display, guard = excluded.guard, "
            "updated_at = excluded.updated_at",
            (session["id"], session["turn"], json.dumps(session["llm_messages"], ensure_ascii=False),
             json.dumps(session["display"], ensure_ascii=False), json.dumps(session["guard"]),
             session.get("owner") or config.OWNER, t, t),
        )


# ---------------------------------------------------------------- batches
def _task(row) -> dict:
    d = dict(row)
    d["actions"] = json.loads(d["actions"] or "[]")
    d["payload"] = json.loads(d["payload"]) if d.get("payload") else None
    d["checkpoint"] = json.loads(d["checkpoint"]) if d.get("checkpoint") else None
    return d


def add_batch(session_id: str, items: list, owner: str = config.OWNER) -> dict:
    """items: instruction strings (run by the agent) or (instruction, change dict) pairs (run in code)."""
    batch_id = uuid.uuid4().hex[:10]
    with tx() as c:
        c.execute("INSERT INTO batches (id, session_id, owner, created_at) VALUES (?, ?, ?, ?)",
                  (batch_id, session_id, owner, now()))
        for i, item in enumerate(items, start=1):
            text, change = item if isinstance(item, tuple) else (item, None)
            c.execute("INSERT INTO batch_tasks (batch_id, position, instruction, payload, status) "
                      "VALUES (?, ?, ?, ?, 'queued')",
                      (batch_id, i, text, json.dumps(change, ensure_ascii=False) if change else None))
    return get_batch(batch_id)


def tasks_run_for(session_id: str) -> list[dict]:
    """The tasks a chat queued that have finished, with what each did ({"actions", "finished_at"})."""
    with _lock:
        rows = _db().execute(
            "SELECT t.actions, t.finished_at FROM batch_tasks t JOIN batches b ON b.id = t.batch_id "
            "WHERE b.session_id = ? AND t.finished_at IS NOT NULL ORDER BY t.finished_at, t.id",
            (session_id,)).fetchall()
    return [{"actions": json.loads(r["actions"] or "[]"), "finished_at": r["finished_at"]} for r in rows]


def pending_tasks_for(session_id: str) -> int:
    """How many of the tasks a chat queued are still to run (queued or running)."""
    with _lock:
        return _db().execute(
            "SELECT COUNT(*) FROM batch_tasks t JOIN batches b ON b.id = t.batch_id "
            "WHERE b.session_id = ? AND t.status IN ('queued', 'running')", (session_id,)).fetchone()[0]


def get_batch(batch_id: str) -> dict | None:
    with _lock:
        row = _db().execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if row is None:
            return None
        tasks = _db().execute("SELECT * FROM batch_tasks WHERE batch_id = ? ORDER BY position", (batch_id,)).fetchall()
    return {**dict(row), "tasks": [_task(t) for t in tasks]}


def active_or_recent_batches(hours: float = 1.0, owner: str | None = None) -> list[dict]:
    """What the queue panel shows: batches with tasks still to run, and batches queued in the last `hours`."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with _lock:
        ids = [r["id"] for r in _db().execute(
            "SELECT id FROM batches b WHERE (? IS NULL OR owner = ?) AND (created_at >= ? OR EXISTS (SELECT 1 "
            "FROM batch_tasks t WHERE t.batch_id = b.id AND t.status IN ('queued', 'running'))) "
            "ORDER BY created_at DESC, rowid DESC", (owner, owner, since)).fetchall()]
    return [get_batch(i) for i in ids]


def list_batches(limit: int = 10, owner: str | None = None) -> list[dict]:
    with _lock:
        ids = [r["id"] for r in _db().execute("SELECT id FROM batches WHERE (? IS NULL OR owner = ?) "
                                              "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                                              (owner, owner, limit)).fetchall()]
    return [get_batch(i) for i in ids]


def next_queued_task() -> dict | None:
    with _lock:
        row = _db().execute("SELECT t.* FROM batch_tasks t JOIN batches b ON b.id = t.batch_id "
                            "WHERE t.status = 'queued' ORDER BY b.created_at, b.rowid, t.position LIMIT 1").fetchone()
    return _task(row) if row else None


def get_task(task_id: int) -> dict | None:
    with _lock:
        row = _db().execute("SELECT * FROM batch_tasks WHERE id = ?", (task_id,)).fetchone()
    return _task(row) if row else None


def update_task(task_id: int, **fields) -> None:
    for key in ("actions", "checkpoint"):
        if key in fields and fields[key] is not None:
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with tx() as c:
        c.execute(f"UPDATE batch_tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))


def cancel_batch(batch_id: str) -> int:
    with tx() as c:
        return c.execute("UPDATE batch_tasks SET status = 'cancelled', finished_at = ? "
                         "WHERE batch_id = ? AND status = 'queued'", (now(), batch_id)).rowcount


def count_queued_tasks(owner: str | None = None) -> int:
    with _lock:
        return _db().execute("SELECT COUNT(*) FROM batch_tasks t JOIN batches b ON b.id = t.batch_id "
                             "WHERE t.status = 'queued' AND (? IS NULL OR b.owner = ?)", (owner, owner)).fetchone()[0]


def tasks_with_status(status: str) -> list[dict]:
    with _lock:
        rows = _db().execute("SELECT * FROM batch_tasks WHERE status = ? ORDER BY id", (status,)).fetchall()
    return [_task(r) for r in rows]


def requeue_running_tasks() -> None:
    """Tasks that were running when the server stopped are queued again."""
    with tx() as c:
        c.execute("UPDATE batch_tasks SET status = 'queued', started_at = NULL WHERE status = 'running'")


# ---------------------------------------------------------------- users and logins
def create_user(username: str, password_hash: str) -> dict:
    user_id = uuid.uuid4().hex[:12]
    with tx() as c:
        c.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                  (user_id, username, password_hash, now()))
    return get_user(user_id)


def get_user(user_id: str) -> dict | None:
    with _lock:
        row = _db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def user_by_name(username: str) -> dict | None:
    with _lock:
        row = _db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def count_users() -> int:
    with _lock:
        return _db().execute("SELECT COUNT(*) FROM users").fetchone()[0]


def add_token(user_id: str, token_hash: str, expires_at: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (now(),))
        c.execute("INSERT INTO auth_tokens (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                  (token_hash, user_id, now(), expires_at))


def user_for_token(token_hash: str) -> dict | None:
    with _lock:
        row = _db().execute("SELECT u.* FROM auth_tokens t JOIN users u ON u.id = t.user_id "
                            "WHERE t.token_hash = ? AND t.expires_at > ?", (token_hash, now())).fetchone()
    return dict(row) if row else None


def delete_token(token_hash: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM auth_tokens WHERE token_hash = ?", (token_hash,))


def give_legacy_chats_and_batches_to(user_id: str) -> None:
    """Chats and batches made before accounts existed become the first user's (their documents move separately)."""
    with tx() as c:
        c.execute("UPDATE sessions SET owner = ? WHERE owner = ?", (user_id, config.OWNER))
        c.execute("UPDATE batches SET owner = ? WHERE owner = ?", (user_id, config.OWNER))
