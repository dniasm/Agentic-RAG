"""Gemini embedding keys (backend/config/gemini.py): a refused request moves to the next key, a key refused for the
day stays out until Google's next day, unexplained refusals back off, the site's day comes from each key's state,
and no key text reaches logs, events or messages."""
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.config import api_meter, gemini, redact
from backend.config.settings import settings
from backend.library import catalog, embed_budget, library
from services.document import ingestion
from services.document.embedding_cache import embed_query as real_embed_query   # (the library tests' fixture fakes both)
from services.document.ingestion import embed_paced as real_embed_paced

KEYS = ["AIzaFAKE_key_number_one_000", "AIzaFAKE_key_number_two_000",
        "AIzaFAKE_key_number_three_00", "AIzaFAKE_key_number_four_000"]
PER_DAY = ("429 RESOURCE_EXHAUSTED. Quota exceeded for metric embed_content_free_tier_requests, quotaId: "
           "EmbedContentRequestsPerDayPerUserPerProjectPerModel-FreeTier. Please retry in 12.5s.")
PER_MINUTE = ("429 RESOURCE_EXHAUSTED. quotaId: EmbedContentRequestsPerMinutePerUserPerProjectPerModel-FreeTier. "
              "Please retry in 3s.")
UNEXPLAINED = "429 RESOURCE_EXHAUSTED. Too many requests."


@pytest.fixture(autouse=True)
def four_keys(monkeypatch, user_db):
    for name, key in zip(("gemini_api_key", "gemini_api_key_2", "gemini_api_key_3", "gemini_api_key_4"), KEYS):
        monkeypatch.setattr(settings, name, key)
    monkeypatch.setattr(settings, "embed_site_per_day", None)
    monkeypatch.setattr(settings, "embed_per_key_day", 1000)
    monkeypatch.setattr(api_meter, "FLUSH_SECONDS", 3600.0)
    with api_meter._lock:
        api_meter._pending.clear()
    with user_db.tx() as c:
        c.execute("DELETE FROM api_usage")
    gemini.pool.reset()
    yield
    gemini.pool.reset()
    with api_meter._lock:
        api_meter._pending.clear()


class FakeClient:
    """Answers, or raises the next error in `errors[key]`."""

    def __init__(self, key, errors, sent):
        self.key, self.errors, self.sent = key, errors, sent

    def _answer(self, n):
        queue = self.errors.get(self.key, [])
        if queue:
            raise Exception(queue.pop(0))
        self.sent.append((self.key, n))
        return [[float(self.key)] * 3 for _ in range(n)]

    def embed_documents(self, texts, **_):
        return self._answer(len(texts))

    def embed_query(self, text, **_):
        return self._answer(1)[0]


@pytest.fixture
def clients(monkeypatch):
    errors, sent = {}, []
    monkeypatch.setattr(gemini, "embeddings", lambda key=0: FakeClient(key, errors, sent))
    return errors, sent


def counts():
    api_meter.flush()
    rows = [r for r in api_meter.rows_since(2) if r["provider"] == "gemini"]
    return {(r["key"], r["model"]): (r["calls"], r["limited"]) for r in rows}


# ---------------------------------------------------------------- fallback
def test_a_refused_batch_goes_whole_to_the_next_key_and_is_counted_there(clients):
    errors, sent = clients
    errors[0] = [PER_MINUTE]
    vectors = real_embed_paced(["one", "two", "three"], "T")
    assert vectors == [[1.0] * 3] * 3 and sent == [(1, 3)]       # one batch, whole, on key 2
    assert counts() == {(0, "documents"): (0, 1), (1, "documents"): (3, 0)}


def test_a_question_moves_to_the_next_key_too(clients):
    errors, sent = clients
    errors[0] = [PER_DAY]
    assert real_embed_query("a question nobody asked before 7f3a " + uuid.uuid4().hex) == [1.0] * 3
    assert counts()[(1, "questions")] == (1, 0)


def test_a_per_day_refusal_keeps_the_key_out_until_googles_next_day():
    now = datetime(2026, 11, 17, 20, 0, tzinfo=timezone.utc)          # 12:00 PST
    assert gemini.pool.mark(0, Exception(PER_DAY), now=now) == "day"
    midnight = datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc)      # 00:00 PST
    assert gemini.next_day_start(now) == midnight
    assert gemini.pool.out_for_day(0, now)
    assert 0 not in gemini.pool.available(4, now=(midnight - timedelta(seconds=1)).timestamp())
    assert 0 in gemini.pool.available(4, now=midnight.timestamp())


