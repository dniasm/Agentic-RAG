"""Answer-checker benchmark: do the code citation checks agree with the large model's answer check?

For a sample of answerable benchmark questions (stratified by shape):
    1. pick the passages the app would pick (saved pools, Voyage scores, MMR, 6 passages)
    2. the large model writes the answer (backend.tools.rag.generate, same prompt as the app)
    3. the large model checks it (backend.evaluation.evaluator.evaluate_answer, cited passages only, as the app)
    4. the code checks it (backend.evaluation.citations.check)
Then each answer is broken on purpose in five ways and checked again by code (the right verdict is known):
    wrong_citation    every [n] moved to a passage the answer didn't use
    invented_claim    a made-up sentence with a citation appended
    changed_number    a number in a cited sentence changed
    uncited_claim     a made-up sentence without a citation appended
    invalid_citation  a citation pointing at a passage that doesn't exist

Uses the large model (~3,500 tokens per question), paced for Groq's per-minute limit; answers and verdicts are
cached in results/checker_eval.json, so a re-run only scores (and --rescore re-runs the code check only).

    python benchmark/run_checker.py [--per-shape 4] [--gap 20] [--rescore] [--fresh]
"""
import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

import run_selection as rs  # noqa: E402
from backend.evaluation import citations  # noqa: E402

OUT = rs.RESULTS / "checker_eval.json"
SHAPES = ("fact", "definition", "summary", "multi_aspect", "comparison", "open_ended")
INVENTED = [
    "The programme was funded by a grant from the Asian Development Bank in 1997",
    "Its original design was patented by a Swiss engineering firm in Basel",
    "A follow-up review by the Finnish Ministry of Agriculture confirmed the results",
    "The approach was later adopted by the European Space Agency for satellite telemetry",
    "Early versions were written in COBOL and ran on IBM mainframes",
]


def sample(questions, per_shape, seed=7, exclude=()):
    rnd = random.Random(seed)
    out = []
    for shape in SHAPES:
        group = sorted([q for q in questions if q["shape"] == shape and q["id"] not in exclude], key=lambda q: q["id"])
        out += rnd.sample(group, min(per_shape, len(group)))
    return out


def passages_for(q, data, npz):
    pool, vecs = data[q["id"]], npz[q["id"].replace("-", "_")]
    chosen = rs.run_method("B", {"k": 6, "lam": 0.7, "top_n": 12}, pool, vecs)
    return [pool[i] for p in chosen for i in p["members"]]


def write_and_judge(q, docs):
    from backend.evaluation.evaluator import evaluate_answer
    from backend.graph.nodes import _cited
    from backend.tools import rag
    answer = rag.generate(q["question"], [dict(d, page=d.get("page")) for d in docs], [])
    if answer is None:
        return None, None
    context = "\n\n".join(rag.format_block(i, d) for i, d in _cited(docs, answer))
    return answer, evaluate_answer(q["question"], answer, context)


def _retry(fn, *args):
    for attempt in range(5):
        try:
            return fn(*args)
        except Exception as e:  # rate limit: wait for the minute window to pass
            if "rate" not in str(e).lower() and "429" not in str(e):
                raise
            print(f"   rate-limited, waiting 60s ({str(e)[:80]})")
            time.sleep(60)
    raise RuntimeError("still rate-limited")


