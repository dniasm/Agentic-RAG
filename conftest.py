"""pytest only (day7's unittest run doesn't load this): every folder the backend writes to points at one
throw-away directory, set before anything from `backend` is imported, so no test touches real data."""
import os
import shutil
import tempfile

TEST_DATA = tempfile.mkdtemp(prefix="day7_test_")
os.environ.update({
    "QDRANT_URL": "",
    "QDRANT_PATH": os.path.join(TEST_DATA, "qdrant"),
    "DATABASE_PATH": os.path.join(TEST_DATA, "app.db"),
    "LIBRARY_DATA_DIR": os.path.join(TEST_DATA, "library-data"),
    "LOG_DIR": os.path.join(TEST_DATA, "logs"),   # importing backend.app starts the log files: never in the repo's logs/
})


def pytest_sessionfinish(session, exitstatus):
    from backend.rag import vectorstore
    vectorstore.close_client()
    from backend.library import catalog
    catalog.reset_connection()
    shutil.rmtree(TEST_DATA, ignore_errors=True)


import pytest  # noqa: E402

from backend import security  # noqa: E402

# TestClient's host: served as a local address (plain-HTTP cookies), like localhost in development
security.LOCAL_NAMES.add("testserver")


@pytest.fixture(autouse=True)
def _fresh_chat_allowance():
    """Each test starts with every account's chat allowance unused (the counts live in memory, process-wide)."""
    from backend import chat_limits
    chat_limits.allowance._sent.clear()


@pytest.fixture(autouse=True)
def _summaries_settled():
    """A summary refresh a test scheduled finishes before the next test starts (it runs on its own worker)."""
    yield
    from backend.library import summaries
    summaries.wait_idle()


@pytest.fixture(autouse=True)
def _fresh_embed_usage():
    """Each test starts with no passages embedded today (the counts are kept in the test database)."""
    from backend.library import catalog, embed_budget
    embed_budget._ensure()
    with catalog.tx() as c:
        c.execute("DELETE FROM embed_usage")


@pytest.fixture(autouse=True)
def _no_font_downloads(monkeypatch):
    """Tests never download fonts: a test that wants one replaces font_fetch._download itself."""
    from backend.library import font_fetch

    def offline(*_args, **_kwargs):
        raise font_fetch.FetchError("no network in tests")
    monkeypatch.setattr(font_fetch, "_download", offline)
    monkeypatch.setattr(font_fetch, "_failed", {})


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """LOG_DIR in a temporary folder for a test that calls setup_logging itself; the root logger's handlers (and the
    request id) are put back as they were afterwards."""
    import logging
    from services.common import log as logmod
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    for name in ("LOG_LEVEL", "LOG_RETENTION_DAYS"):
        monkeypatch.delenv(name, raising=False)
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    service = logmod._service
    yield tmp_path
    for handler in list(root.handlers):
        root.removeHandler(handler)
        if handler not in before:
            handler.close()
    for handler in before:
        root.addHandler(handler)
    root.setLevel(level)
    logmod._service = service
    logmod._request_id.set("-")
