"""The glyph check for letters written in a PDF's own fonts (pdf_edit._check_own_glyphs) compares the glyph drawn with
the one font object the letter was written in, not with every font object of that name on the page: two subsets of a
font number their glyphs differently, so the other subset's number for the letter must not pass."""
import pymupdf
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from backend.library import pdf_edit


def _boxes(order: list[str]) -> bytes:
    """A TrueType font named Boxes-Regular whose glyphs come in `order` (so each letter has another number)."""
    import io
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", *order])
    fb.setupCharacterMap({ord(c): c for c in order})
    glyphs = {}
    for g in [".notdef", *order]:
        pen = TTGlyphPen(None)
        pen.moveTo((50, 0)), pen.lineTo((50, 700)), pen.lineTo((450, 700)), pen.lineTo((450, 0)), pen.closePath()
        glyphs[g] = pen.glyph()
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({g: (500, 50) for g in [".notdef", *order]})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Boxes", "styleName": "Regular", "psName": "Boxes-Regular"})
    fb.setupOS2()
    fb.setupPost()
    out = io.BytesIO()
    fb.save(out)
    return out.getvalue()


@pytest.fixture
def two_subsets():
    """A page with two font objects named Boxes-Regular: in A "a" is glyph 1, in B glyph 3. An "a" drawn with B."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="FA", fontbuffer=_boxes(["a", "b", "c"]))
    page.insert_font(fontname="FB", fontbuffer=_boxes(["c", "b", "a"]))
    page.insert_text((72, 100), "b", fontname="FA", fontsize=12)    # (A used on the page too)
    page.insert_text((72, 140), "a", fontname="FB", fontsize=12)
    doc = pymupdf.open("pdf", doc.tobytes())
    page = doc[0]
    resources = {name: xref for xref, _e, _t, _b, name, *_ in page.get_fonts(full=True)}
    drawn = {chr(u): gid for span in page.get_texttrace() for u, gid, _o, _b in span["chars"]}
    assert set(resources) >= {"FA", "FB"} and drawn["a"] == 3   # the setup: B's number for "a"
    return page


def _letter(resource):
    style = pdf_edit.Style("Boxes-Regular", 12.0, 0)
    return pdf_edit._Typeset(72, 140, "a", style, pymupdf.Font("helv"), resource=resource)


def test_the_letter_is_checked_against_the_font_object_it_was_written_in(two_subsets):
    pdf_edit._check_own_glyphs(two_subsets, [_letter("FB")])   # drawn with B, written in B: right


def test_a_glyph_another_subset_would_have_drawn_is_refused(two_subsets):
    # written as A's "a" (glyph 1) but glyph 3 was drawn: the union of both subsets' numbers would have let it pass
    with pytest.raises(pdf_edit.CantKeepLayout):
        pdf_edit._check_own_glyphs(two_subsets, [_letter("FA")])


def test_a_letter_without_a_recorded_font_object_still_gets_the_name_check(two_subsets):
    pdf_edit._check_own_glyphs(two_subsets, [_letter(None)])
