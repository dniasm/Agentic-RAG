"""Router benchmark: does the intake router send differently worded messages to the right source?

Runs every case in router_cases.json through the real router node (small model + large-model review when
unsure), with the case's documents "uploaded", and records the chosen source, tokens and time.
No retrieval or answers are generated, so it needs no Qdrant and can run with the backend running.

    python benchmark/run_router.py [--gap 9] [--only st-hey,doc-gdpr-sum] [--out router_eval.json]
"""
import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import TypedDict

BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_DIR.parent))

import groq  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402

from backend.config.usage import UsageTracker  # noqa: E402
from backend.graph import nodes  # noqa: E402

SOURCES = ["greeting", "direct", "rag", "web_search"]


class _S(TypedDict, total=False):
    query: str
    history: list
    memories: list
    doc_names: list
    out: dict


def _graph():
    def run(state):
        return {"out": nodes.router_node(state)}
    g = StateGraph(_S)
    g.add_node("router", run)
    g.add_edge(START, "router")
    g.add_edge("router", END)
    return g.compile()


def run_case(graph, case: dict, doc_names: list[str]) -> dict:
    tracker = UsageTracker()
    state = {"query": case["message"], "history": case.get("history", []), "memories": [], "doc_names": doc_names}
    started = time.perf_counter()
    for attempt in range(2):
        try:
            out = graph.invoke(state, {"callbacks": [tracker]})["out"]
            break
        except groq.RateLimitError:
            if attempt:
                raise
            print("    rate limited; waiting 40 s", flush=True)
            time.sleep(40)
    steps = out["trace"]
    reviews = [s for s in steps if s["node"] == "review" and not s["data"].get("skipped")]
    usage = tracker.summary()
    chosen = out["intent"]
    ok = chosen == case["expect"]
    return {
        "id": case["id"], "group": case["group"], "message": case["message"], "expect": case["expect"],
        "also_ok": case.get("also_ok", []),
        "chosen": chosen, "source_preference": out["source_preference"], "query_is_clear": out["query_is_clear"],
        "standalone_query": out["standalone_query"], "reason": out["route_reason"],
        "correct": ok, "acceptable": ok or chosen in case.get("also_ok", []),
        "reviewed": bool(reviews), "revised": any(not s["data"].get("approved", True) for s in reviews),
        "tokens": usage["total"]["input"] + usage["total"]["output"], "by_model": usage["by_model"],
        "seconds": round(time.perf_counter() - started, 2),
    }


def summarise(rows: list[dict]) -> dict:
    n = len(rows)
    confusion = {e: Counter() for e in SOURCES}
    groups = defaultdict(lambda: {"n": 0, "correct": 0, "acceptable": 0})
    for r in rows:
        confusion[r["expect"]][r["chosen"]] += 1
        g = groups[r["group"]]
        g["n"] += 1
        g["correct"] += r["correct"]
        g["acceptable"] += r["acceptable"]
    # Same-topic general questions ("what is a support vector machine?") may use the documents.
    non_doc = [r for r in rows if r["expect"] in ("greeting", "direct") and "rag" not in r.get("also_ok", [])]
    return {
        "cases": n,
        "correct": sum(r["correct"] for r in rows),
        "acceptable": sum(r["acceptable"] for r in rows),
        # The failure you saw in testing: casual / opinion / general messages sent to the documents.
        "non_document_sent_to_rag": sum(r["chosen"] == "rag" for r in non_doc),
        "non_document_cases": len(non_doc),
        "reviewed": sum(r["reviewed"] for r in rows),
        "revised_by_review": sum(r["revised"] for r in rows),
        "avg_tokens": round(sum(r["tokens"] for r in rows) / max(1, n)),
        "avg_seconds": round(sum(r["seconds"] for r in rows) / max(1, n), 2),
        "by_group": dict(groups),
        "confusion": {e: dict(c) for e, c in confusion.items()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=float, default=9.0, help="seconds between cases (small model: 8k tokens/min)")
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ap.add_argument("--out", default="router_eval.json")
    args = ap.parse_args()

    data = json.loads((BENCH_DIR / "router_cases.json").read_text(encoding="utf-8"))
    cases = data["cases"]
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    graph = _graph()
    rows = []
    for i, case in enumerate(cases, 1):
        if i > 1:
            time.sleep(args.gap)
        row = run_case(graph, case, data["doc_names"])
        rows.append(row)
        mark = "ok " if row["correct"] else ("~  " if row["acceptable"] else "XX ")
        print(f"[{i:2}/{len(cases)}] {mark} {row['expect']:<10} -> {row['chosen']:<10} "
              f"{row['tokens']:>5} tok{' (reviewed)' if row['reviewed'] else ''} :: {row['message'][:60]}", flush=True)

    summary = summarise(rows)
    out = BENCH_DIR / "results" / args.out
    out.write_text(json.dumps({"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "summary": summary, "rows": rows},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "by_group"}, indent=1))
    print("saved", out)


if __name__ == "__main__":
    main()
