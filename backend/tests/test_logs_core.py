"""services/common/log.py: line format, daily files, audit file, retention, secret masking, failing writes, the
request id (valid ids, scopes, copying into threads), library events."""
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from backend.library.logs import event
from services.common import log as logmod
from services.common.log import (
    audit, get_request_id, in_context, request_id_scope, safe_path, set_request_id, setup_logging, valid_request_id,
)


def lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def today():
    return date.today().isoformat()


def service_file(log_dir, service="orchestrator"):
    return log_dir / service / f"{today()}.log"


def audit_file(log_dir, service="orchestrator"):
    return log_dir / "audit" / f"{service}-{today()}.log"


# ---------------------------------------------------------------- format and files
def test_line_format(log_dir):
    setup_logging("orchestrator")
    set_request_id("abc")
    logging.getLogger("backend.x").info("x=%s", 1)
    assert re.match(r"^\d\d:\d\d:\d\d INFO    \[orchestrator\] \[abc\] backend\.x: x=1$", lines(service_file(log_dir))[-1])


def test_default_request_id_is_a_dash(log_dir):
    setup_logging("document")
    logging.getLogger("t").info("hello")
    assert "[document] [-]" in lines(service_file(log_dir, "document"))[-1]


def test_audit_goes_to_both_files_and_plain_lines_only_to_the_service_file(log_dir):
    setup_logging("orchestrator")
    logging.getLogger("t").info("plain line")
    audit("upload", doc="ab12", name="My Notes.pdf")
    audit_lines = lines(audit_file(log_dir))
    assert len(audit_lines) == 1 and audit_lines[0].endswith('upload doc=ab12 name="My Notes.pdf"')
    assert any(line.endswith('upload doc=ab12 name="My Notes.pdf"') for line in lines(service_file(log_dir)))
    assert not any("plain line" in line for line in audit_lines)


