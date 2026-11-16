"""An edited PDF is saved without the weight the edit adds (a downloaded resume went from 110 KB to 247 KB for one
re-set paragraph), and an edit next to a link works (putting back a link a redaction took crashed on its "id")."""
import re

import pymupdf

from backend.library import pdf_edit


def _page_with_link(path, words):
    d = pymupdf.open()
    page = d.new_page(width=612, height=792)
    font = pymupdf.Font("helv")
    for i, line in enumerate(words):
        tw = pymupdf.TextWriter(page.rect)
        tw.append((72, 100 + 14 * i), line, font=font, fontsize=11)
        tw.write_text(page)
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 89, 200, 102), "uri": "https://example.com"})
    d.save(str(path))


def test_edits_next_to_a_link_can_follow_each_other(tmp_path):
    src, once, twice = tmp_path / "a.pdf", tmp_path / "b.pdf", tmp_path / "c.pdf"
    _page_with_link(src, ["Website and contact details", "Profile text that will change twice"])
    pdf_edit.replace(src, once, re.compile(r"Website"), "Homepage", False)
    pdf_edit.replace(once, twice, re.compile(r"Homepage"), "Web page", False)   # the link has an id now
    d = pymupdf.open(twice)
    assert "Web page and contact" in d[0].get_text() and [l["uri"] for l in d[0].get_links()] == ["https://example.com"]


def test_an_edited_pdf_is_saved_small(tmp_path, monkeypatch):
    src, small, plain = tmp_path / "a.pdf", tmp_path / "small.pdf", tmp_path / "plain.pdf"
    _page_with_link(src, ["Short line one", "A paragraph line that gets a much longer replacement text"])
    pdf_edit.replace(src, small, re.compile(r"Short line one"), "A new first line", False)
    monkeypatch.setattr(pdf_edit, "_save_small", lambda doc, out: doc.save(str(out), garbage=3, deflate=True))
    pdf_edit.replace(src, plain, re.compile(r"Short line one"), "A new first line", False)
    assert small.stat().st_size < plain.stat().st_size * 0.8
    assert pymupdf.open(small)[0].get_text() == pymupdf.open(plain)[0].get_text()
