"""Edge cases found by probing, each pinned down."""
import json
import shutil
import subprocess

import pytest
import tiktoken

from renamer import agent, batch, catalog, config, library
from tests.test_agent import turn
from tests.test_editing import stored

WEB = config.PROJECT_ROOT / "web"


# ---------------------------------------------------------------- a big library can still be listed
def test_the_document_list_is_compact_and_capped(library_docs, monkeypatch):
    out = agent.run_tool("list_documents", {}, agent.Guard({}, 1), [])
    per_doc = len(tiktoken.get_encoding("o200k_base").encode(json.dumps(out))) / len(out["documents"])
    assert per_doc < 30                                   # was 76: about 100 documents filled Groq's whole minute
    assert set(out["documents"][0]) == {"doc_id", "title"} and "next" not in out

    monkeypatch.setattr(config, "LIST_MAX_DOCUMENTS", 2)
    guard = agent.Guard({}, 1)
    out = agent.run_tool("list_documents", {}, guard, [])
    newest = [d["id"] for d in reversed(catalog.list_documents())][:2]
    assert out["total"] == 4 and [d["doc_id"] for d in out["documents"]] == newest
    assert "library panel" in out["next"] and set(guard.listed) == set(newest)   # only what was shown counts as seen


# ---------------------------------------------------------------- previews of Markdown documents
def test_a_markdown_documents_own_asterisks_are_escaped_in_the_preview(library_docs, tmp_path):
    path = tmp_path / "facts.md"
    path.write_text("# Facts\n\nThe **key** point is that cases rose by 20 percent this year.", encoding="utf-8")
    doc, _ = library.ingest_path(path)
    out, _ = turn("md1", [("find_documents", {"description": "facts.md"})],
                  [("edit_document", {"doc_id": doc["id"], "operation": "replace", "find": "cases rose", "text": "cases fell"})])
    before = out["reply"].splitlines()[1]
    assert "key\\*\\* point" in before and "**cases rose**" in before
    assert before.replace("\\*", "").count("**") == 2     # only the reply's own bold is left unescaped


def test_titles_with_asterisks_are_escaped_too():
    reply = agent.compose_reply([{"status": "renamed", "old_title": "A*B", "new_title": "C"}])
    assert reply == 'Renamed "A\\*B" to "C".'


# ---------------------------------------------------------------- the page's Markdown renderer
@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_the_chat_markdown_renderer():
    cases = {
        "Before: …key\\*\\* point is that **cases rose** by …":
            "<p>Before: …key** point is that <strong>cases rose</strong> by …</p>",
        "The area is 2 * 3 * 4 = 24.": "<p>The area is 2 * 3 * 4 = 24.</p>",
        "This is *really* important.": "<p>This is <em>really</em> important.</p>",
        "a ** b ** c": "<p>a ** b ** c</p>",
        "<img src=x onerror=alert(1)> **x**": "<p>&lt;img src=x onerror=alert(1)&gt; <strong>x</strong></p>",
        "1. one\n2. two": "<ol><li>one</li><li>two</li></ol>",
    }
    url = (WEB / "js" / "lib" / "markdown.js").as_uri()
    script = (f"const m = await import({json.dumps(url)});"
              f"const cases = {json.dumps(list(cases))};"
              "console.log(JSON.stringify({html: cases.map(m.markdown), "
              "plain: m.plainText('Before: …key\\\\*\\\\* point **cases rose** …')}));")
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True)
    result = json.loads(out.stdout)
    assert result["html"] == list(cases.values())
    assert result["plain"] == "Before: …key** point cases rose …"     # the queue panel shows this, no markup


# ---------------------------------------------------------------- a queued change interrupted by a restart
def interrupt(task_id: int, change: dict, apply: bool) -> None:
    """Run a queued change as the worker does, but stop as a server stop would: after the checkpoint, with the
    change applied or not, before the task is marked finished."""
    def checkpoint(doc):
        catalog.update_task(task_id, status="running",
                            checkpoint={"doc_id": doc["id"], "sha256": doc["sha256"], "title": doc["title"]})
        if not apply:
            raise KeyboardInterrupt
    try:
        batch.run_change(change, before_applying=checkpoint)
    except KeyboardInterrupt:
        pass


