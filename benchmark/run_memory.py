"""Memory benchmark: does the memory model + merge code save, update and forget facts correctly?

Each case in memory_cases.json is a fresh user on a TEMPORARY database (your real memories are untouched).
Calls the real memory model (Qwen), paced to stay under its tokens-per-minute limit.

    python benchmark/run_memory.py            # all cases
    python benchmark/run_memory.py relocation # one case
"""
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config.settings import settings  # noqa: E402

settings.database_path = os.path.join(tempfile.mkdtemp(), "memory_bench.db")  # before any database use

from backend.config.usage import UsageTracker  # noqa: E402
from backend.models import database as db  # noqa: E402
from backend.tools.memory import extract_and_save_memories  # noqa: E402

BENCH_DIR = Path(__file__).resolve().parent
TOKENS_PER_MINUTE = 6000  # Groq free tier allows 8,000 per model; leave headroom


def _found(term: str, values: list[str]) -> bool:
    pattern = re.compile(rf"\b{re.escape(term.casefold())}\b")
    return any(pattern.search(v.casefold()) for v in values)


def run_case(case: dict, pace: dict) -> dict:
    user = db.create_user(f"mem_{case['id']}_{time.time_ns()}", "x")
    history: list[dict] = []
    tracker = UsageTracker()
    for turn in case["turns"]:
        before = tracker.summary()["total"]
        extract_and_save_memories(user["id"], turn, history, callbacks=[tracker])
        after = tracker.summary()["total"]
        used = (after["input"] + after["output"]) - (before["input"] + before["output"])
        # Pace: spend at most TOKENS_PER_MINUTE per rolling minute.
        pace["spent"].append((time.monotonic(), used))
        window = [(t, n) for t, n in pace["spent"] if time.monotonic() - t < 60]
        pace["spent"] = window
        if sum(n for _, n in window) > TOKENS_PER_MINUTE:
            time.sleep(max(0.0, 60 - (time.monotonic() - window[0][0])))
        history += [{"role": "user", "content": turn}, {"role": "assistant", "content": "Got it."}]

    memories = {m["memory_key"]: m["values"] for m in db.list_memories(user["id"])}
    values = [v for vs in memories.values() for v in vs]
    missing = [t for t in case.get("present", []) if not _found(t, values)]
    unwanted = [t for t in case.get("absent", []) if _found(t, values)]
    false_save = bool(case.get("empty")) and bool(memories)
    usage = tracker.summary()["total"]
    return {"id": case["id"], "passed": not missing and not unwanted and not false_save, "memories": memories,
            "missing": missing, "unwanted": unwanted, "false_save": false_save,
            "tokens": usage["input"] + usage["output"]}


def main():
    all_cases = json.loads((BENCH_DIR / "memory_cases.json").read_text(encoding="utf-8"))["cases"]
    cases = [c for c in all_cases if c["id"] in sys.argv[1:]] if len(sys.argv) > 1 else all_cases
    db.init_db()
    pace = {"spent": []}
    rows = []
    for case in cases:
        r = run_case(case, pace)
        rows.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        extra = "".join([f"  missing={r['missing']}" if r["missing"] else "",
                         f"  should be gone={r['unwanted']}" if r["unwanted"] else "",
                         "  saved something it shouldn't" if r["false_save"] else ""])
        print(f"{mark}  {r['id']:<24} {json.dumps(r['memories'], ensure_ascii=False)}{extra}", flush=True)

    passed = sum(r["passed"] for r in rows)
    present_total = sum(len(c.get("present", [])) for c in cases)
    present_found = present_total - sum(len(r["missing"]) for r in rows)
    summary = {
        "cases_passed": f"{passed}/{len(rows)}",
        "facts_saved": f"{present_found}/{present_total}",
        "stale_values_left": sum(len(r["unwanted"]) for r in rows),
        "false_saves": sum(r["false_save"] for r in rows),
        "memory_model_tokens": sum(r["tokens"] for r in rows),
    }
    print("\n" + "  ".join(f"{k}: {v}" for k, v in summary.items()))
    if len(cases) != len(all_cases):  # a single-case run must not replace the full results
        print("single case: results not saved")
        return
    out = BENCH_DIR / "results" / "memory_eval.json"
    out.write_text(json.dumps({"timestamp": datetime.now().isoformat(timespec="seconds"), "model":
                               settings.memory_llm_model, "summary": summary, "rows": rows},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"saved to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
