"""scripts/logs.py: parse the shared line format, merge services by time, filters, audit-only, tracebacks, damaged lines."""
import importlib.util
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("logs_viewer", ROOT / "scripts" / "logs.py")
viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viewer)

DAY = date(2026, 11, 26)


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "orchestrator").mkdir()
    (tmp_path / "document").mkdir()
    (tmp_path / "audit").mkdir()
    (tmp_path / "orchestrator" / "2026-11-26.log").write_text(
        "10:00:01 INFO    [orchestrator] [r1] backend.request_log: POST /api/library/upload -> 200 41ms\n"
        "10:00:03 WARNING [orchestrator] [r2] backend.x: slow thing\n"
        "10:00:05 ERROR   [orchestrator] [r2] backend.request_log: unhandled GET /api/boom\n"
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in boom\n'
        "RuntimeError: kaput\n"
        "10:00:06 INFO    [orchestrator] [r2] audit: rename doc=ab12 user=u1 new=\"B C\"\n"
        "not a log line at all\n"
        "10:0\n", encoding="utf-8")
    (tmp_path / "document" / "2026-11-26.log").write_text(
        "10:00:02 INFO    [document] [r1] services.common.server: GetDocument -> OK 3ms\n", encoding="utf-8")
    (tmp_path / "audit" / "orchestrator-2026-11-26.log").write_text(
        "10:00:06 INFO    [orchestrator] [r2] audit: rename doc=ab12 user=u1 new=\"B C\"\n", encoding="utf-8")
    return tmp_path


def run(tree, capsys, *args):
    code = viewer.main(["--dir", str(tree), "--day", "2026-11-26", *args])
    return code, capsys.readouterr().out.splitlines()


def test_parse_one_line():
    rec = viewer.parse("10:00:01 INFO    [orchestrator] [r1] backend.x: a: b", DAY, "fallback")
    assert rec["ts"] == datetime(2026, 11, 26, 10, 0, 1) and rec["level"] == "INFO"
    assert rec["service"] == "orchestrator" and rec["request_id"] == "r1" and rec["logger"] == "backend.x"
    assert rec["message"] == "a: b"
    assert viewer.parse("garbage", DAY, "s") is None


def test_fields_read_left_to_right_and_a_quoted_value_hides_its_own_pairs():
    assert viewer.fields('rename doc=ab12 new="B user=evil" user=u1') == {"doc": "ab12", "new": "B user=evil", "user": "u1"}


def test_services_are_merged_by_time(tree, capsys):
    code, out = run(tree, capsys)
    assert code == 0
    stamps = [line.split()[1] for line in out if line[:4] == "2026"]
    assert stamps == sorted(stamps)
    assert [line for line in out if "[document]" in line][0].split()[1] == "10:00:02"


def test_one_request_across_services(tree, capsys):
    _, out = run(tree, capsys, "--request", "r1")
    assert len(out) == 2 and "[orchestrator]" in out[0] and "[document]" in out[1]


def test_filters_by_service_level_and_text(tree, capsys):
    assert all("[document]" in line for line in run(tree, capsys, "--service", "document")[1])
    levels = run(tree, capsys, "--level", "WARNING")[1]
    assert levels and all(" WARNING " in line or " ERROR " in line for line in levels if line[:4] == "2026")
    assert len(run(tree, capsys, "--grep", "SLOW THING")[1]) == 1


def test_a_traceback_stays_with_its_record_and_damaged_lines_are_skipped(tree, capsys):
    _, out = run(tree, capsys, "--request", "r2", "--level", "ERROR")
    text = "\n".join(out)
    assert "Traceback (most recent call last)" in text and "RuntimeError: kaput" in text
    assert "not a log line" not in "\n".join(run(tree, capsys, "--request", "r1")[1])


def test_audit_only_and_by_user(tree, capsys):
    _, out = run(tree, capsys, "--audit")
    assert len(out) == 1 and "audit: rename doc=ab12" in out[0]
    assert len(run(tree, capsys, "--audit", "--user", "u1")[1]) == 1
    assert run(tree, capsys, "--audit", "--user", "nobody")[1] == []


def test_bad_arguments_exit_with_2(tree, capsys):
    assert viewer.main(["--dir", str(tree), "--level", "loud"]) == 2
    assert viewer.main(["--dir", str(tree), "--day", "yesterday-ish"]) == 2
    assert viewer.main(["--dir", str(tree), "--since", "soon"]) == 2


def test_since_accepts_relative_and_iso_times():
    assert (datetime.now() - viewer.parse_since("30m")).total_seconds() == pytest.approx(1800, abs=5)
    assert viewer.parse_since("2026-11-26T10:00:00") == datetime(2026, 11, 26, 10, 0, 0)


def test_the_folder_defaults_to_log_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    assert viewer.build_parser().parse_args([]).dir == str(tmp_path)