def test_an_append_that_finished_before_a_restart_is_not_appended_again(library_docs):
    change = {"action": "append", "document": "Support Vector Machines", "text": "Reviewed in 2026."}
    task = catalog.add_batch("s", [("Append", change)])["tasks"][0]
    interrupt(task["id"], change, apply=True)
    batch.recover_interrupted()
    after = catalog.get_task(task["id"])
    assert after["status"] == "done" and "not run again" in after["reply"]
    assert stored(library_docs["svm.txt"]).count("Reviewed in 2026.") == 1


def test_a_change_stopped_before_it_applied_runs_again(library_docs):
    change = {"action": "append", "document": "Support Vector Machines", "text": "Reviewed in 2026."}
    task = catalog.add_batch("s", [("Append", change)])["tasks"][0]
    interrupt(task["id"], change, apply=False)
    batch.recover_interrupted()
    assert catalog.get_task(task["id"])["status"] == "queued"
    batch.run_pending(model=None)
    assert catalog.get_task(task["id"])["status"] == "done"
    assert stored(library_docs["svm.txt"]).count("Reviewed in 2026.") == 1


def test_a_free_text_task_without_a_checkpoint_is_queued_again(library_docs):
    task = catalog.add_batch("s", ["Append a note to svm.txt"])["tasks"][0]
    catalog.update_task(task["id"], status="running")
    batch.recover_interrupted()
    assert catalog.get_task(task["id"])["status"] == "queued"


# ---------------------------------------------------------------- 5. swapping titles in one batch
def test_two_documents_can_swap_titles_in_one_batch(library_docs):
    from tests.test_logs_and_queue import NoModel, queue
    svm, glove = library_docs["svm.txt"]["id"], library_docs["glove.txt"]["id"]
    _, results = queue([
        {"document": "Support Vector Machines", "action": "rename", "new_title": "GloVe Word Embeddings"},
        {"document": "GloVe Word Embeddings", "action": "rename", "new_title": "Support Vector Machines"}])
    assert results[-1]["status"] == "queued"
    batch.run_pending(model=NoModel())
    assert [t["status"] for t in catalog.list_batches()[0]["tasks"]] == ["done", "done"]
    assert catalog.get_document(svm)["title"] == "GloVe Word Embeddings"
    assert catalog.get_document(glove)["title"] == "Support Vector Machines"


def test_a_batch_rename_still_asks_when_the_name_is_kept(library_docs):
    """Taking a name is allowed only when its holder gives it up later in the same batch."""
    from tests.test_logs_and_queue import NoModel, queue
    queue([{"document": "Support Vector Machines", "action": "rename", "new_title": "GloVe Word Embeddings"}])
    batch.run_pending(model=NoModel())
    task = catalog.list_batches()[0]["tasks"][0]
    assert task["status"] == "not_done" and "already has the name" in task["reply"]


def test_later_changes_find_their_document_after_an_earlier_rename(library_docs):
    from tests.test_logs_and_queue import NoModel, queue
    queue([{"document": "Support Vector Machines", "action": "rename", "new_title": "SVM Primer"},
           {"document": "Support Vector Machines", "action": "append", "text": "Reviewed."}])
    batch.run_pending(model=NoModel())
    assert [t["status"] for t in catalog.list_batches()[0]["tasks"]] == ["done", "done"]
    assert stored(library_docs["svm.txt"]).rstrip().endswith("Reviewed.")


# ---------------------------------------------------------------- 6. Word text across paragraphs
def test_word_text_across_two_paragraphs_gets_a_clear_message(tmp_path):
    import docx
    from renamer import editing
    path = tmp_path / "memo.docx"
    d = docx.Document()
    d.add_paragraph("The launch is planned")
    d.add_paragraph("for March in Kathmandu.")
    d.save(str(path))
    doc, _ = library.ingest_path(path)
    with pytest.raises(editing.EditError, match="runs across more than one paragraph"):
        editing.edit(doc["id"], "replace", find="planned for March", text="planned for June")
    with pytest.raises(editing.EditError, match="isn't in the document"):
        editing.edit(doc["id"], "replace", find="planned for April", text="x")


