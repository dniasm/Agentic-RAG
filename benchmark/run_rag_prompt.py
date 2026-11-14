"""Document-answer prompt check: old vs new instructions on the SAME passages.

The passages are the ones the app would pick (saved pools, Voyage scores, MMR, 6 passages; as run_checker.py), so
both prompts see identical evidence. Questions: 6 per answer shape (seed 5) plus every out-of-scope question.
Code checks per answer:

    citations   the model-free citation check (what decides whether the 120B answer check can be skipped)
    aspects     of the facts a full answer needs that ARE in the passages, how many the answer states
    gap         when some needed fact is NOT in the passages: does the answer say the documents don't cover it
    refused     the model replied with the sentinel (right for out-of-scope, wrong otherwise)
    tokens      input and output (output includes hidden reasoning)

Answers are saved for reading in results/rag_prompt_eval.json; the old prompt is saved in rag_prompt_old.json the
first time the script runs, so later runs compare against it.

    python benchmark/run_rag_prompt.py [--version old|new|both] [--only ids] [--repeat N] [--gap 10]
"""
import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_DIR.parent))
sys.path.insert(0, str(BENCH_DIR))

import numpy as np  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402

import run_selection as rs  # noqa: E402
from backend.config.llm import consume_backup_flag  # noqa: E402
from backend.config.usage import UsageTracker  # noqa: E402
from backend.evaluation import citations  # noqa: E402
from backend.tools import rag  # noqa: E402
from run_checker import passages_for  # noqa: E402

OUT = rs.RESULTS / "rag_prompt_eval.json"
OLD = rs.RESULTS / "rag_prompt_old.json"
SHAPES = ("fact", "definition", "summary", "multi_aspect", "comparison", "open_ended")

_GAP = re.compile(r"\b(?:don['’]t|do not|doesn['’]t|does not|aren['’]t|isn['’]t|no (?:data|information|details?|"
                  r"mention|figures?)|not (?:available|included|covered|mentioned|provided|specified|stated|described|"
                  r"given|addressed|discussed)|cannot be (?:determined|answered)|none of the)\b", re.I)


_JARGON = re.compile(r"\b(?:excerpts?|passages?)\b", re.I)
_LATEX = re.compile(r"\\[\[(]|\\(?:frac|mathrm|text|log|sum|times|cdot)\b|\$[^$\s][^$\n]*\\[^$\n]*\$")


