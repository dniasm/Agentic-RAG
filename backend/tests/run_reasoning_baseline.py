
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.tools.direct_answer import answer
from backend.tests.test_reasoning_cases import TEST_CASES


output_path = Path(__file__).with_name("reasoning_baseline_results2.json")
results = []

for index, case in enumerate(TEST_CASES, start=1):
    print(f"\n{'=' * 70}", flush=True)
    print(f"[{index}/{len(TEST_CASES)}] {case['id']}", flush=True)
    print(f"Question: {case['query']}", flush=True)

    started = time.perf_counter()

    try:
        response, needs_web, gate = answer(
            query=case["query"],
            history=case["history"],
            confidence_required=False,
        )

        result = {
            "id": case["id"],
            "category": case["category"],
            "query": case["query"],
            "expected": case["expected"],
            "response": response,
            "needs_web": needs_web,
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "error": None,
        }

        print(f"\nResponse:\n{response}", flush=True)

    except Exception as exc:
        result = {
            "id": case["id"],
            "category": case["category"],
            "query": case["query"],
            "expected": case["expected"],
            "response": None,
            "needs_web": None,
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "error": repr(exc),
        }

        print(f"\nERROR: {exc!r}", flush=True)

    results.append(result)

    output_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Saved progress to {output_path}", flush=True)

print(f"\nFinished. Results saved to:\n{output_path}", flush=True)