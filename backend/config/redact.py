"""Keeps API key text out of logs, stored errors and messages.

Some client errors can carry the request (with its key) in their text. scrub() replaces every configured key with
"[key]"; KeyScrubber does the same to every log line, its arguments and its traceback, so a key can't reach
all.log, events.log or the console even through an error we didn't expect.
"""
import logging

from backend.config.settings import settings

_MIN_LENGTH = 8   # shorter values aren't real keys, and replacing them would mangle ordinary text


def secrets() -> list[str]:
    """Every configured API key (any setting whose name holds "api_key"), longest first."""
    found = {str(v) for name, v in settings.model_dump().items()
             if "api_key" in name and v and len(str(v)) >= _MIN_LENGTH}
    return sorted(found, key=len, reverse=True)


def scrub(text: str) -> str:
    for secret in secrets():
        if secret in text:
            text = text.replace(secret, "[key]")
    return text


class KeyScrubber(logging.Filter):
    """A handler filter: the record's message and traceback with every key replaced."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            clean = scrub(message)
            if clean != message:
                record.msg, record.args = clean, ()
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = scrub(record.exc_text)
        except Exception:
            pass   # never lose a log line over this
        return True


def guard_handlers(*loggers: logging.Logger) -> None:
    """Add the scrubber to every handler of these loggers (the root logger when none are given). Safe to repeat."""
    for logger in loggers or (logging.getLogger(),):
        for handler in logger.handlers:
            if not any(isinstance(f, KeyScrubber) for f in handler.filters):
                handler.addFilter(KeyScrubber())
