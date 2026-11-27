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


def test_an_undo_isnt_held_to_the_accounts_cap_only_to_the_sites_day(library_docs, monkeypatch):
    doc = library_docs["svm.txt"]
    before = _text(doc)
    editing.edit(doc["id"], "replace", find="maximum margin hyperplane", text="widest separating boundary")
    monkeypatch.setattr(settings, "embed_per_user_day", 1)
    monkeypatch.setattr(settings, "embed_site_per_day", 1000)
    embed_budget.charge(doc["owner"], 1)       # the account's day is used up: no more edits...
    with pytest.raises(editing.EditError):
        editing.edit(doc["id"], "replace", find="Kernels map", text="Kernel functions map")
    editing.undo(doc["id"])                    # ...but the way back still works
    assert _text(doc) == before


def test_the_sites_whole_day_stops_an_undo_too(library_docs, monkeypatch):
    doc = library_docs["svm.txt"]
    editing.edit(doc["id"], "replace", find="maximum margin hyperplane", text="widest separating boundary")
    monkeypatch.setattr(settings, "embed_site_per_day", 1)
    embed_budget.charge("someone", 1)
    with pytest.raises(editing.EditError, match="quota for today is used up"):
        editing.undo(doc["id"])


def test_a_refusal_reads_as_the_limit_not_a_failure(library_docs, monkeypatch):
    doc = library_docs["svm.txt"]
    monkeypatch.setattr(settings, "embed_per_user_day", 1)
    embed_budget.charge(doc["owner"], 1)
    with pytest.raises(editing.EditError) as e:
        editing.edit(doc["id"], "replace", find="maximum margin hyperplane", text="widest separating boundary")
    assert str(e.value).startswith("This needs") and "failed" not in str(e.value)


def test_an_admin_has_no_per_account_cap_but_the_site_quota_still_applies(monkeypatch):
    monkeypatch.setattr(embed_budget.usage_limits, "is_unlimited", lambda uid: uid == "boss")
    monkeypatch.setattr(settings, "embed_per_user_day", 10)
    monkeypatch.setattr(settings, "embed_site_per_day", 100)
    monkeypatch.setattr(settings, "embed_index_share", 0.8)
    embed_budget.charge("boss", 50)
    embed_budget.check("boss", 20)               # over the 10 a day a normal account gets: passes
    assert embed_budget.allowance("boss") == 30  # (what the Document service is told: the site's share only)
    with pytest.raises(embed_budget.EmbedBudgetExceeded, match="kept for answering questions"):
        embed_budget.check("boss", 31)           # 50 + 31 > 80: the site's share still holds
    with pytest.raises(embed_budget.EmbedBudgetExceeded, match="your account"):
        embed_budget.check("plain", 11)          # a normal account is unchanged
