"""Stage 2: reranking, confidence zones, and MMR selection.

A reranker reads the question and a passage TOGETHER and scores how well the passage answers it (more
accurate than comparing two separately computed vectors). It is Voyage AI's hosted rerank API: no torch and
no model in memory, so the app can be deployed on a small server. (A local MiniLM cross-encoder was used until
2026-11-02 and removed on 2026-11-14; Voyage scored better on all 74 benchmark questions.)

If Voyage can't score (down, rate-limited or no key), score() returns None and the caller falls back to the
LLM relevance check.

The best score is the "confidence" that decides whether the documents cover a question (thresholds
calibrated on the benchmark):

    best relevance >= high  -> "high": answer from the documents
    between the two         -> "mid":  something related exists; reword and search once more
    best relevance <  low   -> "low":  the documents don't cover it; go to the next source

MMR (Maximal Marginal Relevance) then picks the final passages one at a time, each time taking the one
that maximises   lambda * relevance - (1 - lambda) * (max similarity to passages already picked),
so near-duplicates (our chunks overlap by 200 characters) don't crowd out other useful passages.
"""
import logging
import time

import numpy as np

from backend.config import voyage
from backend.config.settings import settings

log = logging.getLogger(__name__)

PASSAGE_CHARS = 2000  # characters of each passage sent to the reranker


def available() -> bool:
    """A reranker can be tried for this request."""
    return settings.rerank_enabled and bool(settings.voyage_api_key)


def ensure_loaded() -> bool:
    """Whether the reranker is ready (benchmarks call this before a run)."""
    return available()


# ---------------------------------------------------------------- Voyage
def _score_voyage(query: str, texts: list[str]) -> list[float]:
    # Highest priority on the shared Voyage limit; if no slot frees up in time, score() falls back.
    result = voyage.call(voyage.RERANK, lambda c: c.rerank(query, texts, model=settings.voyage_rerank_model,
                                                         truncation=True))
    scores = [0.0] * len(texts)
    for r in result.results:
        scores[r.index] = float(r.relevance_score)
    return scores


# ---------------------------------------------------------------- scoring and zones
def score(query: str, hits: list[dict]) -> list[dict] | None:
    """Attach "relevance" (0-1) and "scorer" to each hit and return them sorted best first.
    Returns None if the reranker could not score them (the caller then uses the LLM relevance check)."""
    if not hits:
        return []
    if not available():
        return None
    texts = [h["content"][:PASSAGE_CHARS] for h in hits]
    started = time.perf_counter()
    try:
        scores = _score_voyage(query, texts)
    except Exception as e:  # rate limit, network, service error
        log.warning("Reranker voyage failed (%s): %s", type(e).__name__, str(e)[:200])
        return None
    log.info("Reranked %d passages with voyage in %.2fs", len(hits), time.perf_counter() - started)
    for h, s in zip(hits, scores):
        h["relevance"], h["scorer"] = round(s, 4), "voyage"
    return sorted(hits, key=lambda h: h["relevance"], reverse=True)


def zone(best_relevance: float | None) -> str:
    high, low = settings.voyage_relevance_high, settings.voyage_relevance_low
    if best_relevance is None or best_relevance < low:
        return "low"
    return "high" if best_relevance >= high else "mid"


# ---------------------------------------------------------------- MMR
def _cos_matrix(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    unit = vectors / np.where(norms == 0, 1, norms)
    return unit @ unit.T


def mmr_select(hits: list[dict], k: int | None = None, lam: float | None = None) -> list[dict]:
    """Pick k passages from the reranked hits, trading relevance against redundancy."""
    from backend.rag import vectorstore

    k = k or settings.retrieval_k
    lam = settings.mmr_lambda if lam is None else lam
    pool = hits[: settings.rerank_top_n]
    if len(pool) <= 1:
        return pool
    vectors = vectorstore.get_dense_vectors([h["id"] for h in pool])
    if any(v is None for v in vectors):  # can't measure redundancy: keep the relevance order
        return pool[:k]
    sim = _cos_matrix(np.asarray(vectors, dtype=float))
    rel = np.array([h["relevance"] for h in pool])
    chosen = [0]  # the most relevant passage always goes first
    while len(chosen) < min(k, len(pool)):
        rest = [i for i in range(len(pool)) if i not in chosen]
        best = max(rest, key=lambda i: lam * rel[i] - (1 - lam) * max(sim[i][j] for j in chosen))
        chosen.append(best)
    return [pool[i] for i in chosen]
