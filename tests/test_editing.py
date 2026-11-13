"""Editing document text: file, index, catalog, backup, and the agent's guard around it."""
import docx
import pytest

from renamer import agent, catalog, config, editing, library
from renamer.day7 import vectorstore
from tests.test_agent import turn


def stored(doc):
    return (config.LIBRARY_DIR / catalog.get_document(doc["id"])["file_name"]).read_text(encoding="utf-8")


def chunk_texts(doc_id):
    client = vectorstore.get_client()
    points, _ = client.scroll(library.day7_settings.collection_name,
                              scroll_filter=vectorstore.user_filter(config.OWNER, doc_id), limit=100)
    return " ".join(p.payload["page_content"] for p in points)


def test_replace_updates_file_index_catalog_and_keeps_a_backup(library_docs):
    doc = library_docs["svm.txt"]
    out = editing.edit(doc["id"], "replace", find="maximum margin", text="widest possible margin")
    assert out["status"] == "edited" and out["changes"] == 1
    assert "widest possible margin" in stored(doc) and "maximum margin" not in stored(doc)
    assert "widest possible margin" in chunk_texts(doc["id"])
    assert "maximum margin" not in chunk_texts(doc["id"])
    assert catalog.get_document(doc["id"])["sha256"] != doc["sha256"]
    backup = next((config.DATA_DIR / "versions" / doc["id"]).iterdir())
    assert "maximum margin" in backup.read_text(encoding="utf-8")
    assert catalog.edit_history(doc["id"])[0]["operation"] == "replace"


def test_replace_ignores_line_breaks_and_case(library_docs):
    doc = library_docs["svm.txt"]
    editing.edit(doc["id"], "replace", find="SUPPORT  vector   machine is", text="An SVM is")
    assert "An SVM is a classifier" in stored(doc)


def test_several_matches_change_nothing_until_told(library_docs):
    doc = library_docs["w2v.txt"]
    out = editing.edit(doc["id"], "replace", find="word", text="term")
    assert out["status"] == "ambiguous" and out["matches"] > 1
    assert "word" in stored(doc) and not (config.DATA_DIR / "versions" / doc["id"]).exists()
    out = editing.edit(doc["id"], "replace", find="word", text="term", replace_all=True)
    assert out["changes"] == 3
    assert stored(doc).count("term") == 3 and " word " not in stored(doc)


def test_missing_text_and_unsupported_types_are_refused(library_docs):
    doc = library_docs["svm.txt"]
    with pytest.raises(editing.EditError, match="isn't in the document"):
        editing.edit(doc["id"], "replace", find="quantum entanglement", text="x")
    catalog.update_document(doc["id"], ext=".pptx")
    with pytest.raises(editing.EditError, match="can't be edited"):
        editing.edit(doc["id"], "append", text="x")


def test_append_grows_the_index_and_shrinking_drops_old_chunks(library_docs):
    doc = library_docs["svm.txt"]
    before = catalog.get_document(doc["id"])["chunks"]
    editing.edit(doc["id"], "append", text="Zebrafish are striped. " * 200)
    grown = catalog.get_document(doc["id"])["chunks"]
    assert grown > before and "Zebrafish" in chunk_texts(doc["id"])
    editing.edit(doc["id"], "replace", find="Zebrafish are striped. " * 200, text="", replace_all=True)
    assert catalog.get_document(doc["id"])["chunks"] == before
    assert vectorstore.count_user_vectors(config.OWNER) == sum(d["chunks"] for d in catalog.list_documents())


def test_failed_embedding_puts_the_file_back(library_docs, monkeypatch):
    doc = library_docs["svm.txt"]
    original = stored(doc)

    def boom(texts, title):
        raise RuntimeError("quota")
    monkeypatch.setattr(editing.ingestion, "embed_paced", boom)
    with pytest.raises(editing.EditError, match="unchanged"):
        editing.edit(doc["id"], "replace", find="classifier", text="thing")
    assert stored(doc) == original and "classifier" in chunk_texts(doc["id"])


def test_docx_replace_keeps_formatting_of_the_run(tmp_path):
    path = tmp_path / "memo.docx"
    d = docx.Document()
    d.add_paragraph().add_run("The launch is planned for March in Kathmandu.").bold = True
    d.save(str(path))
    doc, _ = library.ingest_path(path)
    assert doc["status"] == "indexed"
    editing.edit(doc["id"], "replace", find="March", text="June")
    saved = docx.Document(str(config.LIBRARY_DIR / catalog.get_document(doc["id"])["file_name"]))
    assert saved.paragraphs[0].text == "The launch is planned for June in Kathmandu."
    assert saved.paragraphs[0].runs[0].bold is True
    assert "June" in chunk_texts(doc["id"])


def test_agent_cannot_edit_an_unidentified_or_ambiguous_document(library_docs):
    svm, w2v = library_docs["svm.txt"]["id"], library_docs["w2v.txt"]["id"]
    _, results = turn("e1", [("edit_document", {"doc_id": svm, "operation": "append", "text": "x"})])
    assert results[-1]["status"] == "needs_clarification"
    _, results = turn("e2", [("find_documents", {"description": "word embeddings"})],
                      [("edit_document", {"doc_id": w2v, "operation": "append", "text": "x"})])
    assert results[-1]["status"] == "needs_clarification"
    assert "x" not in stored(library_docs["w2v.txt"])[-3:]


def test_agent_edit_reports_an_action(library_docs):
    svm = library_docs["svm.txt"]["id"]
    out, results = turn("e3", [("find_documents", {"description": "svm.txt"})],
                        [("edit_document", {"doc_id": svm, "operation": "replace", "find": "Kernels",
                                            "text": "Kernel functions"})])
    assert results[-1]["status"] == "edited"
    assert out["actions"][0]["type"] == "edit" and out["actions"][0]["changes"] == 1


def test_old_messages_are_cut_but_the_last_exchange_is_whole():
    from langchain_core.messages import AIMessage, HumanMessage
    long = "x" * 3000
    history = [HumanMessage("q1"), AIMessage(long), HumanMessage("q2"), AIMessage(long)]
    out = agent._shorten(history)
    assert len(out[1].content) <= config.OLDER_MAX_CHARS + 2 and len(out[3].content) == 3000
    assert len(history[1].content) == 3000   # the saved copy is untouched


def test_reply_shows_only_the_change_with_20_characters_around_it(library_docs):
    svm = library_docs["svm.txt"]["id"]
    out, _ = turn("p1", [("find_documents", {"description": "svm.txt"})],
                  [("edit_document", {"doc_id": svm, "operation": "replace", "find": "maximum margin",
                                      "text": "widest margin"})])
    lines = out["reply"].splitlines()
    before, after = lines[1], lines[2]
    assert before.startswith("Before: …") and "**maximum margin**" in before
    assert after.startswith("After: …") and "**widest margin**" in after
    lead = before.split("…")[1].split("**")[0]
    trail = before.split("**")[2].rstrip("…")
    assert len(lead) <= 20 and len(trail) <= 20
    assert "classifier that finds" not in out["reply"]          # not the whole document
    assert out["actions"][0]["previews"][0]["old"] == "maximum margin"


def test_append_reply_shows_what_was_added(library_docs):
    svm = library_docs["svm.txt"]["id"]
    out, _ = turn("p2", [("find_documents", {"description": "svm.txt"})],
                  [("edit_document", {"doc_id": svm, "operation": "append", "text": "See also: kernels."})])
    assert "Added after …" in out["reply"] and "**See also: kernels.**" in out["reply"]