def _norm(s: str) -> str:
    s = citations.CITATION.sub(" ", s)
    s = re.sub(r"[*_`#|]", " ", s).replace(" ", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", s).lower()


def pick(questions, per_shape=6, seed=5):
    rnd = random.Random(seed)
    out = []
    for shape in SHAPES:
        group = sorted([q for q in questions if q["shape"] == shape], key=lambda q: q["id"])
        out += rnd.sample(group, min(per_shape, len(group)))
    return out + sorted([q for q in questions if q["shape"] == "out_of_scope"], key=lambda q: q["id"])


def aspects(q) -> list[list[str]]:
    return q.get("aspects") or ([q["gold"]] if q.get("gold") else [])


def answer(q, docs, prompt: dict | None) -> dict:
    system, intro = rag.SYSTEM, rag.CONTEXT_INTRO
    if prompt is not None:
        rag.SYSTEM, rag.CONTEXT_INTRO = prompt["SYSTEM"], prompt["CONTEXT_INTRO"]
    tracker, backup = UsageTracker(), []

    def write(text):  # the backup flag is set inside the Runnable's context, so it must be read there
        out = rag.generate(text, docs, [])
        backup.append(consume_backup_flag())
        return out

    try:
        text = RunnableLambda(write).invoke(q["question"], {"callbacks": [tracker]})
    finally:
        rag.SYSTEM, rag.CONTEXT_INTRO = system, intro
    if backup[0]:
        sys.exit("The 120B was rate-limited and the backup model wrote this answer; stopping (not comparable).")
    total = tracker.summary()["total"]
    row = {"shape": q["shape"], "question": q["question"], "answer": text, "refused": text is None,
           "input_tokens": total["input"], "output_tokens": total["output"]}
    if text is None:
        return row
    evidence = _norm(" ".join(d["content"] for d in docs))
    said = _norm(text)
    available = [a for a in aspects(q) if any(_norm(alt).strip() in evidence for alt in a)]
    row["aspects_available"] = len(available)
    row["aspects_stated"] = sum(any(_norm(alt).strip() in said for alt in a) for a in available)
    row["aspects_missing_from_passages"] = len(aspects(q)) - len(available)
    row["gap_named"] = bool(_GAP.search(text))
    report = citations.check(text, [d["content"] for d in docs], question=q["question"])
    row["citations_ok"], row["citation_problems"] = report.ok, report.problems
    row["chars"] = len(text)
    row["says_excerpts"] = bool(_JARGON.search(text))
    row["latex"] = bool(_LATEX.search(text))
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="both", choices=("old", "new", "both"))
    ap.add_argument("--only", default="")
    ap.add_argument("--repeat", type=int, default=1, help="extra runs saved under <id>#2, #3 … (flaky cases)")
    ap.add_argument("--gap", type=float, default=10, help="seconds between answers (120B: 8k tokens/min per key)")
    args = ap.parse_args()
    if not OLD.exists():
        OLD.write_text(json.dumps({"SYSTEM": rag.SYSTEM, "CONTEXT_INTRO": rag.CONTEXT_INTRO}, indent=1,
                                  ensure_ascii=False), encoding="utf-8")
    old_prompt = json.loads(OLD.read_text(encoding="utf-8"))
    out = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"old": {}, "new": {}}

    rs.SCORE_FLOOR = rs.FLOORS["voyage"]
    data = json.loads(rs.POOL_JSON.read_text(encoding="utf-8"))["pools"]
    npz = rs._use_voyage_scores(data, np.load(rs.POOL_NPZ))
    questions = [q for q in rs.load_questions() if q["id"] in data]
    cases = pick(questions)
    if args.only:
        cases = [q for q in questions if q["id"] in args.only.split(",")]
    versions = {"old": old_prompt, "new": None}
    if args.version != "both":
        versions = {args.version: versions[args.version]}

    for q in cases:
        docs = [dict(d, page=d.get("page")) for d in passages_for(q, data, npz)]
        for version, prompt in versions.items():
            keys = [q["id"]] + [f"{q['id']}#{n}" for n in range(2, args.repeat + 1)]
            for key in keys:
                if key in out[version] and not (args.only and args.repeat == 1):
                    continue
                out[version][key] = answer(q, docs, prompt)
                OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
                r = out[version][key]
                print(f"  {version} {key:<26} " + ("REFUSED" if r["refused"] else
                      f"cites {'ok' if r['citations_ok'] else 'NO'}  aspects {r['aspects_stated']}/"
                      f"{r['aspects_available']}  gap {r['gap_named']}") +
                      f"  in {r['input_tokens']} out {r['output_tokens']}", flush=True)
                time.sleep(args.gap)
    report(out)


def report(out: dict):
    for version in ("old", "new"):
        rows = [r for k, r in out[version].items() if "#" not in k]
        if not rows:
            continue
        ans = [r for r in rows if r["shape"] != "out_of_scope"]
        oos = [r for r in rows if r["shape"] == "out_of_scope"]
        wrote = [r for r in ans if not r["refused"]]
        need_gap = [r for r in wrote if r["aspects_missing_from_passages"]]
        print(f"\n{version}: {len(rows)} questions")
        print(f"  answerable refused        {sum(r['refused'] for r in ans)}/{len(ans)}")
        print(f"  out of scope refused      {sum(r['refused'] for r in oos)}/{len(oos)}")
        print(f"  citations ok (skip 120B)  {sum(r['citations_ok'] for r in wrote)}/{len(wrote)}")
        print(f"  aspects stated            {sum(r['aspects_stated'] for r in wrote)}/"
              f"{sum(r['aspects_available'] for r in wrote)} (of those in the passages)")
        print(f"  gaps named                {sum(r['gap_named'] for r in need_gap)}/{len(need_gap)}")
        print(f"  says excerpts/passages    {sum(r.get('says_excerpts', False) for r in wrote)}/{len(wrote)}")
        print(f"  LaTeX                     {sum(r.get('latex', False) for r in wrote)}/{len(wrote)}")
        print(f"  tokens in / out           {sum(r['input_tokens'] for r in rows)} / "
              f"{sum(r['output_tokens'] for r in rows)}   answer chars {sum(r.get('chars', 0) for r in rows)}")
        for shape in SHAPES:
            s = [r for r in wrote if r["shape"] == shape]
            if s:
                print(f"    {shape:<13} cites ok {sum(r['citations_ok'] for r in s)}/{len(s)}  "
                      f"out {sum(r['output_tokens'] for r in s) // len(s)}/answer")


if __name__ == "__main__":
    main()