def test_googles_day_starts_at_pacific_midnight_across_daylight_saving():
    assert gemini.day_start(datetime(2026, 7, 1, 6, 59, tzinfo=timezone.utc)) == datetime(2026, 6, 30, 7, tzinfo=timezone.utc)
    assert gemini.next_day_start(datetime(2026, 10, 31, 12, tzinfo=timezone.utc)) == \
        datetime(2026, 11, 1, 7, tzinfo=timezone.utc)                  # still PDT
    assert gemini.next_day_start(datetime(2026, 11, 1, 12, tzinfo=timezone.utc)) == \
        datetime(2026, 11, 2, 8, tzinfo=timezone.utc)                  # the 25-hour day, then PST


def test_three_unexplained_refusals_in_a_row_back_off_for_an_hour():
    now = datetime(2026, 11, 17, 20, 0, tzinfo=timezone.utc)
    for _ in range(2):
        assert gemini.pool.mark(2, Exception(UNEXPLAINED), now=now) == "unexplained"
    assert gemini.pool.cooldowns(now=now.timestamp())[2] == gemini.SHORT_WAIT
    gemini.pool.mark(2, Exception(UNEXPLAINED), now=now)
    assert gemini.pool.cooldowns(now=now.timestamp())[2] == gemini.STRIKE_WAIT
    assert not gemini.pool.out_for_day(2, now)                         # backed off, not declared out for the day


def test_a_success_clears_the_strikes(clients):
    errors, _ = clients
    now = datetime.now(timezone.utc)
    gemini.pool.mark(0, Exception(UNEXPLAINED), now=now - timedelta(hours=2))
    gemini.pool.mark(0, Exception(UNEXPLAINED), now=now - timedelta(hours=2))
    real_embed_paced(["x"], "T")                                      # key 1 is back and answers
    gemini.pool.mark(0, Exception(UNEXPLAINED))
    assert gemini.pool.cooldowns()[0] <= gemini.SHORT_WAIT


def test_when_every_key_is_cooling_indexing_waits_then_succeeds(clients, monkeypatch):
    errors, sent = clients
    for k in range(4):
        errors[k] = [PER_MINUTE]
    slept = []
    monkeypatch.setattr(ingestion.time, "sleep", lambda s: (slept.append(s), gemini.pool.reset()))
    assert real_embed_paced(["a"], "T") == [[0.0] * 3]
    assert len(slept) == 1 and 0 < slept[0] <= 3 + 1 + 2


def test_when_every_key_is_out_for_the_day_indexing_gives_up_at_once(clients, monkeypatch):
    errors, sent = clients
    for k in range(4):
        errors[k] = [PER_DAY]
    monkeypatch.setattr(ingestion.time, "sleep", lambda s: pytest.fail("waited for a key that is out for the day"))
    with pytest.raises(gemini.AllKeysLimited) as e:
        real_embed_paced(["a"], "T")
    assert e.value.for_day and "for today" in str(e.value) and not sent


def test_questions_raise_as_before_when_every_key_is_limited(clients):
    errors, _ = clients
    for k in range(4):
        errors[k] = [PER_MINUTE]
    with pytest.raises(gemini.AllKeysLimited):
        real_embed_query("another new question 91be " + uuid.uuid4().hex)


# ---------------------------------------------------------------- the site's day
def test_the_day_is_what_the_keys_have_left():
    assert embed_budget.site_day() == {"total": 4000, "used": 0, "left": 4000}
    api_meter.count("gemini", 1, "questions", calls=300)
    assert embed_budget.site_day() == {"total": 4000, "used": 300, "left": 3700}


def test_a_key_out_for_the_day_leaves_nothing_and_indexing_stops_while_questions_go_on(clients, monkeypatch):
    errors, _ = clients
    monkeypatch.setattr(settings, "embed_per_key_day", 100)
    monkeypatch.setattr(settings, "embed_per_user_day", 0)
    monkeypatch.setattr(settings, "embed_index_share", 0.8)
    for k in (0, 1, 2):
        gemini.pool.mark(k, Exception(PER_DAY))
    api_meter.count("gemini", 3, "documents", calls=60)
    embed_budget.charge("u1", 60)
    day = embed_budget.site_day()
    assert day["left"] == 40 and day["total"] == 100               # three keys' remainders are gone
    with pytest.raises(embed_budget.EmbedBudgetExceeded, match="kept for answering questions"):
        embed_budget.check("u2", 21)                               # 60 + 21 > 80% of 100
    assert real_embed_query("still answered on key 4 c0de " + uuid.uuid4().hex) == [3.0] * 3


def test_keys_refused_well_under_their_count_are_flagged_as_sharing_a_project(caplog):
    caplog.set_level(logging.INFO)
    gemini.pool.mark(1, Exception(PER_DAY))
    gemini.pool.mark(1, Exception(PER_DAY))
    assert gemini.pool.shared() == {1: 0}
    assert sum("gemini_key_shares_quota" in r.getMessage() for r in caplog.records) == 1   # logged once


