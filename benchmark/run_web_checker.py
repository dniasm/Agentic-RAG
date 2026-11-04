"""Web-answer checker benchmark: can the code citation check stand in for the large model's check on web answers?

Same method as run_checker.py (document answers), on web answers:
    1. the large model's answer check (backend.evaluation.evaluator.evaluate_answer, given the cited results only,
       each cut to 1,200 characters, as the app does)
    2. the code check, with the app's rule for web answers (backend.graph.nodes._code_check): it passes only if
       the citations hold up AND the answer doesn't say the results miss part of the question
Code passing an answer the model flags is a miss (the model check would have been skipped). Each answer is also
broken on purpose in five ways and checked again by code.

Answer sets:
    tuning  the 20 answers from run_web_prompt.py (old and new prompt, 10 questions); the checker was fixed on these
    fresh   new questions, searched and answered here (--fresh), for validation

    python benchmark/run_web_checker.py [--fresh] [--gap 20] [--rescore]
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_DIR.parent))
sys.path.insert(0, str(BENCH_DIR))

from langchain_core.runnables import RunnableLambda  # noqa: E402

from backend.config.llm import consume_backup_flag  # noqa: E402
from backend.evaluation import citations  # noqa: E402
from backend.config.llm import structured  # noqa: E402
from backend.evaluation.evaluator import EVAL_PROMPT, AnswerEval  # noqa: E402
from backend.graph import nodes  # noqa: E402
from backend.tools import web_search  # noqa: E402
from run_checker import corruptions  # noqa: E402

RESULTS = BENCH_DIR / "results"
OUT = RESULTS / "web_checker_eval.json"
FRESH_OUT = RESULTS / "web_checker_eval_fresh.json"
FRESH = [
    "What is the latest LTS version of Node.js?",
    "Who is the current Secretary-General of the United Nations?",
    "What is the US dollar to Nepali rupee exchange rate today?",
    "Who won the 2026 FIFA World Cup?",
    "What is the population of Kathmandu?",
    "When is the next total solar eclipse visible from Europe?",
    "What's new in the latest Android version?",
    "What did the most recent IPCC report conclude?",
    "How much does a Tesla Model 3 cost in the US?",
    "What is Nepal's current inflation rate?",
    "Who is the CEO of OpenAI?",
    "How long is the Great Wall of China?",
]


def _large_model(fn, *args):
    """Run one large-model call; stop if the backup model answered instead (results would not be comparable)."""
    flag = []

    def call(_):
        out = fn(*args)
        flag.append(consume_backup_flag())  # the flag lives in the Runnable's context
        return out

    for attempt in range(60):  # the daily limit frees up slowly: wait as long as Groq says
        try:
            out = RunnableLambda(call).invoke(None)
            break
        except Exception as e:
            if "rate" not in str(e).lower() and "429" not in str(e):
                raise
            m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", str(e))
            wait = 60 if not m else int(m[1] or 0) * 3600 + int(m[2] or 0) * 60 + float(m[3] or 0) + 5
            print(f"   rate-limited, waiting {wait:.0f}s", flush=True)
            time.sleep(wait)
    else:
        raise RuntimeError("still rate-limited")
    if flag and flag[-1]:
        sys.exit("The 120B was rate-limited and the backup model answered; stopping (results not comparable).")
    return out


def verdict(question: str, answer: str, results: list[dict]) -> dict:
    """The app's answer check (evaluator.evaluate_answer), without the backup model: a rate limit must stop the
    run, not let the small model grade."""
    context = "\n\n".join(f"[{i}] {r.get('content', '')[:1200]}" for i, r in nodes._cited(results, answer))
    prompt = EVAL_PROMPT.format(query=question, answer=answer,
                                context_block=f"Context the answer must be based on:\n{context[:6000]}\n\n")
    return _large_model(lambda: structured(AnswerEval, fast=False).invoke(prompt).model_dump())


def tuning_answers() -> dict:
    out = json.loads((RESULTS / "web_prompt_eval.json").read_text(encoding="utf-8"))
    results = json.loads((RESULTS / "web_prompt_results.json").read_text(encoding="utf-8"))
    return {f"{v}-{cid}": {"question": row["q"], "answer": row["answer"], "results": results[cid]}
            for v in ("old", "new") for cid, row in out[v].items()}


def code_verdict(a: dict) -> tuple[bool, citations.CitationReport]:
    """The app's rule for web answers (nodes._code_check): citations hold up and no gap is admitted."""
    r = citations.check(a["answer"], [x.get("content", "") for x in a["results"]], question=a["question"])
    return r.ok and not r.gaps, r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="validation: new questions, searched and answered here")
    ap.add_argument("--gap", type=float, default=20, help="seconds between model calls (8k tokens/min)")
    ap.add_argument("--rescore", action="store_true", help="no model calls; re-run the code check only")
    args = ap.parse_args()
    path = FRESH_OUT if args.fresh else OUT
    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"answers": {}}

    if args.fresh:
        # Answers are written by the 120B only: on a rate limit, wait instead of letting the backup model write.
        from backend.config.llm import get_llm
        web_search.invoke_answer = lambda messages, temperature=None: get_llm(temperature).invoke(messages).content
        for k, q in enumerate(FRESH):
            qid = f"fresh-{k + 1:02d}"
            a = cache["answers"].get(qid)
            if args.rescore or (a and "verdict" in a):
                continue
            if not a:
                results = web_search.search(q)
                answer = _large_model(web_search.generate, q, results, [])
                a = cache["answers"][qid] = {"question": q, "answer": answer, "results": results}
                path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
                time.sleep(args.gap)
            a["verdict"] = verdict(q, a["answer"], a["results"])
            path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"  {qid}  model: {'ok' if a['verdict']['sufficient'] and a['verdict']['grounded'] else 'FLAGGED'}",
                  flush=True)
            time.sleep(args.gap)
    else:
        for qid, a in tuning_answers().items():
            if qid in cache["answers"] or args.rescore:
                continue
            a["verdict"] = verdict(a["question"], a["answer"], a["results"])
            cache["answers"][qid] = a
            path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"  {qid:<18} model: {'ok' if a['verdict']['sufficient'] and a['verdict']['grounded'] else 'FLAGGED'}",
                  flush=True)
            time.sleep(args.gap)

    report(cache)
    path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")


