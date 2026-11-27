"""The retrieval half of the Document service in one call: hybrid search -> top-K -> rerank -> MMR.

retrieval.retrieve() finds the candidates (every query variant embedded, dense + keyword lists fused by RRF); when a
rerank_query is given, rerank.score() reads the question and each passage together and rerank.zone() turns the best
score into a confidence zone; for a zone other than "low", rerank.mmr_select() marks the diverse passages to answer
from. When the reranker can't score (off, busy, failed) the candidates come back as retrieved with reranked=False, and
the orchestrator runs its own LLM relevance check on them.
"""
from services.document import rerank, retrieval


def search(queries: list[str], user_id: str, *, k: int | None = None, min_score: float | None = None,
           doc_id: str | None = None, mode: str | None = None, rerank_query: str | None = None,
           mmr: bool = False) -> dict:
    if not (user_id or "").strip():
        raise ValueError("user_id must not be empty")
    hits = retrieval.retrieve(queries, user_id, k=k, min_score=min_score, mode=mode, doc_id=doc_id)
    out = {"passages": hits, "reranked": False, "zone": "", "best_relevance": None}
    if not (rerank_query or "").strip() or not hits:
        return out
    ranked = rerank.score(rerank_query, hits)
    if ranked is None:
        return out
    best = ranked[0]["relevance"] if ranked else None
    zone = rerank.zone(best)
    if mmr and ranked and zone != "low":
        for order, h in enumerate(rerank.mmr_select(ranked), start=1):
            h["selected"] = order
    return {"passages": ranked, "reranked": True, "zone": zone, "best_relevance": best}
