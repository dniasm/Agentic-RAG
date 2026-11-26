"""pytest only (day7's unittest run doesn't load this): every folder the backend writes to points at one
throw-away directory, set before anything from `backend` is imported, so no test touches real data."""
import os
import shutil
import tempfile

TEST_DATA = tempfile.mkdtemp(prefix="day7_test_")
os.environ.update({
    "QDRANT_URL": "",
    "DATABASE_PATH": os.path.join(TEST_DATA, "app.db"),   # the orchestrator opens none: a test that finds this file made fails
    "LIBRARY_DATA_DIR": os.path.join(TEST_DATA, "library-data"),
    "LOG_DIR": os.path.join(TEST_DATA, "logs"),   # importing backend.app starts the log files: never in the repo's logs/
    "SERVICES_DATA_DIR": os.path.join(TEST_DATA, "services"),   # each service's own SQLite file
})

# The Memory service never calls Voyage in tests: the shared Voyage client is a stand-in that embeds words into a
# vector (texts sharing words are close, the same text always gives the same vector), reranks nothing, and the rate
# limit is lifted. A test that needs another behaviour patches backend.config.voyage itself, as before.
import hashlib  # noqa: E402
import math  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from backend.config import voyage as _voyage  # noqa: E402


def fake_embedding(text: str, dims: int) -> list[float]:
    vec = [0.0] * dims
    for word in text.lower().split():
        vec[int(hashlib.sha256(word.encode()).hexdigest(), 16) % dims] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FakeVoyage:
    def embed(self, texts, model, input_type, output_dimension):
        return SimpleNamespace(embeddings=[fake_embedding(t, output_dimension) for t in texts])

    def rerank(self, *args, **kwargs):
        raise RuntimeError("no Voyage reranking in tests")


_voyage.client = lambda: FakeVoyage()
_voyage.limiter = lambda: _voyage.RateLimiter(10 ** 6, 0)

# The Conversation, User and Memory services run for the whole session, in this process (each its own thread and event loop) on
# an ephemeral port; the orchestrator's settings dial them, so chats, accounts, usage and memory go through the real
# gRPC path.
from services.common.inprocess import RunningService  # noqa: E402
from services.user import db as _user_store  # noqa: E402
from services.user.server import build_service as _build_user  # noqa: E402
from services.memory import db as _memory_store  # noqa: E402
from services.memory.server import build_service as _build_memory  # noqa: E402
from services.memory.vectors import EpisodeStore as _EpisodeStore  # noqa: E402
from services.document.server import build_service as _build_document  # noqa: E402
from services.conversation import db as _conversation_store  # noqa: E402
from services.conversation.server import build_service as _build_conversation  # noqa: E402

CONVERSATION_DB = _conversation_store.open_db()   # the service's own database
CONVERSATION = RunningService(lambda: _build_conversation(CONVERSATION_DB)).start()
USER_DB = _user_store.open_db()   # a test that must change rows the API can't (expire a login, wipe accounts) uses it
USER = RunningService(lambda: _build_user(USER_DB)).start()
MEMORY_DB = _memory_store.open_db()   # facts; the episodes are in <services dir>/memory_qdrant
MEMORY_EPISODES = _EpisodeStore()
MEMORY = RunningService(lambda: _build_memory(MEMORY_DB, MEMORY_EPISODES)).start()
DOCUMENT = RunningService(_build_document).start()   # passages in <services dir>/document_qdrant, caches in document.db
from backend.config.settings import settings as _settings  # noqa: E402

_settings.conversation_service_addr = CONVERSATION.address   # not the environment: config tests read that
_settings.user_service_addr = USER.address
_settings.memory_service_addr = MEMORY.address
_settings.document_service_addr = DOCUMENT.address


def pytest_sessionfinish(session, exitstatus):
    from backend import clients
    from backend.config import api_meter
    from backend.api.routes import chat
    chat.flush_pending_memory()   # facts and episodes still queued on the memory workers go to the Memory service
    chat._episode_worker.submit(lambda: None).result(timeout=30)
    api_meter.flush()   # counts still in memory go to the User service while it is running (not at interpreter exit)
    clients.reset_clients()
    CONVERSATION.stop()
    DOCUMENT.stop()   # (closes its Qdrant folder and document.db)
    MEMORY.stop()
    USER.stop()
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
    """Each test starts with no passages embedded today (the counts are kept in the User service's database)."""
    with USER_DB.tx() as c:
        c.execute("DELETE FROM embed_usage")


@pytest.fixture
def user_db():
    """The User service's database (the one the in-process service runs on), for tests that must change rows the
    API can't: expire a login, count something for an account, wipe the accounts."""
    return USER_DB


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
