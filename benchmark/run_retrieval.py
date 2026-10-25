"""Retrieval-only benchmark: no Groq calls, so it can be run as often as needed.

For each question it runs one retrieval stage and records:
  hit@1 / hit@3 / hit@5   a relevant chunk is in the top 1 / 3 / 5
  MRR@10                  1 / rank of the first relevant chunk (0 if not in the top 10)
  in-docs decision        does the stage judge the documents to cover the question?
                          (in-scope questions should be "yes", out-of-scope ones "no")
  latency                 seconds per query

    python benchmark/run_retrieval.py --stage 0
Run with the backend STOPPED (single-process embedded Qdrant).
"""
import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BENCH_DIR, bench_user, is_relevant, load_questions  # noqa: E402

from backend.config.settings import settings  # noqa: E402


# ---------------------------------------------------------------- stages
# Each stage returns (ranked hits, in_docs_decision, top_signal). Hits: dicts with "source" and "content".
def _search(query: str, user: dict, mode: str):
    from backend.rag.retrieval import retrieve
    hits = retrieve([query], user["id"], k=10, min_score=0.0, mode=mode)[:10]
    top = max((h["score"] for h in hits), default=0.0)  # best meaning-similarity
    return hits, top >= settings.rag_skip_score, top


def stage0(query: str, user: dict):
    """Baseline: dense-only search; 'in docs' when the best cosine score >= RAG_SKIP_SCORE."""
    return _search(query, user, "dense")


def stage1(query: str, user: dict):
    """Hybrid: dense + BM25 fused with RRF. Same 'in docs' rule as the baseline (Stage 2 replaces it)."""
    return _search(query, user, "hybrid")


def stage1_bm25_only(query: str, user: dict):
    """Diagnostic (not a pipeline stage): BM25 keyword search alone. Needs no embeddings.
    Its 'in docs' decision is not meaningful (no cosine score), so it always says yes."""
    from backend.rag import sparse, vectorstore
    points = vectorstore.search_sparse(sparse.query_vector(query), user["id"], limit=10)
    hits = [{"source": p.payload.get("metadata", {}).get("source"), "content": p.payload.get("page_content", "")}
            for p in points]
    return hits, True, float(points[0].score) if points else 0.0


def _reranked(query: str, user: dict, use_mmr: bool):
    from backend.rag import rerank
    from backend.rag.retrieval import retrieve
    if not rerank.ensure_loaded():
        raise RuntimeError("cross-encoder could not be loaded")
    t0 = time.perf_counter()
    pool = retrieve([query], user["id"], k=settings.rerank_candidates - 2, min_score=0.0, mode="hybrid")
    t1 = time.perf_counter()
    ranked = rerank.score(query, pool)
    t2 = time.perf_counter()
    best = ranked[0]["relevance"] if ranked else 0.0
    if use_mmr:
        chosen = rerank.mmr_select(ranked)
        ids = {h["id"] for h in chosen}
        ranked = chosen + [h for h in ranked if h["id"] not in ids]
    t3 = time.perf_counter()
    parts = {"search": t1 - t0, "rerank": t2 - t1, "mmr": t3 - t2}
    # "In docs" unless the cross-encoder says there's no real match (mid zone = retry, still docs-first).
    return ranked[:10], rerank.zone(best) != "low", best, parts


def stage2_rerank_only(query: str, user: dict):
    """Diagnostic: hybrid + cross-encoder reranking, without MMR (isolates the reranker's effect)."""
    return _reranked(query, user, use_mmr=False)


def stage2(query: str, user: dict):
    """Stage 2: hybrid + cross-encoder reranking + confidence zones + MMR."""
    return _reranked(query, user, use_mmr=True)


STAGES = {"0": stage0, "1": stage1, "1-bm25": stage1_bm25_only, "2-rerank": stage2_rerank_only, "2": stage2}


