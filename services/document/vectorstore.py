"""The Document service's passages: a Qdrant collection with dense (Gemini) and sparse (BM25) vectors.

Uses a Qdrant server when QDRANT_URL is set, otherwise this service's own embedded on-disk folder (an embedded Qdrant
can be opened by one process only, so the orchestrator no longer opens one).
Each point has two named vectors:
  "dense" - Gemini embedding (meaning)
  "bm25"  - sparse BM25 keyword vector (see rag/sparse.py); Qdrant applies IDF at query time
Payloads follow the LangChain layout ({"page_content", "metadata"}).
"""
import threading
from functools import lru_cache
from pathlib import Path

from qdrant_client import QdrantClient, models

from backend.config.settings import settings
from services.common import config

# The embedded (local-path) Qdrant client is not safe for concurrent writers.
_lock = threading.RLock()


def default_path() -> Path:
    return config.data_dir() / "document_qdrant"


@lru_cache
def get_client() -> QdrantClient:
    if settings.qdrant_url:
        client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    else:
        try:
            client = QdrantClient(path=str(default_path()))
        except RuntimeError as e:
            if "already accessed" in str(e):
                raise RuntimeError(
                    "The local vector store is already in use by another Document service process. "
                    "Only one can run at a time: stop the other one (or end stray python.exe processes), then start again."
                ) from e
            raise
    _ensure_collection(client)
    return client


DENSE = "dense"
SPARSE = "bm25"


def _ensure_collection(client: QdrantClient) -> None:
    name = settings.collection_name
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config={DENSE: models.VectorParams(size=settings.embedding_dim, distance=models.Distance.COSINE)},
            sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
    else:
        vectors = client.get_collection(name).config.params.vectors
        if not isinstance(vectors, dict) or DENSE not in vectors:
            raise RuntimeError(
                f"Collection '{name}' uses the old single-vector layout. Run "
                "`python -m backend.scripts.migrate_hybrid` or set COLLECTION_NAME to a new collection."
            )
        size = vectors[DENSE].size
        if size != settings.embedding_dim:
            raise RuntimeError(
                f"Collection '{name}' has vector size {size} but EMBEDDING_DIM={settings.embedding_dim}. "
                "Use a new COLLECTION_NAME or delete the old collection."
            )
    if settings.qdrant_url:
        # Payload indexes only matter (and are only supported) on a real server.
        for field in ("metadata.user_id", "metadata.doc_id"):
            client.create_payload_index(name, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD)


def user_filter(user_id: str, doc_id: str | None = None) -> models.Filter:
    must = [models.FieldCondition(key="metadata.user_id", match=models.MatchValue(value=user_id))]
    if doc_id:
        must.append(models.FieldCondition(key="metadata.doc_id", match=models.MatchValue(value=doc_id)))
    return models.Filter(must=must)


def upsert(points: list[models.PointStruct]) -> None:
    client = get_client()
    with _lock:
        for i in range(0, len(points), 128):
            client.upsert(settings.collection_name, points=points[i:i + 128], wait=True)


def search_dense(vector: list[float], user_id: str, limit: int, doc_id: str | None = None) -> list[models.ScoredPoint]:
    """Meaning search: score = cosine similarity."""
    client = get_client()
    with _lock:
        return client.query_points(
            settings.collection_name, query=vector, using=DENSE,
            query_filter=user_filter(user_id, doc_id), limit=limit, with_payload=True,
        ).points


def search_sparse(vector: models.SparseVector, user_id: str, limit: int, doc_id: str | None = None) -> list[models.ScoredPoint]:
    """Keyword (BM25) search: score = BM25. Also returns each hit's dense vector, so callers can
    compute the cosine similarity of keyword-only hits."""
    if not vector.indices:
        return []
    client = get_client()
    with _lock:
        return client.query_points(
            settings.collection_name, query=vector, using=SPARSE,
            query_filter=user_filter(user_id, doc_id), limit=limit, with_payload=True, with_vectors=[DENSE],
        ).points


def get_dense_vectors(ids: list[str]) -> list[list[float] | None]:
    """Stored dense vectors for the given chunk ids (in the same order). No embedding requests."""
    client = get_client()
    with _lock:
        points = client.retrieve(settings.collection_name, ids=ids, with_vectors=[DENSE], with_payload=False)
    by_id = {str(p.id): (p.vector or {}).get(DENSE) if isinstance(p.vector, dict) else None for p in points}
    return [by_id.get(str(i)) for i in ids]


def delete_document_vectors(user_id: str, doc_id: str) -> None:
    client = get_client()
    with _lock:
        client.delete(
            settings.collection_name,
            points_selector=models.FilterSelector(filter=user_filter(user_id, doc_id)),
            wait=True,
        )


def close_client() -> None:
    """Close the shared Qdrant client during application shutdown."""
    if get_client.cache_info().currsize:
        client = get_client()
        client.close()
        get_client.cache_clear()


def stored_vectors(user_id: str, doc_id: str) -> dict[str, list[float]]:
    """The dense vector of each chunk the document has in the index now, by the chunk's text."""
    client = get_client()
    vectors, offset = {}, None
    with _lock:
        while True:
            points, offset = client.scroll(settings.collection_name, scroll_filter=user_filter(user_id, doc_id),
                                           limit=256, offset=offset, with_payload=["page_content"],
                                           with_vectors=[DENSE])
            for p in points:
                vector = p.vector.get(DENSE) if isinstance(p.vector, dict) else p.vector
                if vector is not None:
                    vectors[p.payload["page_content"]] = vector
            if offset is None:
                return vectors


def delete_chunks_from(user_id: str, doc_id: str, first_stale: int) -> None:
    """Drop a document's chunks from index `first_stale` on (chunk ids are doc_id:index, so the rest were overwritten)."""
    stale = user_filter(user_id, doc_id)
    stale.must.append(models.FieldCondition(key="metadata.chunk_index", range=models.Range(gte=first_stale)))
    client = get_client()
    with _lock:
        client.delete(settings.collection_name, points_selector=models.FilterSelector(filter=stale), wait=True)


def set_chunk_fields(user_id: str, doc_id: str, fields: dict) -> None:
    """Update metadata fields on every chunk of a document (no re-embedding)."""
    client = get_client()
    with _lock:
        client.set_payload(settings.collection_name, payload=fields, key="metadata", wait=True,
                           points=models.FilterSelector(filter=user_filter(user_id, doc_id)))


def count(user_id: str | None = None, doc_id: str | None = None) -> int:
    client = get_client()
    flt = user_filter(user_id, doc_id) if user_id else None
    with _lock:
        return client.count(settings.collection_name, count_filter=flt, exact=True).count


def import_points(points: list[models.PointStruct]) -> int:
    """Points exactly as given; ids already stored are left alone. Returns how many were created."""
    client = get_client()
    with _lock:
        have = {str(r.id) for r in client.retrieve(settings.collection_name, ids=[p.id for p in points],
                                                   with_payload=False, with_vectors=False)}
        fresh = [p for p in points if str(p.id) not in have]
        for i in range(0, len(fresh), 128):
            client.upsert(settings.collection_name, points=fresh[i:i + 128], wait=True)
    return len(fresh)
