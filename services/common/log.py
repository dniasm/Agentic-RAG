"""Shared logging for the orchestrator and every service: one line format, a request id per trace, daily files
(logs/<service>/<date>.log), an audit file (logs/audit/<service>-<date>.log), secrets masked.

It lives in services/common/ because both backend/ (the orchestrator) and services/ already import from there."""
import json
import logging
import os
import re
import sys
import uuid
import contextvars
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from pathlib import Path
from typing import Callable

_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_service = "app"

_REPO_ROOT = Path(__file__).resolve().parents[2]
REQUEST_ID_HEADER = "x-request-id"   # the HTTP header and the gRPC metadata key that carry the id
MAX_FIELD = 300   # a logged value (a name, a question, an error text) is cut at this many characters
_DATE_IN_NAME = re.compile(r"\d{4}-\d{2}-\d{2}")

_MASKS = (
    (re.compile(r"gsk_[A-Za-z0-9]{10,}"), "gsk_***"),                    # Groq
    (re.compile(r"AIza[A-Za-z0-9_-]{20,}"), "AIza***"),                  # Gemini
    (re.compile(r"pa-[A-Za-z0-9_-]{20,}"), "pa-***"),                    # Voyage
    (re.compile(r"tvly-[A-Za-z0-9_-]{10,}"), "tvly-***"),                # Tavily
    (re.compile(r"""(?i)\b((?:set-)?cookie)(["']?\s*[=:]\s*["']?)[^\r\n"']+"""), r"\1\2***"),   # Cookie / Set-Cookie headers
    (re.compile(r"\blogin=[A-Za-z0-9_-]{16,}"), "login=***"),            # the login cookie's token
    (re.compile(r"""(?i)\b(authorization)(["']?\s*[=:]\s*["']?)(?:bearer|basic)\s+[^\s"',}]+"""), r"\1\2***"),
    (re.compile(r"""(?i)\b(password|passwd|token|secret|api_key|authorization|session)(["']?\s*[=:]\s*["']?)[^\s"',}]+"""),
     r"\1\2***"),
)


def mask(text: str) -> str:
    """Hide API keys, login cookies and password/token-like values in a log line."""
    for pattern, repl in _MASKS:
        text = pattern.sub(repl, text)
    return text


class MaskingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return mask(super().format(record))


class DailyFileHandler(logging.Handler):
    """Appends to <directory>/<pattern with {date}>, a new file each local day; old files are pruned on each open."""

    def __init__(self, directory: Path, pattern: str, retention_days: int,
                 today: Callable[[], date] = date.today) -> None:
        super().__init__()
        self.directory = Path(directory)
        self.pattern = pattern
        self.retention_days = retention_days
        self._today = today
        self._day: date | None = None
        self._stream = None
        self._failed_day: date | None = None

    def _open(self, day: date) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self.directory.mkdir(parents=True, exist_ok=True)
        self._stream = open(self.directory / self.pattern.format(date=day.isoformat()), "a", encoding="utf-8")
        self._day = day
        self._prune(day)

    def _prune(self, today: date) -> None:
        if self.retention_days <= 0:
            return
        for path in self.directory.glob("*.log"):
            found = _DATE_IN_NAME.search(path.name)
            if not found:
                continue
            try:
                if (today - date.fromisoformat(found.group())).days > self.retention_days:
                    path.unlink()
            except (ValueError, OSError):
                pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = self._today()
            if day == self._failed_day:
                return          # already reported for this date; retry tomorrow
            if self._stream is None or day != self._day:
                self._open(day)
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception as exc:
            self._failed_day = self._day_or_none()
            print(f"logging: cannot write {self.directory}: {exc}", file=sys.stderr)

    def _day_or_none(self) -> date | None:
        try:
            return self._today()
        except Exception:
            return None

    def close(self) -> None:
        with self.lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        super().close()


