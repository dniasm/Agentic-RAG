"""A letter the PDF's own font lacks is added as a font of just that letter, not the whole font file (one "j" in a
TeX resume embedded the complete 36 KB Computer Modern program: 110 KB -> 145 KB)."""
import io
import re
from pathlib import Path

import pymupdf
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from backend.library import pdf_edit
from backend.library.tiny_font import tiny_font

CMR10 = Path(pdf_edit.__file__).parent / "fonts" / "cm" / "cmr10.pfb"


def test_a_type1_font_cut_to_one_letter_draws_it_the_same():
    data = tiny_font(CMR10, "j")
    small, full = pymupdf.Font(fontbuffer=data), pymupdf.Font(fontfile=str(CMR10))
    assert len(data) < 2000 < CMR10.stat().st_size
    assert small.name == full.name and small.has_glyph(ord("j")) and not small.has_glyph(ord("k"))
    assert abs(small.text_length("j", 1000) - full.text_length("j", 1000)) < 0.01


def test_a_ligature_and_a_missing_letter():
    assert pymupdf.Font(fontbuffer=tiny_font(CMR10, "ﬁ")).has_glyph(0xFB01)      # "fi" by its glyph name
    assert tiny_font(CMR10, "j€") is None                                        # no euro in CMR10: whole font


def _box_font(path):
    fb = FontBuilder(1000, isTTF=True)
    order = [".notdef", "a", "b", "c"]
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord(c): c for c in "abc"})
    glyphs = {}
    for g in order:
        pen = TTGlyphPen(None)
        pen.moveTo((50, 0)), pen.lineTo((50, 700)), pen.lineTo((450, 700)), pen.lineTo((450, 0)), pen.closePath()
        glyphs[g] = pen.glyph()
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({g: (500 + 10 * i, 50) for i, g in enumerate(order)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Boxes", "styleName": "Regular", "psName": "Boxes-Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.save(str(path))


def test_a_truetype_font_cut_to_some_letters(tmp_path):
    _box_font(tmp_path / "boxes.ttf")
    font = TTFont(io.BytesIO(tiny_font(tmp_path / "boxes.ttf", "ca")))
    assert set(font.getBestCmap().values()) == {"a", "c"}
    assert font["hmtx"]["c"] == (530, 50)


def _tex_like_pdf(path, text):
    """A page set in a CMR10 subset with only the letters of `text`, named as TeX names it."""
    font = TTFont(io.BytesIO(tiny_font(CMR10, text)))
    for record in font["name"].names:
        if record.nameID in (1, 4, 6):
            record.string = "CMR10"
        elif record.nameID == 2:
            record.string = "Regular"
    buf = io.BytesIO()
    font.save(buf)
    d = pymupdf.open()
    page = d.new_page(width=612, height=792)
    tw = pymupdf.TextWriter(page.rect)
    tw.append((72, 100), text, font=pymupdf.Font(fontbuffer=buf.getvalue()), fontsize=11)
    tw.write_text(page)
    d.save(str(path), garbage=4, deflate=True)


def test_an_edit_needing_a_letter_the_subset_lacks_adds_only_that_letter(tmp_path):
    src, out = tmp_path / "a.pdf", tmp_path / "b.pdf"
    _tex_like_pdf(src, "Profile text that will change")
    pdf_edit.replace(src, out, re.compile(r"text"), "jest", False)       # j: not in the page's subset
    assert "Profile jest that will" in " ".join(pymupdf.open(out)[0].get_text().split())
    assert [f[3] for f in pymupdf.open(src)[0].get_fonts()] == ["CMR10 Regular"]
    assert out.stat().st_size < src.stat().st_size + 4000                 # not the 36 KB font file