# ---------------------------------------------------------------- 7. odd titles
def test_odd_titles_get_clear_messages():
    from renamer import naming
    with pytest.raises(naming.TitleError, match="at least one letter or number"):
        naming.clean_title("📄📄")
    with pytest.raises(naming.TitleError, match="only a file extension"):
        naming.clean_title(".txt")
    with pytest.raises(naming.TitleError, match="empty"):
        naming.clean_title("  ")
    assert naming.file_name_for(".hidden notes", ".md", set()) == "hidden notes.md"


# ---------------------------------------------------------------- 8. file names never collide
def test_a_file_on_disk_is_never_overwritten(library_docs):
    stray = config.LIBRARY_DIR / "SVM Primer.txt"
    stray.write_text("not in the catalog", encoding="utf-8")
    result = library.rename(library_docs["svm.txt"]["id"], "SVM Primer")
    assert result["file_name"] == "SVM Primer (2).txt"
    assert stray.read_text(encoding="utf-8") == "not in the catalog"
    stray.unlink()


def test_renaming_to_its_own_file_name_keeps_it(library_docs):
    doc = library_docs["svm.txt"]
    library.rename(doc["id"], "SVM Primer")
    assert library.rename(doc["id"], "svm primer")["file_name"] == "svm primer.txt"   # not "(2)": it is its own file


# ---------------------------------------------------------------- 9. re-uploading an edited document's original
def test_the_original_of_an_edited_document_is_a_duplicate(library_docs, tmp_path):
    from renamer import editing
    doc = library_docs["svm.txt"]
    original = (config.LIBRARY_DIR / doc["file_name"]).read_bytes()
    editing.edit(doc["id"], "append", text="An extra line.")
    again, duplicate = library.add_file("svm.txt", original)
    assert duplicate and again["id"] == doc["id"]
    assert len(catalog.list_documents()) == 4


def test_the_upload_endpoint_says_edited_since(library_docs):
    from fastapi.testclient import TestClient
    from renamer import api, editing
    doc = library_docs["svm.txt"]
    original = (config.LIBRARY_DIR / doc["file_name"]).read_bytes()
    client = TestClient(api.app)
    same = client.post("/api/documents/upload", files={"files": ("svm.txt", original)}).json()[0]
    assert same["status"] == "duplicate" and same["edited_since"] is False
    editing.edit(doc["id"], "append", text="An extra line.")
    later = client.post("/api/documents/upload", files={"files": ("svm.txt", original)}).json()[0]
    assert later["status"] == "duplicate" and later["edited_since"] is True


# ---------------------------------------------------------------- 10. a free-text queue task does all it says
def test_a_free_text_task_does_every_change_it_names(library_docs, monkeypatch):
    from tests.test_optimizations import Checked
    from tests.test_undo_and_batch import NeverStopped
    monkeypatch.setattr(batch, "_stop", NeverStopped())
    svm, glove = library_docs["svm.txt"]["id"], library_docs["glove.txt"]["id"]
    model = Checked([("find_documents", {"description": "svm.txt"}), ("find_documents", {"description": "glove.txt"})],
                    [("rename_document", {"doc_id": svm, "new_title": "SVM Primer"})],
                    [("rename_document", {"doc_id": glove, "new_title": "GloVe Primer"})],
                    left=[True, False])
    created = catalog.add_batch("s", ["Rename svm.txt to SVM Primer and glove.txt to GloVe Primer"])
    batch.run_pending(sleep=lambda s: None, model=model)
    task = catalog.get_batch(created["id"])["tasks"][0]
    assert task["status"] == "done" and [a["new_title"] for a in task["actions"]] == ["SVM Primer", "GloVe Primer"]
    assert model.asked == [["renamed"], ["renamed", "renamed"]]      # the check ran through the pacing wrapper


def test_the_pacing_wrapper_hides_nothing_the_model_lacks():
    paced = batch.PacedModel(object())
    assert not hasattr(paced, "anything_left") and not hasattr(paced, "retry_on_main")
