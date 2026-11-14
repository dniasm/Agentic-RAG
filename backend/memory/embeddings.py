"""Voyage embeddings for conversation memory, through the shared client and rate limit (config/voyage.py)."""
import logging

from backend.config import voyage
from backend.config.settings import settings

log = logging.getLogger(__name__)


def _embed(text: str, input_type: str, priority: int) -> list[float]:
    """Generate a Voyage embedding for memory storage or retrieval."""
    if not text or not text.strip():
        raise ValueError("Text to embed cannot be empty.")

    dimensions = settings.voyage_memory_dimensions

    result = voyage.call(priority, lambda c: c.embed(
        texts=[text.strip()],
        model=settings.voyage_memory_model,
        input_type=input_type,
        output_dimension=dimensions,
    ))

    vector = result.embeddings[0]

    if len(vector) != dimensions:
        raise RuntimeError(
            f"Expected {dimensions} embedding dimensions, "
            f"got {len(vector)}."
        )

    return vector


def embed_memory(text: str) -> list[float]:
    """Embed a saved memory for storage."""
    return _embed(text, input_type="document", priority=voyage.SAVE)


def embed_memory_query(text: str) -> list[float]:
    """Embed a user query for semantic memory retrieval."""
    return _embed(text, input_type="query", priority=voyage.RECALL)
