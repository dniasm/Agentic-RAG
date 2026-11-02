"""Reranker calibration: Voyage vs the local cross-encoder, on the saved candidate pools.

run_selection.py --collect saved, for each question, the 20 candidates the hybrid search found (with the local
cross-encoder's scores). This script rescores the same candidates with Voyage, so the rerankers are compared on
identical input, with no search, no embedding quota and no Qdrant (it can run with the backend running).

For each reranker it reports:
    ranking   top-1 / top-3 / top-5 hit rate and MRR@10 of the first relevant passage (answerable questions)
    zones     the best score of every question, answerable vs out-of-scope, and the thresholds that separate them
    cost      Voyage tokens and time

    python benchmark/calibrate_reranker.py                      # score (cached per model) + report
    python benchmark/calibrate_reranker.py --models rerank-2.5-lite
    python benchmark/calibrate_reranker.py --report-only        # no API calls
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.config.settings import settings  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"
POOLS = RESULTS / "selection_pool.json"
OUT = RESULTS / "rerank_calibration.json"
PASSAGE_CHARS = 2000  # same as backend/rag/rerank.py


def _is_relevant(hit: dict, q: dict) -> bool:
    from common import is_relevant
    return is_relevant(hit, q)


def _ids(pool: list[dict]) -> str:
    """Fingerprint of a candidate pool, so cached scores are only reused for identical candidates."""
    import hashlib
    return hashlib.sha1("|".join(h["id"] for h in pool).encode()).hexdigest()[:16]


def load():
    pools = json.loads(POOLS.read_text(encoding="utf8"))["pools"]
    questions = {q["id"]: q for q in json.loads((ROOT / "benchmark" / "questions.json").read_text(encoding="utf8"))["questions"]}
    cache = json.loads(OUT.read_text(encoding="utf8")) if OUT.exists() else {"scores": {}}
    return pools, questions, cache


def score_voyage(model: str, pools: dict, questions: dict, cache: dict) -> None:
    import voyageai
    client = voyageai.Client(api_key=settings.voyage_api_key, max_retries=0, timeout=30)
    done = cache["scores"].setdefault(model, {})
    for qid, pool in pools.items():
        if qid not in questions:
            continue
        if qid in done and done[qid].get("ids") == _ids(pool):
            continue  # same candidates as last time: reuse the scores
        texts = [h["content"][:PASSAGE_CHARS] for h in pool]
        for attempt in range(6):
            try:
                t = time.perf_counter()
                r = client.rerank(questions[qid]["question"], texts, model=model, truncation=True)
                seconds = time.perf_counter() - t
                break
            except voyageai.error.RateLimitError:
                print(f"   rate-limited, waiting 25s ({qid})")
                time.sleep(25)
        else:
            raise RuntimeError("still rate-limited after 6 tries")
        scores = [0.0] * len(texts)
        for x in r.results:
            scores[x.index] = round(float(x.relevance_score), 4)
        done[qid] = {"scores": scores, "ids": _ids(pool), "tokens": r.total_tokens, "seconds": round(seconds, 2)}
        OUT.write_text(json.dumps(cache, indent=1), encoding="utf8")
        print(f"   {model} {qid}: best {max(scores):.3f}  {r.total_tokens} tokens  {seconds:.2f}s")


def evaluate(name: str, scores_of, pools: dict, questions: dict) -> dict:
    ranks, best_in, best_out = [], {}, {}
    for qid, pool in pools.items():
        q = questions.get(qid)
        scores = scores_of(qid, pool)
        if q is None or scores is None:
            continue
        order = sorted(range(len(pool)), key=lambda i: scores[i], reverse=True)
        best = scores[order[0]]
        if q["shape"] == "out_of_scope":
            best_out[qid] = best
            continue
        best_in[qid] = best
        rank = next((pos for pos, i in enumerate(order, 1) if _is_relevant(pool[i], q)), None)
        ranks.append(rank)
    n = len(ranks)
    hit = lambda k: sum(1 for r in ranks if r and r <= k) / n  # noqa: E731
    mrr = sum(1 / r for r in ranks if r and r <= 10) / n
    return {
        "reranker": name, "answerable": n, "out_of_scope": len(best_out),
        "top1": round(hit(1), 3), "top3": round(hit(3), 3), "top5": round(hit(5), 3), "mrr10": round(mrr, 3),
        "missed_in_pool": sum(1 for r in ranks if r is None),
        "best_answerable": dict(sorted(best_in.items(), key=lambda kv: kv[1])),
        "best_out_of_scope": dict(sorted(best_out.items(), key=lambda kv: kv[1], reverse=True)),
    }


def report(models: list[str], pools: dict, questions: dict, cache: dict) -> list[dict]:
    rows = [evaluate("local MiniLM", lambda qid, pool: [h["relevance"] for h in pool], pools, questions)]
    for m in models:
        got = cache["scores"].get(m, {})
        rows.append(evaluate(m, lambda qid, pool, got=got: got[qid]["scores"]
                             if qid in got and got[qid].get("ids") == _ids(pool) else None, pools, questions))
        if got:
            rows[-1]["avg_tokens"] = round(sum(v["tokens"] for v in got.values()) / len(got))
            rows[-1]["avg_seconds"] = round(sum(v["seconds"] for v in got.values()) / len(got), 2)
    for r in rows:
        ins, outs = list(r["best_answerable"].values()), list(r["best_out_of_scope"].values())
        print(f"\n== {r['reranker']}: {r['answerable']} answerable, {r['out_of_scope']} out of scope")
        print(f"   top-1 {r['top1']:.0%}  top-3 {r['top3']:.0%}  top-5 {r['top5']:.0%}  MRR@10 {r['mrr10']:.3f}  "
              f"(relevant passage not in pool: {r['missed_in_pool']})")
        if "avg_tokens" in r:
            print(f"   ~{r['avg_tokens']} tokens and {r['avg_seconds']} s per question")
        if ins and outs:
            print(f"   lowest answerable best scores: {[round(x, 3) for x in sorted(ins)[:6]]}")
            print(f"   out-of-scope best scores:     {[round(x, 3) for x in sorted(outs, reverse=True)]}")
            print(f"   gap: highest out-of-scope {max(outs):.3f} vs lowest answerable {min(ins):.3f}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="rerank-2.5-lite,rerank-2.5")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    models = [m for m in args.models.split(",") if m]
    pools, questions, cache = load()
    if not args.report_only:
        for m in models:
            score_voyage(m, pools, questions, cache)
    rows = report(models, pools, questions, cache)
    cache["report"] = rows
    cache["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    OUT.write_text(json.dumps(cache, indent=1), encoding="utf8")


if __name__ == "__main__":
    main()
