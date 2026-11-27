"""Fail if a backend/tests/**/test_*.py file is covered by no CI workflow, or by more than one.

Collects the `tests:` values (space-separated paths/globs) from .github/workflows/*.yml, expands them against
backend/tests/**/test_*.py and compares. Parsed with a regex (PyYAML is not in requirements*.txt).
Usage (repo root): python scripts/ci_test_coverage.py
"""

import glob
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_ROOT = ROOT / "backend" / "tests"
TESTS_LINE = re.compile(r"^\s*(?:-\s*)?tests:\s*(\S.*?)\s*$")


def main() -> int:
    covered: dict[str, list[str]] = defaultdict(list)
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for line in wf.read_text(encoding="utf-8").splitlines():
            m = TESTS_LINE.match(line)
            if not m or m.group(1).startswith(("${{", "#", "|", ">")):
                continue
            for pattern in m.group(1).strip("\"'").split():
                hits = glob.glob(str(ROOT / pattern))
                if not hits:
                    print(f"WARN {wf.name}: '{pattern}' matches no file")
                for p in hits:
                    covered[Path(p).relative_to(ROOT).as_posix()].append(wf.name)
    all_tests = sorted(p.relative_to(ROOT).as_posix() for p in TESTS_ROOT.rglob("test_*.py"))
    missing = [t for t in all_tests if t not in covered]
    doubled = {t: sorted(w) for t, w in covered.items() if len(w) > 1}
    for t in missing:
        print(f"NOT COVERED: {t}")
    for t, w in doubled.items():
        print(f"DOUBLE COUNTED: {t} in {', '.join(w)}")
    print(f"{len(all_tests)} test files, {len(covered)} covered, {len(missing)} missing, {len(doubled)} duplicated")
    return 1 if missing or doubled else 0


if __name__ == "__main__":
    sys.exit(main())
