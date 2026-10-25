"""End-to-end benchmark: real answers through the full agent (costs Groq quota).

For a fixed subset of questions it records which source answered, whether the answer used the expected
document, whether the answer text contains a gold phrase, the evaluator's verdict, latency, time to first
words, and tokens per model.

    python benchmark/run_e2e.py --stage 0 [--gap 20]
Run with the backend STOPPED (single-process embedded Qdrant).
"""
import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BENCH_DIR, bench_user, load_questions  # noqa: E402

from backend.graph.workflow import stream_agent  # noqa: E402
from backend.models import database as db  # noqa: E402

SUBSET = ["ccs-supervisor", "ccs-tdidf", "ccs-weakness", "nlp-smoothing", "rg-vectorize",
          "wiki-bm25-b", "wiki-invindex", "sum-ccs", "oos-australia", "oos-sourdough"]


class TokenCounter(BaseCallbackHandler):
    def __init__(self):
        self.by_model = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0})

    def on_llm_end(self, response, **kwargs):
        for gen in response.generations:
            for g in gen:
                msg = getattr(g, "message", None)
                usage = getattr(msg, "usage_metadata", None) or {}
                model = (getattr(msg, "response_metadata", None) or {}).get("model_name", "?")
                m = self.by_model[model]
                m["calls"] += 1
                m["input"] += usage.get("input_tokens", 0)
                m["output"] += usage.get("output_tokens", 0)


def configure_stage(stage: str) -> None:
    """Switch the pipeline to a given stage, in this process only (the .env is untouched)."""
    from backend.config.settings import settings
    from backend.rag import rerank
    settings.retrieval_mode = "dense" if stage == "0" else "hybrid"
    settings.rerank_enabled = stage not in ("0", "1")
    if settings.rerank_enabled and not rerank.ensure_loaded():
        raise RuntimeError("cross-encoder could not be loaded")
    print(f"stage {stage}: retrieval={settings.retrieval_mode}, rerank={'on' if settings.rerank_enabled else 'off'}")


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).lower()


def _step_times(starts: list[tuple[float, str]], end: float) -> dict[str, float]:
    """Seconds spent in each step: from a step's start signal to the next one (summed if a step repeats)."""
    out: dict[str, float] = defaultdict(float)
    for (t, node), nxt in zip(starts, [s[0] for s in starts[1:]] + [end]):
        out[node] += nxt - t
    return {k: round(v, 2) for k, v in out.items()}


def run_one(q: dict, user: dict, doc_names: list[str]) -> dict:
    from backend.rag import embedding_cache
    from backend.tools import web_search

    counter = TokenCounter()
    emb_before, web_before = embedding_cache.stats["misses"], web_search.stats["Tavily"]
    start = time.perf_counter()
    first_words, final, error, starts = None, {}, None, []
    try:
        for kind, payload in stream_agent(q["question"], user, [], doc_names, callbacks=[counter]):
            now = time.perf_counter() - start
            if kind == "status":
                starts.append((now, payload["node"]))
            elif kind == "draft" and first_words is None:
                first_words = now
            elif kind == "final":
                final = payload
    except Exception as e:  # e.g. a rate limit: record it instead of aborting the whole run
        error = str(e)[:300]
    total = time.perf_counter() - start
    oos = q["category"] == "out_of_scope"
    sources = {s.get("document") for s in final.get("sources", []) if s.get("type") == "document"}
    answer = final.get("answer", "")
    ev = final.get("evaluation") or {}
    return {
        "id": q["id"], "category": q["category"], "question": q["question"],
        "tool": final.get("tool"), "notices": final.get("notices", []),
        "used_expected_doc": (not oos) and bool(sources & set(q["sources"])),
        "answer_has_gold": (not oos) and bool(q["gold"]) and any(_norm(g) in _norm(answer) for g in q["gold"]),
        "correct_route": (final.get("tool") != "rag") if oos else (final.get("tool") == "rag"),
        "eval_sufficient": ev.get("sufficient"), "eval_score": ev.get("score"),
        "latency": round(total, 1),
        "first_words": round(first_words, 1) if first_words else None,
        "step_seconds": _step_times(starts, total),
        "embedding_requests": embedding_cache.stats["misses"] - emb_before,
        "tavily_credits": web_search.stats["Tavily"] - web_before,
        "tokens": {k: dict(v) for k, v in counter.by_model.items()},
        "steps": [s["node"] for s in final.get("trace", [])],
        "error": error, "answer_preview": answer[:240],
    }


def _median_steps(rows: list[dict]) -> dict[str, float]:
    per: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        for node, secs in r["step_seconds"].items():
            per[node].append(secs)
    return {n: sorted(v)[len(v) // 2] for n, v in sorted(per.items())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="0")
    ap.add_argument("--gap", type=float, default=20, help="seconds between questions (free-tier rate limits)")
    args = ap.parse_args()

    configure_stage(args.stage)
    user = bench_user()
    doc_names = [d["filename"] for d in db.list_documents(user["id"], status="indexed")]
    questions = {q["id"]: q for q in load_questions()}
    rows = []
    for i, qid in enumerate(SUBSET):
        if i:
            time.sleep(args.gap)
        row = run_one(questions[qid], user, doc_names)
        rows.append(row)
        toks = sum(v["input"] + v["output"] for v in row["tokens"].values())
        print(f"  {qid:<16} tool={row['tool']!s:<11} route_ok={row['correct_route']!s:<5} "
              f"gold_in_answer={row['answer_has_gold']!s:<5} eval={row['eval_score']} "
              f"{row['latency']:>5}s tokens={toks}" + (f"  ERROR: {row['error'][:90]}" if row["error"] else ""))

    totals = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0})
    for r in rows:
        for model, v in r["tokens"].items():
            for k in v:
                totals[model][k] += v[k]
    ok = [r for r in rows if not r["error"]]
    in_scope = [r for r in ok if r["category"] != "out_of_scope"]
    summary = {
        "questions": len(rows), "errors": len(rows) - len(ok),
        "correct route": f"{sum(r['correct_route'] for r in ok)}/{len(ok)}",
        "answer contains gold phrase": f"{sum(r['answer_has_gold'] for r in in_scope)}/{sum(1 for r in in_scope if r['category'] != 'summary')}",
        "used expected document": f"{sum(r['used_expected_doc'] for r in in_scope)}/{len(in_scope)}",
        "evaluator: sufficient": f"{sum(bool(r['eval_sufficient']) for r in ok)}/{len(ok)}",
        "median latency (s)": sorted(r["latency"] for r in ok)[len(ok) // 2] if ok else None,
        "median first words (s)": (sorted(r["first_words"] for r in ok if r["first_words"])
                                   or [None])[len([r for r in ok if r["first_words"]]) // 2],
        "tokens per question by model": {m: round((v["input"] + v["output"]) / max(len(ok), 1))
                                         for m, v in totals.items()},
        "embedding requests (total)": sum(r["embedding_requests"] for r in rows),
        "Tavily credits (total)": sum(r["tavily_credits"] for r in rows),
        "median seconds per step": _median_steps(ok),
    }
    out = BENCH_DIR / "results" / f"e2e_stage{args.stage}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"stage": args.stage, "timestamp": datetime.now().isoformat(timespec="seconds"),
                               "summary": summary, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n=== End-to-end benchmark, stage {args.stage} ===")
    for k, v in summary.items():
        print(f"  {k:<30} {v}")
    print(f"  saved to {out.relative_to(BENCH_DIR.parent)}")


if __name__ == "__main__":
    main()
