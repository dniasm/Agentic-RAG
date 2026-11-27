"""On-disk cache for QUESTION embeddings.

Gemini's free tier allows 1,000 embedding requests per day (each chunk or question counts as one).
Questions repeat a lot (the benchmark re-asks the same 35 every run; users re-ask and retry), so each
distinct question is embedded once and reused. Stored in the service's own document.db (services/document/db.py).
Keyed by model + dimension + exact text, so changing the embedding settings never reuses stale vectors.
"""
import hashlib
import json

from backend.config.settings import settings
from services.document import db

# Process-wide counters (read by the benchmark): misses = requests actually sent to Google.
stats = {"hits": 0, "misses": 0}


def _key(text: str) -> str:
    raw = f"{settings.embedding_model}|{settings.embedding_dim}|{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def lookup(key: str):
    with db.shared().tx() as c:
        return c.execute("SELECT vector FROM query_embeddings WHERE key = ?", (key,)).fetchone()


def embed_query(text: str) -> list[float]:
    from backend.config import gemini

    key = _key(text)
    row = lookup(key)
    if row:
        stats["hits"] += 1
        return json.loads(row[0])
    stats["misses"] += 1
    # (no waiting here: when every key is limited, the error goes to the caller as before)
    vector = gemini.with_keys(lambda emb: emb.embed_query(text, task_type="RETRIEVAL_QUERY"), "questions")
    with db.shared().tx() as c:
        c.execute("INSERT OR REPLACE INTO query_embeddings (key, vector) VALUES (?, ?)", (key, json.dumps(vector)))
    return vector