class _Context(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.service = _service
        record.request_id = _request_id.get()
        return True


class _AuditOnly(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name == "audit"


def setup_logging(service: str) -> None:
    """Call once in each entry point (main.py, worker.py)."""
    global _service
    _service = service

    formatter = MaskingFormatter(
        "%(asctime)s %(levelname)-7s [%(service)s] [%(request_id)s] %(name)s: %(message)s",
        "%H:%M:%S",
    )
    log_dir = Path(os.getenv("LOG_DIR") or _REPO_ROOT / "logs")
    bad_retention = None
    try:
        retention = int(os.getenv("LOG_RETENTION_DAYS") or 30)
    except ValueError:
        bad_retention, retention = os.getenv("LOG_RETENTION_DAYS"), 30

    console = logging.StreamHandler(sys.stdout)
    service_file = DailyFileHandler(log_dir / service, "{date}.log", retention)
    audit_file = DailyFileHandler(log_dir / "audit", f"{service}-{{date}}.log", 0)
    audit_file.addFilter(_AuditOnly())
    handlers = [console, service_file, audit_file]
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(_Context())

    for old in logging.getLogger().handlers:
        if isinstance(old, DailyFileHandler):
            old.close()

    level = (os.getenv("LOG_LEVEL") or "INFO").upper()
    bad_level = None
    if not isinstance(logging.getLevelName(level), int):
        bad_level, level = os.getenv("LOG_LEVEL"), "INFO"
    logging.basicConfig(
        level=level,
        handlers=handlers,
        force=True,          # replaces any earlier basicConfig
    )
    console.setLevel(level)   # a logger set lower than the root (backend.library: DEBUG) reaches the files only
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):   # uvicorn's own handlers would print a second, unmasked copy
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.setLevel(logging.NOTSET)                                # LOG_LEVEL decides, not uvicorn's --log-level
        uv.propagate = name != "uvicorn.access"                    # our access line replaces uvicorn's
    logging.getLogger("audit").setLevel(logging.INFO)   # audit lines are kept whatever LOG_LEVEL is
    if bad_level is not None:
        logging.getLogger(__name__).warning("LOG_LEVEL=%r is not a level; using INFO", bad_level)
    if bad_retention is not None:
        logging.getLogger(__name__).warning("LOG_RETENTION_DAYS=%r is not a number; using 30", bad_retention)


def add_filter(log_filter: logging.Filter) -> None:
    """Put a filter on every handler set up by setup_logging (the backend adds its exact-key scrubber this way)."""
    for handler in logging.getLogger().handlers:
        handler.addFilter(log_filter)


def set_request_id(value: str | None = None) -> str:
    """Start a trace (no argument) or continue one (pass the incoming ID)."""
    rid = value or uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


_VALID_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


def valid_request_id(value: str | None) -> str | None:
    """The incoming id when it is safe to log and echo (letters, digits, . _ -, 1-64 long), else None (callers then start a new one)."""
    return value if value and _VALID_ID.fullmatch(value) else None


@contextmanager
def request_id_scope(value: str | None = None):
    """A trace for the `with` block only: the id from before it comes back afterwards (for loops that handle many units in one task)."""
    token = _request_id.set(value or uuid.uuid4().hex[:12])
    try:
        yield _request_id.get()
    finally:
        _request_id.reset(token)


def in_context(fn: Callable) -> Callable:
    """`fn` wrapped to run in a copy of the caller's context, for a thread or pool worker: its log lines then carry
    the request id of the code that handed the work over (threads do not inherit it by themselves)."""
    ctx = contextvars.copy_context()
    return lambda *args, **kwargs: ctx.copy().run(fn, *args, **kwargs)   # a copy per call: pool.map runs one wrapper on several threads


def get_request_id() -> str:
    return _request_id.get()


def safe_path(path: str) -> str:
    """A request path with control characters escaped, so a decoded `%0a` cannot forge a log line."""
    return path.encode("unicode_escape").decode("ascii")


def audit(event: str, **fields) -> None:
    """Record a business event: `event key=value ...` (values with spaces are JSON-quoted)."""
    parts = [event]
    for key, value in fields.items():
        text = str(value)
        if len(text) > MAX_FIELD:
            text = text[:MAX_FIELD] + "..."
        if not text or re.search(r"[\s\"]", text):
            text = json.dumps(text)
        parts.append(f"{key}={text}")
    logging.getLogger("audit").info(" ".join(parts))
