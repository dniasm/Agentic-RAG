"""Two log files in data/logs/, both rotating (5 MB, 5 old files kept):

  events.log   the major events only, one line each: uploads, indexing, renames, edits, undos, queued changes and how
               they ended, chats (with token use), and every error.
  all.log      everything: the major events too, plus the agent's tool calls and results, waits, model fallbacks, and
               the warnings of the libraries. Our own debug lines are in; the libraries' debug chatter is not.

event("rename", doc="ab12", old="A", new="B") writes  2026-11-12 08:01:19 rename doc="ab12" old="A" new="B"
"""
import json
import logging
import logging.handlers

from backend.library import config

EVENTS = "backend.library.events"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_done = False


class _Major(logging.Filter):
    """What goes in events.log: the events, and anything that is an error."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name == EVENTS or record.levelno >= logging.ERROR


def setup() -> None:
    """Attach the log files and the console. Safe to call more than once."""
    global _done
    if _done:
        return
    _done = True
    folder = config.DATA_DIR / "logs"
    folder.mkdir(parents=True, exist_ok=True)

    def rotating(name: str) -> logging.Handler:
        h = logging.handlers.RotatingFileHandler(folder / name, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        h.setFormatter(logging.Formatter(_FORMAT, "%Y-%m-%d %H:%M:%S"))
        return h

    everything, major = rotating("all.log"), rotating("events.log")
    everything.setLevel(logging.DEBUG)
    major.setLevel(logging.INFO)
    major.addFilter(_Major())
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    console.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # day7's backend/app.py already prints to the console (logging.basicConfig): a second console handler would
    # print every line twice
    has_console = any(type(h) is logging.StreamHandler for h in root.handlers)
    for handler in (everything, major) if has_console else (everything, major, console):
        root.addHandler(handler)
    from backend.config import redact
    redact.guard_handlers(root, *(logging.getLogger(n) for n in ("uvicorn", "uvicorn.error", "uvicorn.access")))
    logging.getLogger("backend.library").setLevel(logging.DEBUG)    # our own debug lines reach all.log
    for noisy in ("httpcore", "google_genai.models", "google_genai._api_client", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def event(name: str, **fields) -> None:
    """Record a major event: its name, then key="value" pairs."""
    parts = " ".join(f"{k}={json.dumps(v, ensure_ascii=False, default=str)}" for k, v in fields.items())
    logging.getLogger(EVENTS).info("%s %s", name, parts)
