"""Read the shared log files: merge services by time, filter, follow one request across the orchestrator and the services.

Usage (repo root):
  python scripts/logs.py --since 30m --level WARNING
  python scripts/logs.py --audit --user <id> --since 1d
  python scripts/logs.py --request <id>
  python scripts/logs.py --follow --service orchestrator
Reads logs/<service>/<date>.log (or logs/audit/<service>-<date>.log with --audit); LOG_DIR or --dir moves the folder.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

LINE = re.compile(r"^(\d\d:\d\d:\d\d) (\w+)\s+\[([^\]]*)\] \[([^\]]*)\] (\S+?): (.*)$")
TRUNCATED = re.compile(r"^\d\d:\d")       # a record cut off mid-write: not a traceback line
PAIR = re.compile(r'\s*(\w+)=("(?:[^"\\]|\\.)*"|\S*)')
LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
REPO_ROOT = Path(__file__).resolve().parent.parent


def parse(line: str, day: date, service: str) -> dict | None:
    """One log line -> record, or None when it is not in the shared format."""
    m = LINE.match(line.rstrip("\r\n"))
    if not m:
        return None
    clock, level, svc, rid, logger, message = m.groups()
    try:
        ts = datetime.combine(day, datetime.strptime(clock, "%H:%M:%S").time())
    except ValueError:
        return None
    return {"ts": ts, "level": level, "service": svc or service, "request_id": rid, "logger": logger, "message": message}


def fields(message: str) -> dict[str, str]:
    """`event k=v k="a b"` -> {k: v}. Reads left to right, so a quoted value never leaks pairs of its own."""
    out: dict[str, str] = {}
    pos = message.find(" ")
    while 0 <= pos < len(message):
        m = PAIR.match(message, pos)
        if not m:
            break
        key, value = m.groups()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                value = value[1:-1]
        out[key] = value
        pos = m.end()
    return out


def read_file(path: Path, day: date, service: str) -> list[dict]:
    records: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return records
    for number, raw in enumerate(text.splitlines()):
        rec = parse(raw, day, service)
        if rec:
            rec["src"] = (str(path), number)
            records.append(rec)
        elif records and raw.strip() and not TRUNCATED.match(raw):
            records[-1]["message"] += "\n" + raw      # traceback or other continuation
    return records


def load(log_dir: Path, days: list[date], audit: bool) -> list[dict]:
    """All records of the given days, sorted by time (audit files only, or the service files)."""
    log_dir = Path(log_dir)
    records: list[dict] = []
    for day in days:
        stamp = day.isoformat()
        if audit:
            for path in sorted((log_dir / "audit").glob(f"*-{stamp}.log")):
                records += read_file(path, day, path.name[: -len(stamp) - 5])
        else:
            for folder in sorted(p for p in log_dir.glob("*") if p.is_dir() and p.name != "audit"):
                records += read_file(folder / f"{stamp}.log", day, folder.name)
    return sorted(records, key=lambda r: r["ts"])


def parse_since(text: str) -> datetime:
    m = re.fullmatch(r"(\d+)([smhd])", text)
    if m:
        unit = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[m.group(2)]
        return datetime.now() - timedelta(**{unit: int(m.group(1))})
    return datetime.fromisoformat(text)


def render(rec: dict) -> str:
    return (f"{rec['ts']:%Y-%m-%d %H:%M:%S} {rec['level']:<7} [{rec['service']}] [{rec['request_id']}] "
            f"{rec['logger']}: {rec['message']}")


def keep(rec: dict, args: argparse.Namespace, since: datetime | None) -> bool:
    if since and rec["ts"] < since:
        return False
    if args.service and rec["service"] != args.service:
        return False
    if LEVELS.get(rec["level"], 0) < LEVELS[args.level.upper()]:
        return False
    if args.request and rec["request_id"] != args.request:
        return False
    if args.grep and args.grep.lower() not in rec["message"].lower():
        return False
    if args.user and fields(rec["message"]).get("user") != args.user:
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="View the app's log files.")
    p.add_argument("--dir", default=os.getenv("LOG_DIR") or str(REPO_ROOT / "logs"), help="log folder (default LOG_DIR or ./logs)")
    p.add_argument("--day", help="YYYY-MM-DD (default today)")
    p.add_argument("--since", help="30m, 2h, 1d or an ISO time; may span days")
    p.add_argument("--service")
    p.add_argument("--level", default="DEBUG", help="minimum level")
    p.add_argument("--audit", action="store_true", help="audit files only")
    p.add_argument("--request", metavar="ID")
    p.add_argument("--user", metavar="ID")
    p.add_argument("--grep", metavar="TEXT")
    p.add_argument("--follow", action="store_true", help="keep printing new lines (poll 1 s)")
    return p


def span(first: date, last: date) -> list[date]:
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    if args.level.upper() not in LEVELS:
        print(f"unknown level {args.level}", file=sys.stderr)
        return 2
    try:
        last = date.fromisoformat(args.day) if args.day else date.today()
        since = parse_since(args.since) if args.since else None
    except ValueError as exc:
        print(f"bad date or time: {exc}", file=sys.stderr)
        return 2
    first = min(since.date(), last) if since else last
    days = span(first, last)
    log_dir = Path(args.dir)

    seen: set = set()
    while True:
        for rec in load(log_dir, days, args.audit):
            if rec["src"] not in seen and keep(rec, args, since):
                print(render(rec), flush=True)
            seen.add(rec["src"])
        if not args.follow:
            return 0
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            return 0
        if not args.day:                         # a new day starts a new file
            days = span(first, date.today())


if __name__ == "__main__":
    sys.exit(main())
