"""Bugs found in review and in the logs, each pinned down."""
import threading

import pytest
from langchain_core.messages import AIMessage

from renamer import agent, batch, catalog, config, editing, library
from tests.test_agent import turn
from tests.test_editing import stored
from tests.test_pdf_edit import pdf_doc  # noqa: F401  (fixture)


class SilentAfter:
    """Plays the steps, then answers with an empty message (the model sometimes does)."""

    def __init__(self, *steps):
        self.steps = list(steps)

    def invoke(self, messages):
        if not self.steps:
            return AIMessage("")
        calls = self.steps.pop(0)
        return AIMessage("", tool_calls=[{"name": n, "args": a, "id": f"c{i}{n}"} for i, (n, a) in enumerate(calls)])


def test_empty_reply_after_an_edit_and_a_queue_does_not_crash(library_docs):
    svm = library_docs["svm.txt"]["id"]
    out = agent.chat("f1", "x", model=SilentAfter(
        [("find_documents", {"description": "svm.txt"}), ("queue_tasks", {"tasks": ["a"]})],
        [("edit_document", {"doc_id": svm, "operation": "append", "text": "More."}), ("queue_status", {})]))
    assert out["reply"].startswith("Done: ") and 'edited "' in out["reply"] and "queued 1 change" in out["reply"]


def test_pdf_agreement_counts_once(pdf_doc):  # noqa: F811
    call = {"doc_id": pdf_doc["id"], "operation": "replace", "find": "March", "text": "June"}
    turn("f2", [("find_documents", {"description": "budget.pdf"})], [("edit_document", call)])
    _, results = turn("f2", [("edit_document", {**call, "convert_pdf": True})])
    assert results[-1]["converted"]
    editing.undo(pdf_doc["id"])                               # a PDF again, same chat
    _, results = turn("f2", [("edit_document", {**call, "find": "Costs", "text": "Spend", "convert_pdf": True})])
    assert results[-1]["status"] == "needs_confirmation"      # the old "yes" doesn't cover a new conversion


def test_asking_about_a_pdf_keeps_a_pending_rename(library_docs):
    guard = agent.Guard({}, 1)
    guard.ask_confirmation("d", "New Name", "name_conflict")
    guard.ask_confirmation("d", agent.PDF_CONVERSION, "pdf_conversion")
    guard.turn = 2
    assert guard.confirmed_earlier("d", "New Name") and guard.confirmed_earlier("d", agent.PDF_CONVERSION)


def test_details_show_the_exact_passage_to_quote(library_docs, pdf_doc):  # noqa: F811
    svm = library_docs["svm.txt"]
    turn("f3", [("find_documents", {"description": "svm.txt"})])
    _, results = turn("f3", [("document_details", {"doc_id": svm["id"], "passage": "maximum MARGIN"})])
    assert "maximum margin" in results[-1]["passage"] and len(results[-1]["passage"]) <= 1100
    _, results = turn("f3", [("document_details", {"doc_id": svm["id"], "passage": "zebra crossing"})])
    assert results[-1]["passage"] == "Those words aren't in the document."
    assert "Kathmandu" in editing.passage(pdf_doc, "launch is planned")     # PDFs too, without converting


def test_replacing_text_with_itself_is_refused(library_docs):
    doc = library_docs["svm.txt"]
    with pytest.raises(editing.EditError, match="same as the old"):
        editing.edit(doc["id"], "replace", find="maximum margin", text="maximum  margin")
    assert not (config.DATA_DIR / "versions" / doc["id"]).exists()


def test_backup_names_never_collide_with_a_stray_file(library_docs):
    doc = library_docs["svm.txt"]
    editing.edit(doc["id"], "append", text="One.")
    editing.edit(doc["id"], "append", text="Two.")
    folder = config.DATA_DIR / "versions" / doc["id"]
    (folder / "001.txt").unlink()                               # two files become one, then a stray file
    (folder / "notes.tmp").write_text("x")
    editing.edit(doc["id"], "append", text="Three.")
    assert sorted(p.name for p in folder.iterdir()) == ["002.txt", "003.txt", "notes.tmp"]


def test_a_rename_waits_for_an_edit_of_the_same_library(library_docs, monkeypatch):
    """Edits and renames come from the chat and the queue worker at once: they take turns."""
    doc = library_docs["svm.txt"]
    started, release, order = threading.Event(), threading.Event(), []
    real = editing._reindex

    def slow_reindex(d):
        started.set()
        release.wait(5)
        order.append("edit")
        return real(d)
    monkeypatch.setattr(editing, "_reindex", slow_reindex)
    t = threading.Thread(target=editing.edit, args=(doc["id"], "append"), kwargs={"text": "Later."})
    t.start()
    started.wait(5)
    r = threading.Thread(target=lambda: (library.rename(doc["id"], "SVM Primer"), order.append("rename")))
    r.start()
    r.join(0.3)
    assert r.is_alive()                                         # blocked while the edit runs
    release.set()
    t.join(10)
    r.join(10)
    assert order == ["edit", "rename"] and "Later." in stored(doc)


def test_the_queue_worker_survives_an_unexpected_error(library_docs, monkeypatch):
    created = catalog.add_batch("s", ["a", "b"])
    calls = []

    def run_task(task, *a, **k):
        calls.append(task["id"])
        if len(calls) == 1:
            raise RuntimeError("disk on fire")
        catalog.update_task(task["id"], status="done")
        batch._stop.set()                                       # end the loop after the second task
    monkeypatch.setattr(batch, "run_task", run_task)
    monkeypatch.setattr(batch, "_stop", threading.Event())
    batch._loop()
    tasks = catalog.get_batch(created["id"])["tasks"]
    assert [t["status"] for t in tasks] == ["failed", "done"] and "disk on fire" in tasks[0]["reply"]