def report(cache: dict):
    rows = {qid: a for qid, a in cache["answers"].items() if a.get("verdict")}
    agree = {"both_ok": 0, "both_flag": 0, "code_only_flags": 0, "model_only_flags": 0}
    catch, details = {}, []
    for k, (qid, a) in enumerate(sorted(rows.items())):
        passed, r = code_verdict(a)
        model_ok = bool(a["verdict"]["sufficient"] and a["verdict"]["grounded"])
        key = ("both_ok" if passed else "code_only_flags") if model_ok else ("model_only_flags" if passed else "both_flag")
        agree[key] += 1
        a["code"] = {"pass": passed, "problems": r.problems, "gaps": [g[:160] for g in r.gaps],
                     "unsupported": [(s.text[:160], s.support, s.missing_numbers) for s in r.unsupported],
                     "uncited": [s[:160] for s in r.uncited]}
        if key != "both_ok":
            details.append((qid, key, a["verdict"].get("missing", "")[:160], r.problems, len(r.gaps)))
        evidence = [x.get("content", "") for x in a["results"]]
        for kind, broken in corruptions(a["answer"], len(evidence), k).items():
            caught = not citations.check(broken, evidence, question=a["question"]).ok
            catch.setdefault(kind, [0, 0])
            catch[kind][0] += caught
            catch[kind][1] += 1
    cache["summary"] = {"answers": len(rows), "agreement": agree,
                        "model_check_skipped": f"{agree['both_ok'] + agree['model_only_flags']}/{len(rows)}",
                        "catch_rate": {k: f"{c}/{t}" for k, (c, t) in catch.items()}}
    print(f"\n{len(rows)} answers")
    print(f"  both say OK              {agree['both_ok']}   (model check skipped)")
    print(f"  both flag                {agree['both_flag']}")
    print(f"  only the code flags      {agree['code_only_flags']}   (sent to the model check)")
    print(f"  only the model flags     {agree['model_only_flags']}   (code would MISS these)")
    print("\nBroken on purpose, caught by the code:")
    for kind, (c, t) in catch.items():
        print(f"  {kind:<18} {c}/{t}")
    print("\nDisagreements:")
    for d in details:
        print("  ", d)


if __name__ == "__main__":
    main()
