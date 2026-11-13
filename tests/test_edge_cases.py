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
