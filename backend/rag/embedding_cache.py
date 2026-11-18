"""On-disk cache for QUESTION embeddings.

Gemini's free tier allows 1,000 embedding requests per day (each chunk or question counts as one).
Questions repeat a lot (the benchmark re-asks the same 35 every run; users re-ask and retry), so each
distinct question is embedded once and reused. Stored in SQLite next to the app database.
Keyed by model + dimension + exact text, so changing the embedding settings never reuses stale vectors.
"""
import hashlib
import json
import sqlite3
import threading
from pathlib import Path

from backend.config.settings import settings

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

# Process-wide counters (read by the benchmark): misses = requests actually sent to Google.
stats = {"hits": 0, "misses": 0}


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = Path(settings.database_path).parent / "embedding_cache.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.execute("CREATE TABLE IF NOT EXISTS query_embeddings (key TEXT PRIMARY KEY, vector TEXT NOT NULL)")
    return _conn


def _key(text: str) -> str:
    raw = f"{settings.embedding_model}|{settings.embedding_dim}|{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def embed_query(text: str) -> list[float]:
    from backend.config import gemini

    key = _key(text)
    with _lock:
        row = _db().execute("SELECT vector FROM query_embeddings WHERE key = ?", (key,)).fetchone()
    if row:
        stats["hits"] += 1
        return json.loads(row[0])
    stats["misses"] += 1
    # (no waiting here: when every key is limited, the error goes to the caller as before)
    vector = gemini.with_keys(lambda emb: emb.embed_query(text, task_type="RETRIEVAL_QUERY"), "questions")
    with _lock:
        _db().execute("INSERT OR REPLACE INTO query_embeddings (key, vector) VALUES (?, ?)", (key, json.dumps(vector)))
        _db().commit()
    return vector