# ---------------------------------------------------------------- corruptions
def corruptions(answer: str, n_docs: int, k: int) -> dict[str, str]:
    out = {}
    used = sorted(set(citations._cited(answer)))
    unused = [n for n in range(1, n_docs + 1) if n not in used]
    if used and unused:
        mapping = {u: unused[i % len(unused)] for i, u in enumerate(used)}
        out["wrong_citation"] = citations.CITATION.sub(
            lambda m: "[" + ", ".join(str(mapping.get(int(x), x)) for x in m.group(1).split(",")) + "]", answer)
    first = used[0] if used else 1
    out["invented_claim"] = answer.rstrip() + f"\n\n{INVENTED[k % len(INVENTED)]} [{first}]."
    out["uncited_claim"] = answer.rstrip() + f"\n\n{INVENTED[(k + 2) % len(INVENTED)]}."
    m = re.search(r"(?<![\[\d])(\d{2,})(?![\d\]])", citations.CITATION.sub(lambda c: "#" * len(c.group(0)), answer))
    if m:
        n = m.group(1)
        changed = str(int(n) + 7) if len(n) < 4 or not n.startswith(("19", "20")) else str(int(n) + 3)
        out["changed_number"] = answer[:m.start(1)] + changed + answer[m.end(1):]
    if used:
        out["invalid_citation"] = answer.replace(f"[{used[0]}]", f"[{n_docs + 3}]", 1) if f"[{used[0]}]" in answer \
            else answer.rstrip() + f" [{n_docs + 3}]"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-shape", type=int, default=4)
    ap.add_argument("--gap", type=float, default=20, help="seconds between questions (Groq's per-minute limit)")
    ap.add_argument("--rescore", action="store_true", help="don't call the model; re-run the code checks only")
    ap.add_argument("--fresh", action="store_true",
                    help="validation run: skip the questions in checker_eval.json (the rules were tuned on them), "
                         "use another seed and save to checker_eval_fresh.json")
    args = ap.parse_args()
    out_path, seed, exclude = OUT, 7, set()
    if args.fresh:
        exclude = set(json.loads(OUT.read_text(encoding="utf-8"))["answers"])
        out_path, seed = rs.RESULTS / "checker_eval_fresh.json", 11

    rs.SCORE_FLOOR = rs.FLOORS["voyage"]
    data = json.loads(rs.POOL_JSON.read_text(encoding="utf-8"))["pools"]
    npz = rs._use_voyage_scores(data, np.load(rs.POOL_NPZ))
    questions = [q for q in rs.load_questions() if q["id"] in data]
    cache = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {"answers": {}}

    for q in sample(questions, args.per_shape, seed, exclude):
        if q["id"] in cache["answers"] or args.rescore:
            continue
        docs = passages_for(q, data, npz)
        answer, verdict = _retry(write_and_judge, q, docs)
        cache["answers"][q["id"]] = {"shape": q["shape"], "question": q["question"], "answer": answer,
                                     "verdict": verdict, "evidence": [d["content"] for d in docs]}
        out_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  {q['id']:<24} {'(no answer)' if answer is None else ('model: ok' if verdict['sufficient'] and verdict['grounded'] else 'model: FLAGGED')}")
        time.sleep(args.gap)

    report(cache)
    out_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")


def report(cache):
    rows = {qid: a for qid, a in cache["answers"].items() if a["answer"]}
    agree = {"both_ok": 0, "code_only_flags": 0, "model_only_flags": 0, "both_flag": 0}
    details, catch = [], {}
    for k, (qid, a) in enumerate(sorted(rows.items())):
        r = citations.check(a["answer"], a["evidence"], question=a["question"])
        model_ok = bool(a["verdict"]["sufficient"] and a["verdict"]["grounded"])
        key = ("both_ok" if r.ok else "code_only_flags") if model_ok else ("model_only_flags" if r.ok else "both_flag")
        agree[key] += 1
        a["code"] = {"ok": r.ok, "support": r.support, "problems": r.problems,
                     "unsupported": [(s.text[:160], s.support, s.missing_numbers) for s in r.unsupported],
                     "uncited": [s[:160] for s in r.uncited]}
        if key != "both_ok":
            details.append((qid, key, a["verdict"].get("missing", "")[:140], r.problems,
                            [(s.text[:110], s.support, s.missing_numbers) for s in r.unsupported][:2], [u[:110] for u in r.uncited][:2]))
        for kind, broken in corruptions(a["answer"], len(a["evidence"]), k).items():
            caught = not citations.check(broken, a["evidence"], question=a["question"]).ok
            catch.setdefault(kind, [0, 0])
            catch[kind][0] += caught
            catch[kind][1] += 1
    n = len(rows)
    cache["summary"] = {"answers": n, "agreement": agree,
                        "catch_rate": {k: f"{c}/{t}" for k, (c, t) in catch.items()}}
    print(f"\n{n} answers")
    print(f"  both say OK              {agree['both_ok']}")
    print(f"  both flag                {agree['both_flag']}")
    print(f"  only the code flags      {agree['code_only_flags']}   (code would send these to the model check)")
    print(f"  only the model flags     {agree['model_only_flags']}   (code would MISS these)")
    print("\nBroken on purpose, caught by the code:")
    for kind, (c, t) in catch.items():
        print(f"  {kind:<18} {c}/{t}")
    print("\nDisagreements:")
    for d in details:
        print("  ", d)


if __name__ == "__main__":
    main()
