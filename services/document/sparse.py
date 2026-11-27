"""BM25 keyword vectors for hybrid search.

Each chunk gets a sparse vector: {term id: BM25 term-frequency weight}. Qdrant multiplies in each term's
IDF (how rare it is across the collection) at query time via `Modifier.IDF`, so together they give the
standard BM25 score. Queries are sparse vectors with weight 1 per distinct term.

The tokenizer is services/common/tokens.py (shared with the orchestrator's citation check).
"""
from collections import Counter

import mmh3
from qdrant_client import models

from backend.config.settings import settings
from services.common.tokens import tokenize  # noqa: F401  (re-exported: tests and benchmarks use sparse.tokenize)



def _term_id(term: str) -> int:
    return mmh3.hash(term, signed=False)  # stable 32-bit id per term (collisions are negligible)


def document_vector(text: str) -> models.SparseVector:
    """BM25 term-frequency part for a chunk; Qdrant applies IDF at query time."""
    counts = Counter(tokenize(text))
    length = sum(counts.values()) or 1
    k1, b, avg = settings.bm25_k1, settings.bm25_b, settings.bm25_avg_len
    norm = k1 * (1 - b + b * length / avg)
    weights: dict[int, float] = {}
    for term, tf in counts.items():
        idx = _term_id(term)
        weights[idx] = weights.get(idx, 0.0) + tf * (k1 + 1) / (tf + norm)
    return models.SparseVector(indices=list(weights), values=list(weights.values()))


def query_vector(text: str) -> models.SparseVector:
    ids = sorted({_term_id(t) for t in tokenize(text)})
    return models.SparseVector(indices=ids, values=[1.0] * len(ids))
