"""Editing on the website: "Edit selection" in the preview (POST /edit, the same edit as the chat's) and the whole-text
editor for TXT / MD files (GET / PUT /text), each one undoable edit."""
from backend.library import catalog, config, editing, library, pdf_edit


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
    assert editing.edit(md["id"], "replace", find="key point is", text="main point was", loose=True)["status"] == "edited"
    assert "The main point was that" in _text(md)
    # a word the page broke at a line end, with its hyphen kept or not
    txt = _ingest("words.txt", "Here is an example of a self-contained sentence.\n")
    assert editing.edit(txt["id"], "replace", find="an exam- ple of", text="one case of", loose=True)["status"] == "edited"
    assert editing.edit(txt["id"], "replace", find="a self- contained", text="a standalone", loose=True)["status"] == "edited"
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


# ---------------------------------------------------------------- Fable's audit: loose matching is the website's only
def test_the_chat_agent_never_gets_loose_matching():
    md = _ingest("bold.md", "This is **bold text** and more.\n")
    try:
        editing.edit(md["id"], "replace", find="text and", text="X")   # the agent's call: as written, or not found
        raise AssertionError("a loose match was made for the agent")
    except editing.EditError as e:
        assert "isn't in the document" in str(e)
    assert _text(md) == "This is **bold text** and more.\n"


def test_a_selection_that_would_split_formatting_is_refused():
    md = _ingest("split.md", "This is **bold text** and more.\n")
    try:
        editing.edit(md["id"], "replace", find="text and", text="X", loose=True)
        raise AssertionError("half of a ** pair was taken")
    except editing.EditError as e:
        assert "formatting" in str(e)
    assert _text(md) == "This is **bold text** and more.\n"
    # a word's own underscore isn't a mark: the whole bold part may go
    var = _ingest("var.md", "Set **my_var** to one.\n")
    assert editing.edit(var["id"], "replace", find="my_var to", text="it to", loose=True)["status"] == "edited"
    assert _text(var) == "Set it to one.\n"
    # whole italic pairs, single marks included, may go too (* + * is a pair, not one **)
    italic = _ingest("italic.md", "The *key* point and the _main_ idea.\n")
    assert editing.edit(italic["id"], "replace", find="key point", text="first point", loose=True)["status"] == "edited"
    assert editing.edit(italic["id"], "replace", find="main idea", text="whole idea", loose=True)["status"] == "edited"
    assert _text(italic) == "The first point and the whole idea.\n"


def test_marks_are_only_marks_in_markdown():
    txt = _ingest("stars.txt", "A *starred* word here.\n")   # in a text file * is a letter like any other
    try:
        editing.edit(txt["id"], "replace", find="starred word", text="x", loose=True)
        raise AssertionError("a .txt file's * was treated as a mark")
    except editing.EditError as e:
        assert "isn't in the document" in str(e)


def test_a_file_that_isnt_utf8_isnt_opened_in_the_editor(signed_in):
    doc = _ingest("latin.txt", "Café au lait.\n".encode("latin-1"), signed_in.user["id"])
    r = signed_in.get(f"/api/library/documents/{doc['id']}/text")
    assert r.status_code == 400 and "UTF-8" in r.json()["detail"]


def test_the_editor_keeps_a_byte_order_mark(signed_in):
    doc = _ingest("bom.txt", b"\xef\xbb\xbfFirst line.\n", signed_in.user["id"])
    url = f"/api/library/documents/{doc['id']}/text"
    opened = signed_in.get(url).json()
    assert opened["text"] == "First line.\n"
    assert signed_in.put(url, json={"text": "Second line.\n", "sha256": opened["sha256"]}).status_code == 200
    assert (config.LIBRARY_DIR / catalog.get_document(doc["id"])["file_name"]).read_bytes() == b"\xef\xbb\xbfSecond line.\n"


def test_a_refusal_for_missing_letters_says_which(library_docs, signed_in, monkeypatch):
    def lacking(*a, **k):
        raise editing.NeedsConversion('no copy of the font for the letters "j"', missing="j")
    monkeypatch.setattr(editing, "edit", lacking)
    r = signed_in.post(f"/api/library/documents/{library_docs['svm.txt']['id']}/edit", json={"find": "a", "text": "jj"})
    assert r.status_code == 409 and r.json()["detail"]["missing"] == "j"


# ---------------------------------------------------------------- converting a PDF: the text the preview shows
def _pdf(lines):
    import pymupdf
    d = pymupdf.open()
    page = d.new_page()
    for i, line in enumerate(lines):
        page.insert_text((72, 72 + 20 * i), line, fontname="helv", fontsize=11)
    data = d.tobytes()
    d.close()
    return data


def test_a_converted_pdf_takes_the_pdf_editors_text(monkeypatch):
    doc = catalog.get_document(_ingest("job.pdf", _pdf(["Radiant InfoTech Nepal", "Apr 2025 to Jun 2025"]))["id"])
    assert "Radiant InfoTech Nepal" in editing._conversion_text(doc)          # words whole (pypdf: "InfoT ech")
    # the PDF editor's reading (ToUnicode: real dashes), not indexing's
    monkeypatch.setattr(pdf_edit, "document_text", lambda path: "Apr 2025 – Jun 2025")
    assert editing._conversion_text(doc) == "Apr 2025 – Jun 2025"
    monkeypatch.setattr(pdf_edit, "document_text", lambda path: (_ for _ in ()).throw(ValueError("unreadable")))
    assert "Radiant" in editing._conversion_text(doc)                         # unreadable for it: indexing's text


def test_conversion_is_offered_only_when_it_would_find_the_passage():
    doc = catalog.get_document(_ingest("offer.pdf", _pdf(["First line here", "Second line there"]))["id"])
    assert editing._convertible(doc, "Second line there", loose=True)
    assert not editing._convertible(doc, "a passage that is nowhere", loose=True)


def test_conversion_takes_the_cleaner_reading_and_offers_what_it_would_convert(monkeypatch):
    from backend.rag import ingestion

    class Page:
        def __init__(self, text):
            self.page_content = text

    doc = catalog.get_document(_ingest("readings.pdf", _pdf(["Batch normalization helps"]))["id"])

    def readings(editor, indexed):
        monkeypatch.setattr(pdf_edit, "document_text", lambda path: editor)
        monkeypatch.setattr(ingestion, "load_file", lambda path: ([Page(indexed)], None))

    # the editor's reading lost letters (U+FFFD, or private-use codes): indexing's is cleaner, and is what converts
    readings("Batch normaliza�on helps", "Batch normalization helps")
    assert editing._conversion_text(doc) == "Batch normalization helps"
    assert editing._convertible(doc, "normalization helps", loose=True)
    readings("Batch normalizaon helps", "Batch normalization helps")
    assert editing._conversion_text(doc) == "Batch normalization helps"
    # equally clean: the editor's, which is what the preview's selection matches
    readings("Batch normalization – helps", "Batch normalization - helps")
    assert editing._conversion_text(doc) == "Batch normalization – helps"
    assert editing._convertible(doc, "normalization – helps", loose=True)
    assert not editing._convertible(doc, "normalization - helps", loose=False)   # not in what it would become
