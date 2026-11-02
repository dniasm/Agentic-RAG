"""Does a question meant for the model's own knowledge actually get answered by it?

Router benchmarks only check that a message is routed to model knowledge. This one also runs the knowledge
gate the direct answer passes through, because a refusal there sends the question on to the documents and
the web. For each case: router (small model, large-model review when unsure), then, when the router asks for a
fact check, the gate (large model). No answer is written.

Expected outcomes:
  answer  - opinions, philosophy, hypotheticals and well-established facts: answered from model knowledge
  refuse  - uncertain or invented facts, recent events, the user's own files: passed on to another source

    python benchmark/run_direct_gate.py [--out direct_gate_eval.json]
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_DIR.parent))

import groq  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402

from backend.config.usage import UsageTracker  # noqa: E402
from backend.graph import nodes  # noqa: E402
from backend.tools import direct_answer  # noqa: E402

EXTRA = [
    {"id": "ph-human", "message": "what do you think makes humans human?", "expect": "answer"},
    {"id": "ph-consciousness", "message": "Why do you think consciousness exists at all?", "expect": "answer"},
    {"id": "ph-good-life", "message": "What makes a life a good life?", "expect": "answer"},
    {"id": "ph-ai-feel", "message": "Can an AI ever truly understand anything, in your view?", "expect": "answer"},
    {"id": "fact-austen", "message": "Who wrote Pride and Prejudice?", "expect": "answer"},
    {"id": "fact-hooks", "message": "How do React hooks work?", "expect": "answer"},
    {"id": "ctl-invented", "message": "Summarize the 2019 paper 'Sparse Graph Tricks for Tiny Transformers'", "expect": "refuse"},
    {"id": "ctl-population", "message": "What is the exact population of Bhaktapur?", "expect": "refuse"},
    {"id": "ctl-recent", "message": "Who won the most recent Nepal general election?", "expect": "refuse"},
    {"id": "ctl-myfile", "message": "What does chapter 3 of my report conclude?", "expect": "refuse"},
]


def cases() -> list[dict]:
    router = json.loads((BENCH_DIR / "router_cases.json").read_text(encoding="utf-8"))
    opinion = [{"id": c["id"], "message": c["message"], "expect": "answer"}
               for c in router["cases"] if c["group"] == "opinion"]
    return opinion + EXTRA, router["doc_names"]


def run_case(case: dict, doc_names: list[str], gate_only: bool = False) -> dict:
    tracker = UsageTracker()
    state = {"query": case["message"], "history": [], "memories": [], "doc_names": doc_names}
    config = {"callbacks": [tracker]}
    for attempt in range(3):
        try:
            # Run through a Runnable so the tracker's callbacks reach the LLM calls inside.
            if gate_only:  # the worst case: as if the router had asked for a fact check on everything
                route = {"intent": "direct", "confidence_required": True, "standalone_query": case["message"]}
            else:
                route = RunnableLambda(nodes.router_node).invoke(state, config)
            gate = None
            if route["intent"] == "direct" and route["confidence_required"]:
                gate = RunnableLambda(lambda q: direct_answer._run_gate(q)).invoke(route["standalone_query"], config)
            break
        except groq.RateLimitError:
            if attempt == 2:
                raise
            print("    rate limited; waiting 40 s", flush=True)
            time.sleep(40)

    if route["intent"] == "greeting":
        outcome = "answer"  # small talk is written by the same model from its own knowledge
    elif route["intent"] != "direct":
        outcome = "refuse"
    elif not route["confidence_required"]:
        outcome = "answer"
    else:
        outcome = "answer" if gate is not None and direct_answer._gate_passes(gate) else "refuse"
    s = tracker.summary()["total"]
    return {
        **case, "outcome": outcome, "ok": outcome == case["expect"], "intent": route["intent"],
        "confidence_required": route["confidence_required"],
        "gate": gate.model_dump() if gate is not None else None,
        "tokens": s["input"] + s["output"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="direct_gate_eval.json")
    ap.add_argument("--gate-only", action="store_true",
                    help="skip the router and send every case to the knowledge gate (the router's worst case)")
    args = ap.parse_args()
    all_cases, doc_names = cases()
    rows = []
    for case in all_cases:
        r = run_case(case, doc_names, gate_only=args.gate_only)
        rows.append(r)
        g = r["gate"]
        gate_txt = (f"gate {g.get('question_kind', '-')}/{g['confidence']}" if g else "no gate")
        print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['id']:<18} expect {r['expect']:<6} got {r['outcome']:<6} "
              f"(route {r['intent']}, check {'yes' if r['confidence_required'] else 'no'}, {gate_txt})", flush=True)
        time.sleep(2)
    answer_ok = sum(r["ok"] for r in rows if r["expect"] == "answer")
    refuse_ok = sum(r["ok"] for r in rows if r["expect"] == "refuse")
    summary = {
        "answered_as_expected": f"{answer_ok}/{sum(r['expect'] == 'answer' for r in rows)}",
        "refused_as_expected": f"{refuse_ok}/{sum(r['expect'] == 'refuse' for r in rows)}",
        "gate_calls": sum(r["gate"] is not None for r in rows),
        "tokens": sum(r["tokens"] for r in rows),
    }
    print("\n" + "  ".join(f"{k}: {v}" for k, v in summary.items()))
    out = BENCH_DIR / "results" / args.out
    out.write_text(json.dumps({"timestamp": datetime.now().isoformat(timespec="seconds"), "summary": summary,
                               "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"saved to {out.relative_to(BENCH_DIR.parent)}")


if __name__ == "__main__":
    main()
