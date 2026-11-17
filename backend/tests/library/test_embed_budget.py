"""The daily embedding allowance (backend/library/embed_budget.py): per account and, for indexing, per site; checked
on the real number before anything is embedded, refused whole, only successes charged, kept across restarts."""
from datetime import datetime, timezone

import pytest

from backend.config.settings import settings
from backend.library import catalog, config, editing, embed_budget, library


def test_the_day_is_googles_pacific_day():
    utc = lambda *a: datetime(*a, tzinfo=timezone.utc)   # noqa: E731
    assert embed_budget.provider_day(utc(2026, 11, 17, 7, 59)) == "2026-11-16"   # PST: 23:59
    assert embed_budget.provider_day(utc(2026, 11, 17, 8, 0)) == "2026-11-17"    # PST: midnight
    assert embed_budget.provider_day(utc(2026, 7, 1, 6, 59)) == "2026-06-30"     # PDT: 23:59
    assert embed_budget.provider_day(utc(2026, 7, 1, 7, 0)) == "2026-07-01"
    # the switches: 2 a.m. on 8 March 2026 (PST) and 1 November 2026 (PDT)
    assert embed_budget.provider_day(utc(2026, 3, 8, 9, 59)) == "2026-03-08"     # 01:59 PST
    assert embed_budget.provider_day(utc(2026, 11, 2, 7, 30)) == "2026-11-01"    # 23:30 PST after the end


def _text(doc):
    return (config.LIBRARY_DIR / catalog.get_document(doc["id"])["file_name"]).read_text(encoding="utf-8")


def test_an_account_over_its_day_is_refused_with_the_numbers(monkeypatch):
    monkeypatch.setattr(settings, "embed_per_user_day", 10)
    embed_budget.charge("u1", 8)
    with pytest.raises(embed_budget.EmbedBudgetExceeded) as e:
        embed_budget.check("u1", 3)
    assert "needs 3 passages" in str(e.value) and "2 of 10 left" in str(e.value)
    embed_budget.check("u1", 2)          # what fits goes
    embed_budget.check("u2", 10)         # another account has its own


def test_indexing_stops_at_the_sites_share(monkeypatch):
    monkeypatch.setattr(settings, "embed_per_user_day", 0)
    monkeypatch.setattr(settings, "embed_site_per_day", 100)
    monkeypatch.setattr(settings, "embed_index_share", 0.8)
    embed_budget.charge("u1", 50)
    embed_budget.charge("u2", 25)
    with pytest.raises(embed_budget.EmbedBudgetExceeded, match="kept for answering questions"):
        embed_budget.check("u3", 6)      # 75 + 6 > 80
    embed_budget.check("u3", 5)


def test_the_counts_survive_a_restart():
    embed_budget.charge("u1", 7)
    catalog.reset_connection()           # what a new process does: a fresh connection to the same file
    assert embed_budget.used("u1") == 7 and embed_budget.used() == 7


def test_an_upload_over_the_allowance_fails_with_the_message(monkeypatch):
    monkeypatch.setattr(settings, "embed_per_user_day", 1)
    doc, _ = library.add_file("long.txt", ("A sentence about embeddings. " * 400).encode(), owner="u9")
    doc = library.process(doc["id"])
    assert doc["status"] == "failed" and "passages indexed" in doc["error"]
    assert embed_budget.used("u9") == 0  # nothing embedded, nothing charged


def test_an_edit_over_the_allowance_leaves_the_file_and_index_as_they_were(library_docs, monkeypatch):
    doc = library_docs["svm.txt"]
    before, chunks = _text(doc), catalog.get_document(doc["id"])["chunks"]
    monkeypatch.setattr(settings, "embed_per_user_day", 1)
    embed_budget.charge(doc["owner"], 1)                           # today's allowance used up
    with pytest.raises(editing.EditError, match="passages indexed"):
        editing.edit(doc["id"], "replace", find="maximum margin hyperplane", text="widest separating boundary")
    assert _text(doc) == before and catalog.get_document(doc["id"])["chunks"] == chunks
    assert catalog.last_edit(doc["id"]) is None


def test_an_edit_is_charged_only_what_it_embedded(library_docs):
    doc = library_docs["svm.txt"]
    before = embed_budget.used(doc["owner"])     # (the fixture's uploads were charged too)
    editing.edit(doc["id"], "replace", find="maximum margin hyperplane", text="widest separating boundary")
    assert embed_budget.used(doc["owner"]) - before == 1   # the one changed passage; the rest reused their vectors


def test_searching_isnt_refused_when_indexing_is(library_docs, monkeypatch):
    monkeypatch.setattr(settings, "embed_site_per_day", 1)
    embed_budget.charge("someone", 1)            # indexing is closed for the day
    found = library.find("support vector machine classifier")
    assert found["candidates"]                   # questions still search
