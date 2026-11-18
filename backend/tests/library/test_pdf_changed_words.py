"""A change is placed by the words it really changes: "1.1 Introduction" to "1.1 Hello World" in a contents line
that breaks after "1.1" is one line's change, not a paragraph's (it was refused, and a Markdown copy offered)."""
import re

import pymupdf

from backend.library import pdf_edit


def test_only_the_changed_words_are_replaced():
    assert pdf_edit._changed_words("1.1 Introduction", "1.1 Hello World") == (4, 16, "Hello World")
    assert pdf_edit._changed_words("a b c", "a x c") == (2, 3, "x")
    assert pdf_edit._changed_words("Introduction", "Hello") == (0, 12, "Hello")
    old = "x y"
    a, b, text = pdf_edit._changed_words(old, "x")
    assert old[:a] + text + old[b:] == "x"


def test_a_contents_line_split_after_its_number_is_edited_in_place(tmp_path):
    src, out = tmp_path / "toc.pdf", tmp_path / "out.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "1.1", fontname="tiro", fontsize=12)
    page.insert_text((110, 100), "Introduction ........................ 1", fontname="tiro", fontsize=12)
    page.insert_text((72, 200), "1.1 Introduction", fontname="tibo", fontsize=14)
    doc.save(src)
    info = {}
    n, _, _ = pdf_edit.replace(src, out, re.compile(r"1\.1\s+Introduction"), "1.1 Hello World", True, info)
    assert n == 2 and info["layout"] == "line"
    text = pymupdf.open(out)[0].get_text()
    assert "Introduction" not in text and text.count("Hello World") == 2
