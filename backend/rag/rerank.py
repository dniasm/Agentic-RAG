"""Stage 2: cross-encoder reranking, confidence zones, and MMR selection.

Cross-encoder: reads the question and a passage TOGETHER and outputs how well the passage answers it
(more accurate than comparing two separately computed vectors). Runs locally: no API, no quota.
Its score, squashed to 0-1, is the "confidence" used to decide whether the documents cover a question:

    best relevance >= RELEVANCE_HIGH  -> "high": answer from the documents
    between the two thresholds       -> "mid":  something related exists; reword and search once more
    best relevance <  RELEVANCE_LOW   -> "low":  the documents don't cover it; go to the next source

MMR (Maximal Marginal Relevance) then picks the final passages one at a time, each time taking the one
that maximises   lambda * relevance - (1 - lambda) * (max similarity to passages already picked),
so near-duplicates (our chunks overlap by 200 characters) don't crowd out other useful passages.

The model loads in a background thread at startup (importing torch can take over a minute on Windows);
until it is ready, `available()` is False and callers fall back to the previous behaviour.
"""
import logging
import math
import threading

import numpy as np

from backend.config.settings import settings

log = logging.getLogger(__name__)

_model = None
_load_failed = False
_lock = threading.Lock()


def _load():
    global _model, _load_failed
    with _lock:
        if _model is not None or _load_failed:
            return
        try:
            from sentence_transformers import CrossEncoder
            kwargs = {"max_length": settings.reranker_max_length}
            try:  # use the downloaded copy if there is one: no network checks, works offline
                _model = CrossEncoder(settings.reranker_model, local_files_only=True, **kwargs)
            except Exception:  # first run: download it (~90 MB, once)
                _model = CrossEncoder(settings.reranker_model, **kwargs)
            log.info("Cross-encoder %s loaded", settings.reranker_model)
        except Exception as e:  # missing package, no network on first download, ...
            _load_failed = True
            log.warning("Cross-encoder unavailable, keeping the previous relevance check: %s", e)


def warm_up_in_background() -> None:
    if settings.rerank_enabled:
        threading.Thread(target=_load, name="cross-encoder-loader", daemon=True).start()


def available() -> bool:
    return settings.rerank_enabled and _model is not None


def ensure_loaded() -> bool:
    """Load synchronously (used by the benchmark and scripts)."""
    _load()
    return available()


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def score(query: str, hits: list[dict]) -> list[dict]:
    """Attach "relevance" (0-1) to each hit and return them sorted by it, best first."""
    if not hits:
        return []
    pairs = [(query, h["content"][:2000]) for h in hits]
    logits = _model.predict(pairs, batch_size=32, show_progress_bar=False)
    for h, logit in zip(hits, logits):
        h["relevance"] = round(_sigmoid(float(logit)), 4)
    return sorted(hits, key=lambda h: h["relevance"], reverse=True)


def zone(best_relevance: float | None) -> str:
    if best_relevance is None or best_relevance < settings.relevance_low:
        return "low"
    return "high" if best_relevance >= settings.relevance_high else "mid"


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
