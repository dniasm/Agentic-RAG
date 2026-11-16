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
    "UPLOAD_DIR": os.path.join(TEST_DATA, "uploads"),
    "LIBRARY_DATA_DIR": os.path.join(TEST_DATA, "library-data"),
})


def pytest_sessionfinish(session, exitstatus):
    from backend.rag import vectorstore
    vectorstore.close_client()
    from backend.library import catalog
    catalog.reset_connection()
    shutil.rmtree(TEST_DATA, ignore_errors=True)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_font_downloads(monkeypatch):
    """Tests never download fonts: a test that wants one replaces font_fetch._download itself."""
    from backend.library import font_fetch

    def offline(*_args, **_kwargs):
        raise font_fetch.FetchError("no network in tests")
    monkeypatch.setattr(font_fetch, "_download", offline)
    monkeypatch.setattr(font_fetch, "_failed", {})
