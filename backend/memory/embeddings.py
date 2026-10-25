import logging
from functools import lru_cache

import voyageai

from backend.config.settings import settings

log = logging.getLogger(__name__)

@lru_cache(maxsize=1)
def get_voyage_client() -> voyageai.Client:
    """Create and reuse a Voyage API client."""
    if not settings.voyage_api_key:
        raise RuntimeError(
            "VOYAGE_API_KEY is missing from the configuration."
        )

    return voyageai.Client(api_key=settings.voyage_api_key)


def _embed(text: str, input_type: str) -> list[float]:
    """Generate a Voyage embedding for memory storage or retrieval."""
    if not text or not text.strip():
        raise ValueError("Text to embed cannot be empty.")
    
    dimensions = settings.voyage_memory_dimensions

    result = get_voyage_client().embed(
        texts=[text.strip()],
        model=settings.voyage_memory_model,
        input_type=input_type,
        output_dimension=dimensions,
    )

    vector = result.embeddings[0]

    if len(vector) != dimensions:
        raise RuntimeError(
            f"Expected {dimensions} embedding dimensions, "
            f"got {len(vector)}."
        )

    return vector


def embed_memory(text: str) -> list[float]:
    """Embed a saved memory for storage."""
    return _embed(text, input_type="document")


def embed_memory_query(text: str) -> list[float]:
    """Embed a user query for semantic memory retrieval."""
    return _embed(text, input_type="query")
