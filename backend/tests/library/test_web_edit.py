"""Editing on the website: "Edit selection" in the preview (POST /edit, the same edit as the chat's) and the whole-text
editor for TXT / MD files (GET / PUT /text), each one undoable edit."""
from backend.library import catalog, config, editing, library


def _text(doc):
    return (config.LIBRARY_DIR / catalog.get_document(doc["id"])["file_name"]).read_text(encoding="utf-8")


def _ingest(name, data, owner=config.OWNER):
    doc, _ = library.add_file(name, data.encode("utf-8") if isinstance(data, str) else data, owner=owner)
    doc = library.process(doc["id"])
    assert doc["status"] == "indexed", doc["error"]
    return doc


def test_a_selected_passage_is_replaced_and_can_be_undone(library_docs, signed_in):
    doc = library_docs["svm.txt"]
    before = _text(doc)
    r = signed_in.post(f"/api/library/documents/{doc['id']}/edit",
                       json={"find": "finds the maximum\nmargin hyperplane", "text": "separates the classes"})
    assert r.status_code == 200, r.text
    assert "separates the classes" in _text(doc) and r.json()["edit_id"] == catalog.last_edit(doc["id"])["id"]
    assert signed_in.post(f"/api/library/documents/{doc['id']}/undo", json={}).status_code == 200
    assert _text(doc) == before


def test_text_selected_in_the_preview_matches_the_file(tmp_path):
    md = _ingest("notes.md", "# Notes\n\nThe **key** point is that cases rose.\n\nA self-contained example.\n")
    # the preview shows Markdown without its marks
    assert editing.edit(md["id"], "replace", find="key point is", text="main point was")["status"] == "edited"
    assert "The main point was that" in _text(md)
    # a word the page broke at a line end, with its hyphen kept or not
    txt = _ingest("words.txt", "Here is an example of a self-contained sentence.\n")
    assert editing.edit(txt["id"], "replace", find="an exam- ple of", text="one case of")["status"] == "edited"
    assert editing.edit(txt["id"], "replace", find="a self- contained", text="a standalone")["status"] == "edited"
    assert _text(txt) == "Here is one case of a standalone sentence.\n"


def test_a_passage_in_several_places_is_asked_about(tmp_path, signed_in):
    doc = _ingest("twice.txt", "Kernel trick. The kernel trick again. A kernel trick once more.\n", signed_in.user["id"])
    url = f"/api/library/documents/{doc['id']}/edit"
    r = signed_in.post(url, json={"find": "kernel trick", "text": "feature map"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    # the exact wording first, so "Kernel trick" with a capital is a different passage
    assert detail["ambiguous"] and detail["matches"] == 2 and len(detail["contexts"]) == 2
    assert _text(doc).count("kernel trick") == 2                            # nothing changed yet
    assert signed_in.post(url, json={"find": "kernel trick", "text": "feature map", "replace_all": True}).status_code == 200
    assert _text(doc) == "Kernel trick. The feature map again. A feature map once more.\n"


def test_a_passage_that_isnt_there_says_so(library_docs, signed_in):
    r = signed_in.post(f"/api/library/documents/{library_docs['svm.txt']['id']}/edit",
                       json={"find": "not in this document at all", "text": "x"})
    assert r.status_code == 400 and "isn't in the document" in r.json()["detail"]


def test_the_whole_text_editor_saves_one_undoable_edit(library_docs, signed_in):
    doc = library_docs["svm.txt"]
    url = f"/api/library/documents/{doc['id']}/text"
    opened = signed_in.get(url).json()
    assert opened["text"] == _text(doc) and len(opened["sha256"]) == 64
    new = opened["text"] + "\nA new closing line."
    r = signed_in.put(url, json={"text": new, "sha256": opened["sha256"]})
    assert r.status_code == 200, r.text
    assert _text(doc) == new and catalog.last_edit(doc["id"])["detail"] == "edited the text in the editor"
    # saving again from the text opened before that save would overwrite it: refused
    r = signed_in.put(url, json={"text": opened["text"] + "\nOther.", "sha256": opened["sha256"]})
    assert r.status_code == 409 and _text(doc) == new
    signed_in.post(f"/api/library/documents/{doc['id']}/undo", json={})
    assert _text(doc) == opened["text"]


def test_the_editor_keeps_the_files_line_endings(tmp_path, signed_in):
    doc = _ingest("crlf.txt", b"First line.\r\nSecond line.\r\n", signed_in.user["id"])
    url = f"/api/library/documents/{doc['id']}/text"
    opened = signed_in.get(url).json()
    assert opened["text"] == "First line.\nSecond line.\n"                 # the editor sees plain line breaks
    signed_in.put(url, json={"text": "First line.\nChanged line.\n", "sha256": opened["sha256"]})
    stored = (config.LIBRARY_DIR / catalog.get_document(doc["id"])["file_name"]).read_bytes()
    assert stored == b"First line.\r\nChanged line.\r\n"


def test_only_text_files_open_in_the_whole_text_editor(library_docs, signed_in, monkeypatch):
    doc = library_docs["svm.txt"]
    monkeypatch.setattr(catalog, "get_document",
                        lambda doc_id, _real=catalog.get_document: {**_real(doc_id), "ext": ".docx"})
    assert signed_in.get(f"/api/library/documents/{doc['id']}/text").status_code == 400
