"""After an edit adds a page (backend/library/pdf_contents.py): bookmarks, links and labels follow by themselves; the
printed contents lines that point past the new page get the new number, in place and checked; body-text page
references are only reported; it is all one edit and one undo."""
import re

import pymupdf
import pytest

from backend.library import editing, library, pdf_contents, pdf_edit
from backend.tests.library.test_pdf_edit import PARAGRAPH, justified, write

MORE = "with many more words added to it " * 12


def _book(path, links=True, labels=None, outline=True, reference=True):
    """Contents on page 1 ("... 2", "... 3", "... 4"), a paragraph near the bottom of page 2 that will run onto a new
    page, then pages 3 and 4."""
    d = pymupdf.open()
    contents = d.new_page(width=612, height=792)
    entries = [("Opening chapter", 1), ("Middle chapter", 2), ("Closing chapter", 3)]
    for i, (title, target) in enumerate(entries):
        number = str(target + 1) if labels is None else labels[target]
        write(contents, 72, 120 + 24 * i, f"{title} ........................ {number}")
    body = d.new_page(width=612, height=792)
    write(body, 72, 100, "A first line near the top of the page.")
    words = (PARAGRAPH[0] + " " + PARAGRAPH[1]).split()
    justified(body, 690, words[:15], 72, 540)
    justified(body, 704, words[15:], 72, 540)
    write(body, 72, 718, "and that is all.")
    for n, heading in ((2, "The middle chapter starts here."), (3, "The closing chapter starts here.")):
        page = d.new_page(width=612, height=792)
        write(page, 72, 100, heading)
        if reference and n == 3:
            write(page, 72, 130, "As shown on page 3, the middle chapter matters.")
    if links:
        contents = d[0]                     # (the page object from before the other pages were added is stale)
        for i, (_title, target) in enumerate(entries):
            contents.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(70, 108 + 24 * i, 540, 124 + 24 * i),
                                  "page": target, "to": pymupdf.Point(0, 0)})
    if outline:
        d.set_toc([[1, title, target + 1] for title, target in entries])
    if labels is not None:
        d.set_page_labels([{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
                           {"startpage": 1, "prefix": "", "style": "D", "firstpagenum": 1}])
    d.save(str(path))
    return path


def _grow(src, out):
    info = {}
    pdf_edit.replace(src, out, re.compile(r"and that is all\."), "and that is all, " + MORE.strip() + ".", False, info)
    return info


def _contents(path) -> str:
    with pymupdf.open(str(path)) as d:
        return " ".join(d[0].get_text().split())


def test_contents_lines_past_the_new_page_get_the_new_number(tmp_path):
    src, out = _book(tmp_path / "book.pdf"), tmp_path / "out.pdf"
    info = _grow(src, out)
    assert info["new_pages"] == [3]
    text = _contents(out)
    assert "Opening chapter ........................ 2" in text            # before the new page: unchanged
    assert "Middle chapter ........................ 4" in text             # was 3
    assert "Closing chapter ........................ 5" in text            # was 4
    assert len(info["contents_renumbered"]) == 2
    with pymupdf.open(str(out)) as d:                                       # bookmarks and links needed nothing
        assert [e[2] for e in d.get_toc()] == [2, 4, 5]
        assert sorted(l["page"] for l in d[0].get_links()) == [1, 3, 4]


def test_without_links_the_bookmarks_identify_the_lines(tmp_path):
    src, out = _book(tmp_path / "book.pdf", links=False), tmp_path / "out.pdf"
    _grow(src, out)
    assert "Middle chapter ........................ 4" in _contents(out)


def test_lines_with_neither_a_link_nor_a_bookmark_are_left_alone(tmp_path):
    src, out = _book(tmp_path / "book.pdf", links=False, outline=False), tmp_path / "out.pdf"
    info = _grow(src, out)
    assert "Middle chapter ........................ 3" in _contents(out) and "contents_renumbered" not in info


def test_printed_numbers_are_compared_as_labels_not_indexes(tmp_path):
    # page 1 is "i", then 1, 2, 3: the contents print 1, 2, 3 for the chapters
    src, out = _book(tmp_path / "book.pdf", labels=["i", "1", "2", "3"]), tmp_path / "out.pdf"
    info = _grow(src, out)
    text = _contents(out)
    assert "Opening chapter ........................ 1" in text
    assert "Middle chapter ........................ 3" in text and "Closing chapter ........................ 4" in text
    assert len(info["contents_renumbered"]) == 2


def test_body_text_page_references_are_reported_not_changed(tmp_path):
    src, out = _book(tmp_path / "book.pdf"), tmp_path / "out.pdf"
    info = _grow(src, out)
    assert info["page_references"] == [{"page": 5, "text": "As shown on page 3, the middle chapter matters."}]
    with pymupdf.open(str(out)) as d:
        assert "As shown on page 3" in d[4].get_text()
        assert not any("Page" in r["text"] and " of " in r["text"] for r in info["page_references"])


def test_a_line_that_cant_be_changed_in_place_is_listed(tmp_path, monkeypatch):
    src, out = _book(tmp_path / "book.pdf"), tmp_path / "out.pdf"
    real = pdf_edit.replace

    def refuse_middle(path, out_, pattern, new, replace_all, info=None):
        if "Middle" in pattern.pattern:
            raise pdf_edit.CantKeepLayout("no room")
        return real(path, out_, pattern, new, replace_all, info)
    monkeypatch.setattr(pdf_edit, "replace", refuse_middle)
    info = {}
    real(src, out, re.compile(r"and that is all\."), "and that is all, " + MORE.strip() + ".", False, info)
    assert info["contents_kept"] == [{"page": 1, "line": "Middle chapter ........................ 3", "should_be": "4"}]
    assert "Closing chapter ........................ 5" in _contents(out)


def test_the_edit_and_its_contents_lines_are_one_undo(tmp_path):
    doc, _ = library.add_file("book.pdf", _book(tmp_path / "book.pdf").read_bytes(), owner="u5")
    doc = library.process(doc["id"])
    before = pdf_edit.document_text(library.config.LIBRARY_DIR / doc["file_name"])
    result = editing.edit(doc["id"], "replace", find="and that is all.", text="and that is all, " + MORE.strip() + ".")
    assert result["new_pages"] == [3] and len(result["contents_renumbered"]) == 2
    from backend.library import catalog
    editing.undo(doc["id"])
    after_undo = pdf_edit.document_text(library.config.LIBRARY_DIR / catalog.get_document(doc["id"])["file_name"])
    assert after_undo == before


def test_the_reply_mentions_what_was_renumbered_and_what_wasnt():
    from backend.library import agent
    reply = agent.compose_reply([{"status": "edited", "title": "book", "operation": "replace", "changes": 1,
                                  "previews": [], "new_pages": [3], "added_lines": 12,
                                  "contents_renumbered": [{"page": 1, "line": "Middle ... 4"}],
                                  "contents_kept": [{"page": 1, "line": "Closing ... 4", "should_be": "5"}],
                                  "page_references": [{"page": 5, "text": "see page 3"}]}])
    assert "1 table-of-contents line now gives the new page number" in reply
    assert '"Closing ... 4" (page 1) should say 5' in reply
    assert "page(s) 5 mentions page numbers" in reply
