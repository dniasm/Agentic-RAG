"""The Memory service's episodes: short summaries of past exchanges, Voyage-embedded, in this service's own embedded
Qdrant folder (not the orchestrator's: an embedded Qdrant can be opened by one process only).

Embedding reuses backend/memory/embeddings.py (the shared Voyage client and rate limit); the key comes from the
environment / settings of this process.
"""
import hashlib
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient, models

from backend.config.settings import settings
from backend.memory import embeddings
from services.common import config

log = logging.getLogger(__name__)


def default_path() -> Path:
    return config.data_dir() / "memory_qdrant"


def episode_id(user_id: str, text: str) -> str:
    """A stable point id for identical user/text pairs."""
    return str(uuid.UUID(hashlib.sha256(f"{user_id}\0{text}".encode("utf-8")).hexdigest()[:32]))


class EpisodeStore:
    def __init__(self, path: str | Path | None = None, collection: str | None = None):
        self.path = Path(path) if path else default_path()
        self.collection = collection or settings.voyage_memory_collection
        self._lock = threading.RLock()   # the embedded client is not safe for concurrent writers
        self.path.mkdir(parents=True, exist_ok=True)
        self.client = QdrantClient(path=str(self.path))
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """Create the collection if missing, or check the vector size of the one there (fails the start on a mismatch)."""
        name, size = self.collection, settings.voyage_memory_dimensions
        with self._lock:
            if self.client.collection_exists(name):
                vectors = self.client.get_collection(name).config.params.vectors
                if isinstance(vectors, dict):
                    raise ValueError(f"Collection '{name}' uses named vectors, but the memory store expects a single unnamed vector.")
                if vectors.size != size:
                    raise ValueError(f"Collection '{name}' has vector size {vectors.size}; expected {size}. "
                                     "Use a new collection name or recreate the collection after backing up any data you need.")
                return
            self.client.create_collection(name, vectors_config=models.VectorParams(size=size, distance=models.Distance.COSINE))
            for field in ("user_id", "conversation_id"):   # to filter by owner and conversation
                self.client.create_payload_index(name, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD)
            log.info("Created episode collection %s (%d dimensions)", name, size)

    def store(self, user_id: str, text: str, kind: str, conversation_id: str | None) -> str:
        user_id, text = (user_id or "").strip(), (text or "").strip()
        if not user_id or not text:
            raise ValueError("user_id and text must not be empty")
        vector = embeddings.embed_memory(text)   # outside the lock: a slow request must not hold up searches
        point_id = episode_id(user_id, text)
        payload = {"user_id": user_id, "text": text, "kind": kind or "episodic", "conversation_id": conversation_id,
                   "updated_at": datetime.now(timezone.utc).isoformat()}
        with self._lock:
            self.client.upsert(self.collection, points=[models.PointStruct(id=point_id, vector=vector, payload=payload)],
                               wait=True)
        return point_id

    def search(self, user_id: str, query: str, limit: int, score_threshold: float | None) -> list[dict[str, Any]]:
        user_id, query = (user_id or "").strip(), (query or "").strip()
        if not user_id:
            raise ValueError("user_id must not be empty")
        if not query or limit <= 0:
            return []
        vector = embeddings.embed_memory_query(query)
        flt = models.Filter(must=[models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))])
        with self._lock:
            points = self.client.query_points(self.collection, query=vector, query_filter=flt, limit=limit,
                                              score_threshold=score_threshold, with_payload=True,
                                              with_vectors=False).points
        out = []
        for p in points:
            payload = p.payload or {}
            if payload.get("user_id") != user_id:   # defense in depth: never return another person's episode
                continue
            out.append({"id": str(p.id), "text": payload.get("text", ""), "kind": payload.get("kind", "episodic"),
                        "conversation_id": payload.get("conversation_id"), "updated_at": payload.get("updated_at"),
                        "score": float(p.score)})
        return out

    def delete_conversation(self, user_id: str, conversation_id: str) -> None:
        if not (user_id or "").strip() or not (conversation_id or "").strip():
            raise ValueError("user_id and conversation_id must not be empty")
        flt = models.Filter(must=[
            models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
            models.FieldCondition(key="conversation_id", match=models.MatchValue(value=conversation_id))])
        with self._lock:
            self.client.delete(self.collection, points_selector=models.FilterSelector(filter=flt), wait=True)

    def import_points(self, episodes: list[dict]) -> int:
        """Points exactly as given (id, vector, payload); ids already stored are left alone. Returns how many were created."""
        size = settings.voyage_memory_dimensions
        for e in episodes:
            if len(e["vector"]) != size or not e["user_id"] or not e["text"]:
                raise ValueError(f"episode {e['id']}: needs a user, a text and a vector of {size} numbers")
        with self._lock:
            have = {str(r.id) for r in self.client.retrieve(self.collection, ids=[e["id"] for e in episodes],
                                                            with_payload=False, with_vectors=False)}
            fresh = [e for e in episodes if e["id"] not in have]
            if fresh:
                self.client.upsert(self.collection, wait=True, points=[models.PointStruct(
                    id=e["id"], vector=e["vector"],
                    payload={"user_id": e["user_id"], "text": e["text"], "kind": e["kind"] or "episodic",
                             "conversation_id": e["conversation_id"], "updated_at": e["updated_at"]}) for e in fresh])
        return len(fresh)

    def count(self) -> int:
        with self._lock:
            return self.client.count(self.collection, exact=True).count

    def close(self) -> None:
        with self._lock:
            self.client.close()
