"""The two log files, and queued changes that run in code (no model)."""
import logging

import pytest

from backend.library import batch, catalog, config, library, logs
from test_agent import turn
from test_editing import stored


class NoModel:
    def invoke(self, messages):
        raise AssertionError("a structured change must not call the model")


@pytest.fixture
def log_files():
    logs._done = False
    root = logging.getLogger()
    before = list(root.handlers)
    logs.setup()
    added = [h for h in root.handlers if h not in before]
    yield config.DATA_DIR / "logs"
    for h in added:
        h.flush()
        h.close()
        root.removeHandler(h)
    logs._done = False


def read(folder, name):
    for h in logging.getLogger().handlers:
        h.flush()
    return (folder / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- logs
def test_major_events_go_to_both_logs_and_details_only_to_all(library_docs, log_files):
    svm = library_docs["svm.txt"]["id"]
    turn("l1", [("find_documents", {"description": "svm.txt"})],
         [("rename_document", {"doc_id": svm, "new_title": "SVM Primer"})])
    logging.getLogger("backend.library.something").error("boom")
    events, everything = read(log_files, "events.log"), read(log_files, "all.log")
    assert 'rename doc="' in events and '"SVM Primer"' in events
    assert "chat session=" in events and "chat_done" in events and "boom" in events
    assert "tool find_documents args=" not in events               # tool calls are detail, not major
    assert "tool find_documents args=" in everything and "tool rename_document" in everything
    assert 'rename doc="' in everything and "boom" in everything     # all.log has everything


def test_indexing_edits_and_deletes_are_logged(tmp_path, log_files):
    path = tmp_path / "note.txt"
    path.write_text("Quarterly Notes\nThe budget is approved for the year.", encoding="utf-8")
    doc, _ = library.ingest_path(path)
    from backend.library import editing
    editing.edit(doc["id"], "replace", find="approved", text="rejected")
    editing.undo(doc["id"])
    library.delete(doc["id"])
    events = read(log_files, "events.log")
    for name in ("upload doc=", "indexed doc=", "edit doc=", "undo doc=", "deleted doc="):
        assert name in events, name


# ---------------------------------------------------------------- structured queue
def queue(changes, session="sq"):
    return turn(session, [("queue_tasks", {"changes": changes})])


def test_no_op_and_duplicate_renames_are_rejected_before_queueing(library_docs):
    _, results = queue([
        {"document": "Support Vector Machines", "action": "rename", "new_title": "support  vector machines"},
        {"document": "GloVe Word Embeddings", "action": "rename", "new_title": "Embeddings"},
        {"document": "Word2vec Neural Word Embeddings", "action": "rename", "new_title": "embeddings"},
    ])
    assert results[-1]["status"] == "error" and len(results[-1]["problems"]) == 2
    assert catalog.list_batches() == []                              # nothing was queued


def test_structured_changes_run_in_code_without_the_model(library_docs):
    _, results = queue([
        {"document": "Support Vector Machines", "action": "rename", "new_title": "SVM Primer"},
        {"document": "GloVe Word Embeddings", "action": "replace", "find": "co-occurrence counts", "text": "pair counts"},
        {"document": "EU General Data Protection Regulation", "action": "append", "text": "Reviewed in 2026."},
    ])
    assert results[-1]["status"] == "queued" and results[-1]["tasks"] == 3
    assert batch.run_pending(model=NoModel()) == 3
    tasks = catalog.list_batches()[0]["tasks"]
    assert [t["status"] for t in tasks] == ["done"] * 3 and all(t["tokens"] == 0 for t in tasks)
    assert catalog.get_document(library_docs["svm.txt"]["id"])["title"] == "SVM Primer"
    assert "pair counts" in stored(library_docs["glove.txt"])
    assert "Reviewed in 2026." in stored(library_docs["gdpr_final.txt"])
    assert "Before:" in tasks[1]["reply"]


def test_changes_that_cannot_be_done_are_reported_not_guessed(library_docs):
    queue([
        {"document": "Support Vector Machines", "action": "rename", "new_title": "GloVe Word Embeddings"},   # taken
        {"document": "word embeddings", "action": "append", "text": "x"},                                  # two fit
        {"document": "Support Vector Machines", "action": "replace", "find": "no such text", "text": "y"},
    ])
    batch.run_pending(model=NoModel())
    tasks = catalog.list_batches()[0]["tasks"]
    assert [t["status"] for t in tasks] == ["not_done"] * 3
    assert "already has the name" in tasks[0]["reply"]
    assert "Could not tell which document" in tasks[1]["reply"]
    assert "isn't in the document" in tasks[2]["reply"]
    assert catalog.get_document(library_docs["svm.txt"]["id"])["title"] == "Support Vector Machines"


def test_queue_status_reports_the_real_state(library_docs):
    queue([{"document": "Support Vector Machines", "action": "rename", "new_title": "SVM Primer"},
           {"document": "Nope Nothing", "action": "rename", "new_title": "Whatever"}])
    batch.run_pending(model=NoModel())
    _, results = turn("sq2", [("queue_status", {})])
    summary = results[-1]["batches"][0]
    assert summary["total"] == 2 and summary["counts"] == {"done": 1, "not_done": 1}
    assert summary["needs_attention"][0]["change"].startswith('Rename "Nope Nothing"')


def test_undo_is_a_queued_change_too(library_docs):
    from backend.library import editing
    svm, glove = library_docs["svm.txt"], library_docs["glove.txt"]
    editing.edit(svm["id"], "replace", find="classifier", text="gadget")
    editing.edit(glove["id"], "append", text="An extra line.")
    _, results = queue([{"document": "Support Vector Machines", "action": "undo"},
                        {"document": "GloVe Word Embeddings", "action": "undo"},
                        {"document": "Word2vec Neural Word Embeddings", "action": "undo"}])     # never edited
    assert results[-1]["status"] == "queued"
    assert batch.run_pending(model=NoModel()) == 3
    tasks = catalog.list_batches()[0]["tasks"]
    assert [t["status"] for t in tasks] == ["done", "done", "not_done"]
    assert "nothing to undo" in tasks[2]["reply"]
    assert "classifier" in stored(svm) and "An extra line." not in stored(glove)
    assert tasks[0]["instruction"] == 'Undo the last edit to "Support Vector Machines"'


# ---------------------------------------------------------------- the catalog's queries
def test_rename_histories_in_one_query_match_the_per_document_ones(library_docs):
    svm = library_docs["svm.txt"]["id"]
    library.rename(svm, "SVM Primer")
    library.rename(svm, "SVM Guide")
    all_of_them = catalog.rename_histories()
    for doc in library_docs.values():
        assert all_of_them.get(doc["id"], []) == catalog.rename_history(doc["id"])
    assert [h["new_title"] for h in all_of_them[svm]][-2:] == ["SVM Primer", "SVM Guide"]
    assert library.find("Support Vector Machines")["candidates"][0]["doc_id"] == svm    # found by its old name


def test_the_queue_panel_gets_only_active_and_recent_batches(library_docs):
    old_done = catalog.add_batch("s", ["a"])
    old_waiting = catalog.add_batch("s", ["b"])
    recent = catalog.add_batch("s", ["c"])
    catalog.update_task(old_done["tasks"][0]["id"], status="done")
    catalog.update_task(recent["tasks"][0]["id"], status="done")
    with catalog.tx() as c:                                   # two of them were queued two hours ago
        c.execute("UPDATE batches SET created_at = '2000-01-01T00:00:00+00:00' WHERE id IN (?, ?)",
                  (old_done["id"], old_waiting["id"]))
    shown = {b["id"] for b in batch.status()["batches"]}
    assert shown == {old_waiting["id"], recent["id"]}         # old but still waiting: shown; old and done: not


def test_the_searched_columns_are_indexed():
    with catalog.tx() as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"renames_doc", "edits_doc", "tasks_status", "tasks_batch"} <= names
