
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from qdrant_client import models

from backend.config.settings import settings
from backend.memory.embeddings import (
    embed_memory,
    embed_memory_query,
)
from backend.rag.vectorstore import get_client, _lock

log = logging.getLogger(__name__)


def _collection_name() -> str:
    return settings.voyage_memory_collection


def ensure_memory_collection() -> None:
    """Create the semantic-memory collection if it does not exist."""
    client = get_client()
    collection_name = _collection_name()

    with _lock:
        if client.collection_exists(collection_name):
            info = client.get_collection(collection_name)
            vector_config = info.config.params.vectors

            # This implementation expects a single unnamed vector.
            if isinstance(vector_config, dict):
                raise ValueError(
                    f"Collection '{collection_name}' uses named vectors, "
                    "but the memory store expects a single unnamed vector."
                )

            expected_size = settings.voyage_memory_dimensions
            actual_size = vector_config.size

            if actual_size != expected_size:
                raise ValueError(
                    f"Collection '{collection_name}' has vector size "
                    f"{actual_size}; expected {expected_size}. "
                    "Use a new collection name or recreate the collection "
                    "after backing up any data you need."
                )

            return

        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=settings.voyage_memory_dimensions,
                distance=models.Distance.COSINE,
            ),
        )

        # These indexes help Qdrant filter memories by owner and conversation.
        client.create_payload_index(
            collection_name=collection_name,
            field_name="user_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=collection_name,
            field_name="conversation_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )


def _memory_id(user_id: str, text: str) -> str:
    """Generate a stable point ID for identical user/text combinations."""
    raw = f"{user_id}\0{text}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return str(uuid.UUID(digest[:32]))


def save_memory(
    user_id: str,
    text: str,
    kind: str = "episodic",
    conversation_id: str | None = None,
) -> str | None:
    """Embed and save a semantic memory. Returns its Qdrant point ID."""
    if not user_id or not user_id.strip():
        raise ValueError("user_id must not be empty")

    text = text.strip()
    if not text:
        raise ValueError("Memory text must not be empty")

    ensure_memory_collection()

    vector = embed_memory(text)
    point_id = _memory_id(user_id, text)

    payload = {
        "user_id": user_id,
        "text": text,
        "kind": kind,
        "conversation_id": conversation_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    client = get_client()

    with _lock:
        client.upsert(
            collection_name=_collection_name(),
            points=[
                models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload,
                )
            ],
            wait=True,
        )

    log.debug(
        "Saved semantic memory for user_id=%s kind=%s",
        user_id,
        kind,
    )
    return point_id


def search_memories(
    user_id: str,
    query: str,
    limit: int = 5,
    score_threshold: float | None = None,
) -> list[dict[str, Any]]:
    """Search semantic memories belonging only to the specified user."""
    if not user_id or not user_id.strip():
        raise ValueError("user_id must not be empty")

    query = query.strip()
    if not query or limit <= 0:
        return []

    ensure_memory_collection()

    vector = embed_memory_query(query)

    query_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="user_id",
                match=models.MatchValue(value=user_id),
            )
        ]
    )

    client = get_client()

    with _lock:
        response = client.query_points(
            collection_name=_collection_name(),
            query=vector,
            query_filter=query_filter,
            limit=limit,
            score_threshold=score_threshold,
            with_payload=True,
            with_vectors=False,
        )

    results = []

    for point in response.points:
        payload = point.payload or {}

        # Defense in depth: never return another user's memory.
        if payload.get("user_id") != user_id:
            continue

        results.append({
            "id": str(point.id),
            "text": payload.get("text", ""),
            "kind": payload.get("kind", "episodic"),
            "conversation_id": payload.get("conversation_id"),
            "updated_at": payload.get("updated_at"),
            "score": float(point.score),
        })

    return results


def delete_conversation_memories(
    user_id: str,
    conversation_id: str,
) -> None:
    """Delete semantic memories associated with one user's conversation."""
    if not user_id or not user_id.strip():
        raise ValueError("user_id must not be empty")

    if not conversation_id or not conversation_id.strip():
        raise ValueError("conversation_id must not be empty")

    ensure_memory_collection()

    client = get_client()

    delete_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="user_id",
                match=models.MatchValue(value=user_id),
            ),
            models.FieldCondition(
                key="conversation_id",
                match=models.MatchValue(value=conversation_id),
            ),
        ]
    )

    with _lock:
        client.delete(
            collection_name=_collection_name(),
            points_selector=models.FilterSelector(
                filter=delete_filter,
            ),
            wait=True,
        )

    log.debug(
        "Deleted semantic memories for user_id=%s conversation_id=%s",
        user_id,
        conversation_id,
    )
