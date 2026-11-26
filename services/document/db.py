"""The Document service's own SQLite file (document.db): the two caches that save outside requests.

  page_text         OCR text of a scanned page, keyed by the rendered image (services/document/ocr.py)
  query_embeddings  a question's embedding, keyed by model + dimension + text (services/document/embedding_cache.py)

Both are only caches: losing the file costs requests, never data. One shared handle per process.
"""
import threading

from services.common.sqlite_db import ServiceDB

SCHEMA = """
CREATE TABLE IF NOT EXISTS page_text (key TEXT PRIMARY KEY, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS query_embeddings (key TEXT PRIMARY KEY, vector TEXT NOT NULL);
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
