"""Web-answer prompt check: old vs new instructions on the SAME search results.

Each question is searched once (results saved to results/web_prompt_results.json, so both prompts see identical
evidence and reruns cost no search credits), then answered by the large model with each prompt. Code checks:

    citations   the model-free citation check (every claim cited, citations exist, cited text matches)
    as_of       for "current" questions: the answer says when its figure or status applies (a date, "as of …")
    gap         for questions the results can only partly answer: the answer says what they don't cover

The answers are saved for reading; code checks can't judge everything.

    python benchmark/run_web_prompt.py [--only ids] [--gap 20]
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_DIR.parent))

from langchain_core.runnables import RunnableLambda  # noqa: E402

from backend.config.llm import consume_backup_flag  # noqa: E402
from backend.config.usage import UsageTracker  # noqa: E402
from backend.evaluation import citations  # noqa: E402
from backend.tools import web_search  # noqa: E402

RESULTS = BENCH_DIR / "results" / "web_prompt_results.json"
OUT = BENCH_DIR / "results" / "web_prompt_eval.json"
OLD = BENCH_DIR / "results" / "web_prompt_old.json"

CASES = [
    {"id": "cur-python", "kind": "current", "q": "What is the latest stable version of Python?"},
    {"id": "cur-react", "kind": "current", "q": "What's the latest version of React?"},
    {"id": "cur-pm", "kind": "current", "q": "Who is the current Prime Minister of Nepal?"},
    {"id": "cur-gold", "kind": "current", "q": "What is the current price of gold per tola in Nepal?"},
    {"id": "cur-repo", "kind": "current", "q": "What is Nepal Rastra Bank's current policy rate?"},
    {"id": "cur-ballon", "kind": "current", "q": "Who won the most recent Ballon d'Or?"},
    {"id": "part-eiffel", "kind": "partial",
     "q": "When was the Eiffel Tower completed, and how many people visited it last Tuesday?"},
    {"id": "part-iphone", "kind": "partial",
     "q": "What is the latest iPhone model, and how many units were sold in Nepal last month?"},
    {"id": "fact-everest", "kind": "fact", "q": "How tall is Mount Everest?"},
    {"id": "fact-webb", "kind": "fact", "q": "What has the James Webb Space Telescope discovered recently?"},
]

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
_AS_OF = re.compile(rf"\bas of\b|\b{_MONTH}\s+(?:\d{{1,2}},?\s+)?\d{{4}}\b|\b\d{{1,2}}\s+{_MONTH}\s+\d{{4}}\b|"
                    r"\b\d{4}-\d{2}-\d{2}\b|\b(?:released|announced|published|updated|appointed|sworn in|took office)"
                    r"\b[^.]{0,40}\b20\d{2}\b", re.I)
_GAP = re.compile(r"\b(?:don['’]t|do not|doesn['’]t|does not|didn['’]t|aren['’]t|isn['’]t|no (?:data|information|"
                  r"figures?|numbers?|details?)|not (?:available|included|covered|mentioned|provided|reported)|"
                  r"couldn['’]t find|can['’]t find|cannot be|can['’]t be|no (?:source|result)s?\b|"
                  r"none of the (?:results|sources))\b", re.I)


def results_for(case: dict, cache: dict) -> list[dict]:
    if case["id"] not in cache:
        cache[case["id"]] = web_search.search(case["q"])
        RESULTS.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    return cache[case["id"]]


def answer(case: dict, results: list[dict], prompt: dict | None) -> dict:
    system, intro = web_search.SYSTEM, web_search.RESULTS_INTRO
    if prompt:
        web_search.SYSTEM, web_search.RESULTS_INTRO = prompt["SYSTEM"], prompt["RESULTS_INTRO"]
    tracker = UsageTracker()
    backup = []

    def write(q):  # the backup flag is set inside the Runnable's context, so it must be read there
        text = web_search.generate(q, results, [])
        backup.append(consume_backup_flag())
        return text

    try:
        text = RunnableLambda(write).invoke(case["q"], {"callbacks": [tracker]})
    finally:
        web_search.SYSTEM, web_search.RESULTS_INTRO = system, intro
    if backup[0]:
        sys.exit("The 120B was rate-limited and the backup model wrote this answer; stopping (results not comparable).")
    report = citations.check(text, [r.get("content", "") for r in results], question=case["q"])
    total = tracker.summary()["total"]
    return {
        "answer": text,
        "citations_ok": report.ok, "citation_problems": report.problems,
        "as_of": bool(_AS_OF.search(text)) if case["kind"] == "current" else None,
        "gap_named": bool(_GAP.search(text)) if case["kind"] == "partial" else None,
        "input_tokens": total["input"], "output_tokens": total["output"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--gap", type=float, default=20, help="seconds between answers (large model: 8k tokens/min)")
    args = ap.parse_args()
    cache = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}
    out = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"old": {}, "new": {}}
    old_prompt = json.loads(OLD.read_text(encoding="utf-8"))
    cases = [c for c in CASES if not args.only or c["id"] in args.only.split(",")]

    for case in cases:
        results = results_for(case, cache)
        for version, prompt in (("old", old_prompt), ("new", None)):
            if case["id"] in out[version] and not args.only:
                continue
            row = {"q": case["q"], "kind": case["kind"], "results": len(results), **answer(case, results, prompt)}
            out[version][case["id"]] = row
            OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"  {version} {case['id']:<12} cites {'ok' if row['citations_ok'] else 'NO'}  "
                  f"as_of {row['as_of']}  gap {row['gap_named']}  in {row['input_tokens']} out {row['output_tokens']}",
                  flush=True)
            time.sleep(args.gap)
    report(out)


def report(out: dict):
    for version in ("old", "new"):
        rows = out[version].values()
        cur = [r for r in rows if r["kind"] == "current"]
        part = [r for r in rows if r["kind"] == "partial"]
        print(f"{version}: citations ok {sum(r['citations_ok'] for r in rows)}/{len(rows)}, "
              f"as-of dates {sum(r['as_of'] for r in cur)}/{len(cur)}, gaps named {sum(r['gap_named'] for r in part)}/"
              f"{len(part)}, input {sum(r['input_tokens'] for r in rows)}, output {sum(r['output_tokens'] for r in rows)}")


if __name__ == "__main__":
    main()