def test_a_set_site_total_is_kept_whatever_the_number_of_keys(monkeypatch):
    monkeypatch.setattr(settings, "embed_site_per_day", 1500)
    assert embed_budget.site_day()["total"] == 1500
    monkeypatch.setattr(settings, "gemini_api_key_4", "")
    assert embed_budget.site_day()["total"] == 1500


# ---------------------------------------------------------------- clients and key text
def test_every_key_gets_the_same_client_apart_from_its_key(monkeypatch):
    import langchain_google_genai
    built = []
    monkeypatch.setattr(langchain_google_genai, "GoogleGenerativeAIEmbeddings", lambda **kw: built.append(kw))
    gemini.embeddings.cache_clear()
    try:
        for k in range(4):
            gemini.embeddings(k)
    finally:
        gemini.embeddings.cache_clear()
    assert [b.pop("google_api_key") for b in built] == KEYS
    assert all(b == built[0] for b in built) and built[0]["model"] == settings.embedding_model


def test_nothing_else_builds_a_gemini_client():
    backend = Path(__file__).resolve().parents[2]
    offenders = [str(p.relative_to(backend)) for p in backend.rglob("*.py")
                 if "tests" not in p.parts and re.search(r"\bGoogleGenerativeAIEmbeddings\(", p.read_text(encoding="utf-8"))]
    assert offenders == [str(Path("config/gemini.py"))]


def test_key_text_in_an_error_reaches_no_log_event_or_message(clients, monkeypatch, caplog):
    errors, _ = clients
    errors[0] = [f"400 Bad request to https://example.test/embed?key={KEYS[0]}"]
    monkeypatch.setattr(ingestion, "embed_paced", real_embed_paced)
    caplog.set_level(logging.DEBUG)
    doc, _ = library.add_file("leak.txt", ("A sentence about embeddings and keys. " * 20).encode(), owner="u7")
    doc = library.process(doc["id"])
    assert doc["status"] == "failed" and "[key]" in doc["error"] and KEYS[0] not in doc["error"]
    assert not [r for r in caplog.records if KEYS[0] in r.getMessage() or KEYS[0] in (r.exc_text or "")]


def test_the_log_filter_scrubs_messages_arguments_and_tracebacks():
    try:
        raise RuntimeError(f"boom {KEYS[2]}")
    except RuntimeError:
        import sys
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed with %s", (KEYS[1],), sys.exc_info())
    redact.KeyScrubber().filter(record)
    line = logging.Formatter().format(record)
    assert not any(k in line for k in KEYS) and line.count("[key]") == 2


def test_a_key_on_the_hour_back_off_isnt_waited_for_under_the_lock(clients, monkeypatch):
    errors, sent = clients
    for k in (0, 1, 2):
        gemini.pool.mark(k, Exception(PER_DAY))
    for _ in range(gemini.STRIKES):
        gemini.pool.mark(3, Exception(UNEXPLAINED), now=datetime.now(timezone.utc) - timedelta(minutes=5))
    gemini.pool._until[3] = datetime.now(timezone.utc).timestamp() + gemini.STRIKE_WAIT
    monkeypatch.setattr(ingestion.time, "sleep", lambda s: pytest.fail(f"waited {s:.0f} s"))
    with pytest.raises(gemini.AllKeysLimited) as e:
        real_embed_paced(["a"], "T")
    assert not e.value.for_day and e.value.wait > ingestion.MAX_WAIT and not sent


REJECTED = "403 PERMISSION_DENIED. API key not valid. Please pass a valid API key. reason: API_KEY_INVALID"


def test_a_mistyped_fallback_key_is_skipped_and_the_next_one_used(clients, caplog):
    errors, sent = clients
    caplog.set_level(logging.INFO)
    errors[0] = [PER_MINUTE]
    errors[1] = [REJECTED, REJECTED]
    assert real_embed_paced(["a"], "T") == [[2.0] * 3]               # key 3 answered
    assert gemini.pool.rejected() == {1} and 1 in gemini.pool.cooldowns()
    gemini.pool._until.clear()                                      # an hour later: both keys tried again
    errors[0] = [PER_MINUTE]
    real_embed_paced(["b"], "T")                                     # key 2 rejected again, not logged again
    assert sum("gemini_key_rejected" in r.getMessage() for r in caplog.records) == 1   # logged once per key


def test_every_key_rejected_is_an_error_not_a_rate_limit(clients):
    errors, _ = clients
    for k in range(4):
        errors[k] = [REJECTED]
    with pytest.raises(gemini.GeminiError) as e:
        real_embed_query("rejected everywhere " + uuid.uuid4().hex)
    assert not isinstance(e.value, gemini.AllKeysLimited) and "rejected every Gemini key" in str(e.value)
