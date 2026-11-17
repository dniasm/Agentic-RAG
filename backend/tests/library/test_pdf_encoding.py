"""Fonts without a copy-text map (ToUnicode): their codes are read from /Encoding (backend/library/pdf_encoding.py),
so an edit can write new letters in the document's own font instead of embedding a copy. A wrong map is caught by the
glyph check on the saved page."""
import io
import logging
import re

import pymupdf
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen

from backend.library import pdf_edit, pdf_encoding

ARIAL = "C:/Windows/Fonts/arial.ttf"


def _simple_font_pdf(path, encoding: str | None = None, lines=("hello world", "quiet study")):
    """A page in Arial as a simple TrueType font: WinAnsi, no ToUnicode (as Word and PowerPoint export it).
    `encoding` replaces the font's /Encoding; a line given as bytes is written by its codes."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="AR", fontbuffer=pymupdf.Font(fontfile=ARIAL).buffer, set_simple=True)
    for i, line in enumerate(lines):
        page.insert_text((72, 100 + 20 * i), line if isinstance(line, str) else "x", fontname="AR", fontsize=12)
    doc = pymupdf.open("pdf", doc.tobytes())
    page = doc[0]
    xref = page.get_fonts(full=True)[0][0]
    if encoding is not None:
        doc.xref_set_key(xref, "Encoding", encoding)
    raw = [line for line in lines if isinstance(line, bytes)]
    if raw:     # draw the byte lines by their codes
        stream = b"".join(b"BT /AR 12 Tf 72 %d Td <%s> Tj ET\n" % (300 + 20 * i, line.hex().encode())
                          for i, line in enumerate(raw))
        page.insert_text((0, 0), "")                          # (a content stream of our own to append to)
        doc.update_stream(page.get_contents()[-1], doc.xref_stream(page.get_contents()[-1]) + stream)
    doc.save(path)
    return xref


@pytest.fixture
def winansi_with_differences(tmp_path):
    path = tmp_path / "word_export.pdf"
    xref = _simple_font_pdf(path, "<< /Type /Encoding /BaseEncoding /WinAnsiEncoding /Differences [128 /eacute] >>",
                            lines=("hello world", "quiet study", b"caf\x80"))
    return path, xref


def _text(path) -> str:
    with pymupdf.open(path) as doc:
        return " ".join(doc[0].get_text().split())


def _fonts_on(path) -> int:
    with pymupdf.open(path) as doc:
        return len(doc[0].get_fonts(full=True))


def test_codes_come_from_the_base_table_and_the_differences(winansi_with_differences):
    path, xref = winansi_with_differences
    with pymupdf.open(path) as doc:
        assert doc.xref_get_key(xref, "ToUnicode")[0] == "null"
        codes = pdf_edit._font_codes(doc, xref)
        assert "café" in doc[0].get_text()                     # MuPDF reads code 128 as é too
    assert codes["é"] == b"\x80" and codes["q"] == b"q" and codes["\u201c"] == b"\x93"   # (WinAnsi's curly quote)


def test_an_edit_writes_new_letters_in_the_documents_own_font(winansi_with_differences, tmp_path):
    path, _ = winansi_with_differences
    out = tmp_path / "edited.pdf"
    pdf_edit.replace(path, out, re.compile(r"hello"), "étude", False, info={})
    assert "étude world quiet" in _text(out)
    assert _fonts_on(out) == _fonts_on(path)                    # no copy of the font embedded


def test_without_the_map_the_same_edit_embedded_a_copy(winansi_with_differences, tmp_path, monkeypatch):
    path, _ = winansi_with_differences
    monkeypatch.setattr(pdf_encoding, "encoding_codes", lambda doc, xref: {})   # the old behaviour
    out = tmp_path / "edited.pdf"
    pdf_edit.replace(path, out, re.compile(r"hello"), "étude", False, info={})
    assert _fonts_on(out) > _fonts_on(path)


def test_a_wrong_map_is_refused_by_the_glyph_check_and_the_edit_still_goes_through(winansi_with_differences, tmp_path,
                                                                                  monkeypatch, caplog):
    path, _ = winansi_with_differences
    real = pdf_encoding.encoding_codes

    def swapped(doc, xref):
        codes = real(doc, xref)
        codes["t"], codes["d"] = codes["d"], codes["t"]          # t and d each written with the other's code
        return codes
    monkeypatch.setattr(pdf_encoding, "encoding_codes", swapped)
    caplog.set_level(logging.INFO)
    out = tmp_path / "edited.pdf"
    pdf_edit.replace(path, out, re.compile(r"hello"), "étude", False, info={})
    assert any("in its own fonts failed (Checking the edited page failed" in r.getMessage() for r in caplog.records)
    assert "étude world quiet" in _text(out)                   # written the old way instead, correctly
    assert _fonts_on(out) > _fonts_on(path)


def test_unknown_glyph_names_stay_unmapped(tmp_path):
    path = tmp_path / "odd.pdf"
    xref = _simple_font_pdf(path, "<< /BaseEncoding /WinAnsiEncoding /Differences [65 /g12 /a1 /parenleftbig] >>")
    with pymupdf.open(path) as doc:
        table = pdf_encoding.encoding_table(doc, xref)
    assert table[65] is None and table[66] is None and table[67] is None and table[68] == "D"


def _cff_with_builtin_encoding() -> bytes:
    """A bare CFF font whose own encoding puts glyph "b" at code 65 and "a" at code 66."""
    fb = FontBuilder(1000, isTTF=False)
    order = [".notdef", "a", "b"]
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord("a"): "a", ord("b"): "b"})
    strings = {}
    for g in order:
        pen = T2CharStringPen(500, None)
        pen.moveTo((50, 0)), pen.lineTo((50, 700)), pen.lineTo((450, 700)), pen.closePath()
        strings[g] = pen.getCharString()
    fb.setupCFF("Boxes", {"FullName": "Boxes"}, strings, {})
    fb.setupHorizontalMetrics({g: (500, 50) for g in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Boxes", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    encoding = [".notdef"] * 256
    encoding[65], encoding[66] = "b", "a"
    fb.font["CFF "].cff.topDictIndex[0].Encoding = encoding
    return fb.font["CFF "].compile(fb.font)


def test_a_type1_font_without_an_encoding_uses_its_programs_own(tmp_path):
    program = _cff_with_builtin_encoding()
    assert pdf_encoding.builtin_names(program, "cff")[65:67] == ["b", "a"]
    doc = pymupdf.open()
    doc.new_page()
    file3 = doc.get_new_xref()
    doc.update_object(file3, "<< /Subtype /Type1C >>")
    doc.update_stream(file3, program)
    desc = doc.get_new_xref()
    doc.update_object(desc, f"<< /Type /FontDescriptor /FontName /Boxes /Flags 4 /FontBBox [0 0 500 700] "
                            f"/ItalicAngle 0 /Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 /FontFile3 {file3} 0 R >>")
    font = doc.get_new_xref()
    doc.update_object(font, f"<< /Type /Font /Subtype /Type1 /BaseFont /Boxes /FontDescriptor {desc} 0 R >>")
    codes = pdf_encoding.encoding_codes(doc, font)
    assert codes["b"] == b"A" and codes["a"] == b"B"


def test_a_font_with_a_copy_text_map_keeps_using_it(monkeypatch):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="AR", fontbuffer=pymupdf.Font(fontfile=ARIAL).buffer)   # a CID font with ToUnicode
    page.insert_text((72, 100), "hello", fontname="AR")
    doc = pymupdf.open("pdf", doc.tobytes())
    xref = doc[0].get_fonts(full=True)[0][0]
    calls = []
    monkeypatch.setattr(pdf_encoding, "encoding_codes", lambda d, x: calls.append(x) or {})
    codes = pdf_edit._font_codes(doc, xref)       # (MuPDF's own map has malformed entries: they're skipped)
    assert codes["h"] and not calls