# ---------------------------------------------------------------- scoring
def evaluate(stage: str) -> dict:
    from backend.rag import embedding_cache

    user = bench_user()
    run = STAGES[stage]
    rows = []
    emb_before = embedding_cache.stats["misses"]
    for q in load_questions():
        start = time.perf_counter()
        out = run(q["question"], user)
        latency = time.perf_counter() - start
        hits, in_docs, signal = out[:3]
        parts = {k: round(v, 3) for k, v in (out[3] if len(out) > 3 else {}).items()}
        oos = q["category"] == "out_of_scope"
        ranks = [i for i, h in enumerate(hits[:10], 1) if not oos and is_relevant(h, q)]
        first = ranks[0] if ranks else None
        rows.append({
            "id": q["id"], "category": q["category"], "question": q["question"],
            "first_relevant_rank": first,
            "hit1": bool(first and first <= 1), "hit3": bool(first and first <= 3), "hit5": bool(first and first <= 5),
            "rr": (1 / first) if first else 0.0,
            "in_docs": in_docs, "decision_correct": (not in_docs) if oos else in_docs,
            "signal": round(float(signal), 4), "latency": round(latency, 3), "parts": parts,
            "top3": [f"{h.get('source')}" for h in hits[:3]],
        })
    return {"stage": stage, "timestamp": datetime.now().isoformat(timespec="seconds"), "rows": rows,
            "embedding_requests": embedding_cache.stats["misses"] - emb_before}


def summarize(result: dict) -> dict:
    rows = result["rows"]
    in_scope = [r for r in rows if r["category"] != "out_of_scope"]
    oos = [r for r in rows if r["category"] == "out_of_scope"]
    pct = lambda xs: round(100 * sum(xs) / len(xs), 1) if xs else None  # noqa: E731
    summary = {
        "hit@1": pct([r["hit1"] for r in in_scope]), "hit@3": pct([r["hit3"] for r in in_scope]),
        "hit@5": pct([r["hit5"] for r in in_scope]),
        "MRR@10": round(statistics.mean(r["rr"] for r in in_scope), 3),
        "in-scope judged 'in docs'": pct([r["in_docs"] for r in in_scope]),
        "out-of-scope judged 'not in docs'": pct([not r["in_docs"] for r in oos]),
        "median latency (s)": round(statistics.median(r["latency"] for r in rows), 2),
        "Google embedding requests": result.get("embedding_requests"),
        "by_category": {},
    }
    part_names = sorted({k for r in rows for k in r.get("parts", {})})
    if part_names:
        summary["median seconds per part"] = {p: round(statistics.median(r["parts"][p] for r in rows), 3)
                                              for p in part_names}
    groups = defaultdict(list)
    for r in in_scope:
        groups[r["category"]].append(r)
    for cat, rs in sorted(groups.items()):
        summary["by_category"][cat] = {"n": len(rs), "hit@5": pct([r["hit5"] for r in rs]),
                                       "MRR@10": round(statistics.mean(r["rr"] for r in rs), 3)}
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="0", choices=sorted(STAGES))
    args = ap.parse_args()

    result = evaluate(args.stage)
    result["summary"] = summarize(result)
    # Variants of the same stage (e.g. RERANKER_MAX_LENGTH=256) are saved under their own name.
    suffix = f"_len{settings.reranker_max_length}" if args.stage.startswith("2") and settings.reranker_max_length != 512 else ""
    out = BENCH_DIR / "results" / f"retrieval_stage{args.stage}{suffix}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    s = result["summary"]
    print(f"\n=== Retrieval benchmark, stage {args.stage} ({len(result['rows'])} questions) ===")
    for k in ["hit@1", "hit@3", "hit@5", "MRR@10", "in-scope judged 'in docs'",
              "out-of-scope judged 'not in docs'", "median latency (s)", "Google embedding requests",
              "median seconds per part"]:
        if k in s:
            print(f"  {k:<36} {s[k]}")
    print("  by category:")
    for cat, v in s["by_category"].items():
        print(f"    {cat:<12} n={v['n']:<3} hit@5={v['hit@5']:<6} MRR@10={v['MRR@10']}")
    print("  misses (no relevant chunk in top 5):")
    for r in result["rows"]:
        if r["category"] != "out_of_scope" and not r["hit5"]:
            print(f"    {r['id']:<18} first relevant rank={r['first_relevant_rank']}  top3={r['top3']}")
    print(f"  saved to {out.relative_to(BENCH_DIR.parent)}")


if __name__ == "__main__":
    main()