def test_audit_is_kept_whatever_the_level_and_long_values_are_cut(log_dir, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    setup_logging("orchestrator")
    logging.getLogger("t").info("quiet")
    audit("ask", question="q" * 1000)
    text = audit_file(log_dir).read_text(encoding="utf-8")
    assert "ask question=" in text and "q" * 300 in text and "q" * 301 not in text
    assert "quiet" not in service_file(log_dir).read_text(encoding="utf-8")


def test_library_events_are_audit_lines_with_the_request_id(log_dir):
    setup_logging("orchestrator")
    set_request_id("req-1")
    event("rename", doc="ab12", old="A", new="B C")
    assert lines(audit_file(log_dir))[-1].endswith('[req-1] audit: rename doc=ab12 old=A new="B C"')


def test_bad_level_and_retention_fall_back_with_a_warning(log_dir, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "verbose")
    monkeypatch.setenv("LOG_RETENTION_DAYS", "abc")
    setup_logging("orchestrator")
    assert logging.getLogger().level == logging.INFO
    text = service_file(log_dir).read_text(encoding="utf-8")
    assert "LOG_LEVEL" in text and "LOG_RETENTION_DAYS" in text


def test_uvicorn_lines_reach_the_file_once(log_dir, capsys):
    setup_logging("orchestrator")
    logging.getLogger("uvicorn.error").info("Started server process")
    assert "uvicorn.error: Started server process" in service_file(log_dir).read_text(encoding="utf-8")
    assert capsys.readouterr().out.count("Started server process") == 1


# ---------------------------------------------------------------- daily files and retention
def emit_with(handlers, name, *messages):
    logger = logging.getLogger(name)
    logger.propagate = False
    for handler in handlers:
        logger.addHandler(handler)
    try:
        for message in messages:
            logger.warning(message)
    finally:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()


def test_a_new_file_each_day(tmp_path):
    days = [date(2026, 11, 26)]
    handler = logmod.DailyFileHandler(tmp_path / "orchestrator", "{date}.log", 30, today=lambda: days[0])
    logger = logging.getLogger("daily-test")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.warning("one")
        days[0] = date(2026, 11, 27)
        logger.warning("two")
    finally:
        logger.removeHandler(handler)
        handler.close()
    assert sorted(p.name for p in (tmp_path / "orchestrator").iterdir()) == ["2026-11-26.log", "2026-11-27.log"]


def test_old_service_files_are_deleted_but_audit_files_are_kept(tmp_path):
    (tmp_path / "orchestrator").mkdir()
    (tmp_path / "audit").mkdir()
    for name in ("orchestrator/2026-10-01.log", "orchestrator/2026-11-20.log", "audit/orchestrator-2026-01-01.log"):
        (tmp_path / name).write_text("old\n")
    day = lambda: date(2026, 11, 26)  # noqa: E731
    emit_with([logmod.DailyFileHandler(tmp_path / "orchestrator", "{date}.log", 30, today=day),
               logmod.DailyFileHandler(tmp_path / "audit", "orchestrator-{date}.log", 0, today=day)],
              "retention-test", "x")
    assert not (tmp_path / "orchestrator" / "2026-10-01.log").exists()
    assert (tmp_path / "orchestrator" / "2026-11-20.log").exists()
    assert (tmp_path / "audit" / "orchestrator-2026-01-01.log").exists()


def test_an_unwritable_log_folder_is_reported_once_and_never_raises(log_dir, monkeypatch, capsys):
    blocker = log_dir / "blocked"
    blocker.write_text("i am a file")
    monkeypatch.setenv("LOG_DIR", str(blocker))
    setup_logging("orchestrator")
    for i in range(5):
        logging.getLogger("t").info("still fine %s", i)
    err = capsys.readouterr().err
    assert 1 <= len(err.strip().splitlines()) <= 2   # one line per file handler, not one per log line
    assert "Traceback" not in err and "--- Logging error ---" not in err


# ---------------------------------------------------------------- masking
def test_masks_this_apps_secrets(log_dir):
    setup_logging("orchestrator")
    logging.getLogger("t").info(
        "groq=gsk_abcdefghijklmn gemini=AIzaSyA1234567890abcdefghijk voyage=pa-abcdefghijklmnopqrstuvwx "
        "tavily=tvly-abcdefghijkl password=hunter2 tokens=1200 Authorization: Bearer xyz999 "
        "Cookie: login=Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA; theme=dark")
    text = service_file(log_dir).read_text(encoding="utf-8")
    for expected in ("gsk_***", "AIza***", "pa-***", "tvly-***", "password=***", "tokens=1200", "Authorization: ***", "Cookie: ***"):
        assert expected in text, expected
    for leaked in ("abcdefghijklmn", "hunter2", "xyz999", "Zm9vYmFy", "AIzaSyA12"):
        assert leaked not in text, leaked


def test_a_set_cookie_header_and_a_bare_login_token_are_masked():
    assert logmod.mask("Set-Cookie: login=abcdefghijklmnopqrstuvwxyz; Path=/") == "Set-Cookie: ***"
    assert logmod.mask("sent login=abcdefghijklmnopqrstuvwxyz ok") == "sent login=*** ok"


def test_tracebacks_are_masked(log_dir):
    setup_logging("orchestrator")
    try:
        raise RuntimeError("Groq said no to gsk_abcdefghijklmn (password=hunter2)")
    except RuntimeError:
        logging.getLogger("t").exception("failed")
    text = service_file(log_dir).read_text(encoding="utf-8")
    assert "Traceback" in text and "abcdefghijklmn" not in text and "hunter2" not in text


# ---------------------------------------------------------------- the request id
def test_set_request_id_starts_or_continues_a_trace():
    assert re.fullmatch(r"[0-9a-f]{12}", set_request_id())
    assert set_request_id("r1") == "r1" and get_request_id() == "r1"
    set_request_id("-")


def test_only_safe_ids_are_accepted():
    assert valid_request_id("abc-1.2_x") == "abc-1.2_x"
    for bad in (None, "", "a b", "x" * 65, "a\nb", "a=b", "é"):
        assert valid_request_id(bad) is None


def test_a_scope_restores_the_previous_id():
    set_request_id("outer")
    with request_id_scope("inner") as rid:
        assert rid == "inner" == get_request_id()
    assert get_request_id() == "outer"
    set_request_id("-")


def test_a_decoded_newline_in_a_path_cannot_forge_a_line():
    assert "\n" not in safe_path("/api/x\nERROR forged") and "\\n" in safe_path("/api/x\nERROR forged")


def test_the_request_id_is_copied_into_threads_and_pool_workers(log_dir):
    setup_logging("orchestrator")
    set_request_id("trace-9")
    seen = {}
    plain = {}
    t = threading.Thread(target=in_context(lambda: seen.setdefault("thread", get_request_id())))
    t.start()
    t.join()
    t2 = threading.Thread(target=lambda: plain.setdefault("thread", get_request_id()))
    t2.start()
    t2.join()
    with ThreadPoolExecutor(max_workers=3) as pool:
        seen["pool"] = list(pool.map(in_context(lambda n: get_request_id()), range(6)))
        seen["submit"] = pool.submit(in_context(get_request_id)).result()
    assert seen["thread"] == "trace-9" and seen["pool"] == ["trace-9"] * 6 and seen["submit"] == "trace-9"
    assert plain["thread"] == "-"   # without in_context a thread starts with no id: why the wrapper exists
