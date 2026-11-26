"""The Document service's own SQLite file (document.db).

  page_text         OCR text of a scanned page, keyed by the rendered image (services/document/ocr.py)
  query_embeddings  a question's embedding, keyed by model + dimension + text (services/document/embedding_cache.py)
  documents, renames, edits, undone_edits, batches, batch_tasks, sessions: the library catalog (catalog.py), real data

The two caches only save requests; the catalog tables are the library's record (back document.db up). One shared
handle per process.
"""
import threading

from services.common.sqlite_db import ServiceDB

SCHEMA = """
CREATE TABLE IF NOT EXISTS page_text (key TEXT PRIMARY KEY, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS query_embeddings (key TEXT PRIMARY KEY, vector TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    original_name TEXT NOT NULL,
    file_name TEXT NOT NULL,          -- name of the stored file in the orchestrator's library folder (follows the title)
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
CREATE TABLE IF NOT EXISTS undone_edits (
    id INTEGER PRIMARY KEY,           -- the edit's own id: a redo brings the edit back as it was
    doc_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    detail TEXT NOT NULL,
    backup TEXT NOT NULL,             -- the file before the edit (what the undo put back)
    redo TEXT NOT NULL,               -- the file with the edit (what a redo puts back), same folder
    ext TEXT NOT NULL,                -- the edited file's type (a PDF edit may have made it Markdown)
    at TEXT NOT NULL,                 -- when the edit was made
    undone_at TEXT NOT NULL,
    undo_order INTEGER NOT NULL DEFAULT 0   -- counts up with each undo: undone_at has whole seconds, and id is the
                                            -- edit's own (an older edit undone after a newer one has the lower id)
);
CREATE INDEX IF NOT EXISTS documents_owner ON documents (owner);
CREATE INDEX IF NOT EXISTS renames_doc ON renames (doc_id);
CREATE INDEX IF NOT EXISTS edits_doc ON edits (doc_id);
CREATE INDEX IF NOT EXISTS tasks_status ON batch_tasks (status);
CREATE INDEX IF NOT EXISTS tasks_batch ON batch_tasks (batch_id, position);
"""

_lock = threading.Lock()
_db: ServiceDB | None = None


def open_db(path=None) -> ServiceDB:
    return ServiceDB("document", SCHEMA, path)


def shared() -> ServiceDB:
    """The process-wide handle the caches use (the data folder is read when it is first needed)."""
    global _db
    with _lock:
        if _db is None:
            _db = open_db()
        return _db


def close_shared() -> None:
    global _db
    with _lock:
        if _db is not None:
            _db.close()
            _db = None
