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


def test_an_edit_with_letters_the_file_has_writes_them_in_its_own_font(tmp_path, monkeypatch):
    src, out = tmp_path / "a.pdf", tmp_path / "b.pdf"
    used = []
    for name in ("_write_own_run", "_write_with_writer"):
        real = getattr(pdf_edit, name)
        monkeypatch.setattr(pdf_edit, name, lambda page, letters, *more, _n=name, _r=real: (used.append(_n), _r(page, letters, *more)))
    _page_with_link(src, ["Website and contact details", "Profile text that will change"])
    before = [f[3] for f in pymupdf.open(src)[0].get_fonts(full=True)]
    pdf_edit.replace(src, out, re.compile(r"Profile text"), "Profile notes", False)   # n, o, s: all in the file
    d = pymupdf.open(out)
    assert "Profile notes that will change" in " ".join(d[0].get_text().split())
    assert len(d[0].get_fonts(full=True)) <= len(before)                  # no font added
    assert used == ["_write_own_run"]                                    # nothing went through the text writer


def test_a_letter_the_files_font_lacks_still_comes_out_right(tmp_path):
    src, out = tmp_path / "a.pdf", tmp_path / "b.pdf"
    _page_with_link(src, ["Website and contact details", "Profile text that will change"])
    pdf_edit.replace(src, out, re.compile(r"Profile text"), "Profile jazz", False)    # j, z: not in the subset
    assert "Profile jazz that will change" in " ".join(pymupdf.open(out)[0].get_text().split())


def test_a_font_whose_map_lies_is_caught_by_the_glyph_drawn(tmp_path, monkeypatch, caplog):
    """Its copy-text map says the code for "W" reads as "o": written by that code the page would read right and look
    wrong. The glyph check sees it, and the edit is made the old way."""
    src, out = tmp_path / "a.pdf", tmp_path / "b.pdf"
    _page_with_link(src, ["Website and contact details", "Profile text that will change"])
    d = pymupdf.open(src)
    for xref, *_ in d[0].get_fonts(full=True):
        codes = pdf_edit._font_codes(d, xref)
        cmap_xref = int(d.xref_get_key(xref, "ToUnicode")[1].split()[0])
        lie = b"<%s> <006F>\n" % codes["W"].hex().upper().encode()   # bfchar is read first: this code wins for "o"
        cmap = d.xref_stream(cmap_xref).replace(b"beginbfchar\n", b"beginbfchar\n" + lie, 1)
        if b"beginbfchar" not in cmap:
            cmap = cmap.replace(b"endcodespacerange", b"endcodespacerange\n1 beginbfchar\n" + lie + b"endbfchar", 1)
        d.update_stream(cmap_xref, cmap)
        assert pdf_edit._font_codes(d, xref)["o"] == codes["W"]
    d.save(tmp_path / "lying.pdf")
    used = []
    for name in ("_write_own_run", "_write_with_writer"):
        real = getattr(pdf_edit, name)
        monkeypatch.setattr(pdf_edit, name, lambda page, letters, *more, _n=name, _r=real: (used.append(_n), _r(page, letters, *more)))
    pdf_edit.replace(tmp_path / "lying.pdf", out, re.compile(r"Profile text"), "Profile notes", False)
    assert used[0] == "_write_own_run" and "_write_with_writer" in used     # tried, caught, done the old way
    assert "drawn with another glyph" in caplog.text
    page = pymupdf.open(out)[0]
    helv = pymupdf.Font("helv")
    new_line = [gid for span in page.get_texttrace() for u, gid, origin, _ in span["chars"]
                if u == ord("o") and origin[1] > 107]                      # "notes", on the second line
    assert new_line and all(g == helv.has_glyph(ord("o")) for g in new_line)


def test_a_rotated_page_is_written_the_old_way(tmp_path, monkeypatch):
    src, out = tmp_path / "a.pdf", tmp_path / "b.pdf"
    _page_with_link(src, ["Website and contact details", "Profile text that will change"])
    d = pymupdf.open(src)
    d[0].set_rotation(90)
    d.save(tmp_path / "rotated.pdf")
    used = []
    real = pdf_edit._write_own_run
    monkeypatch.setattr(pdf_edit, "_write_own_run", lambda page, letters: (used.append(1), real(page, letters)))
    try:
        pdf_edit.replace(tmp_path / "rotated.pdf", out, re.compile(r"Profile text"), "Profile notes", False)
    except pdf_edit.CantKeepLayout:
        pass    # a rotated page may be refused by the layout rules; it must never be written by code
    assert used == []
