"""A font file cut down to a few letters, so an edit that needs a letter the PDF's own font lacks adds only that letter.

MuPDF embeds the whole font it is given, and can't subset a Type 1 font (TeX's Computer Modern) afterwards: one new
"j" would cost the complete 36 KB program. Here fontTools makes a font of just the glyphs asked for, with the same
outlines, advance widths and PostScript name, so it typesets and checks exactly like the complete one:

  * TrueType / OpenType: fontTools' subsetter, keeping names and hinting.
  * Type 1 (.pfb, .pfa): the outlines are redrawn as a small CFF OpenType font (Type 1 and CFF describe glyphs the
    same way: cubic curves on the same 1000-unit grid), with the font's hinting zones carried over.

tiny_font(path, letters) returns the font's bytes, or None when it can't be made (a letter missing, a file fontTools
can't read); the caller then uses the complete font as before.
"""
from __future__ import annotations

import functools
import io
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_TYPE1 = {".pfb", ".pfa"}
# Type 1 Private dictionary entries that mean the same in CFF (hinting zones and stem widths)
_PRIVATE = ("BlueValues", "OtherBlues", "FamilyBlues", "FamilyOtherBlues", "BlueScale", "BlueShift", "BlueFuzz",
            "StdHW", "StdVW", "StemSnapH", "StemSnapV", "LanguageGroup", "ExpansionFactor")


def tiny_font(path: Path, letters: str) -> bytes | None:
    """A font of just these letters (plus .notdef) from the complete font file at `path`."""
    return _cached(Path(path), "".join(sorted(set(letters))))


@functools.lru_cache(maxsize=256)
def _cached(path: Path, letters: str) -> bytes | None:
    try:
        if path.suffix.lower() in _TYPE1:
            return _from_type1(path, letters)
        return _from_sfnt(path, letters)
    except Exception as e:      # any font fontTools can't handle: the complete font is used instead
        log.info("No small copy of %s for %r: %s", path.name, letters, e)
        return None


def _from_sfnt(path: Path, letters: str) -> bytes | None:
    from fontTools import subset
    from fontTools.ttLib import TTFont

    font = TTFont(str(path), fontNumber=0, lazy=False)
    cmap = font.getBestCmap() or {}
    if any(ord(c) not in cmap for c in letters):
        return None
    options = subset.Options()
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.layout_features = []        # letters are placed one by one: no shaping, kerning or ligature tables needed
    options.glyph_names = True
    sub = subset.Subsetter(options)
    sub.populate(unicodes=[ord(c) for c in letters])
    sub.subset(font)
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def _type1_names(glyph_names) -> dict[str, str]:
    """{letter: glyph name} from the names themselves (Adobe's glyph list: "j" -> "j", "fi" -> "ﬁ")."""
    from fontTools.agl import toUnicode

    names: dict[str, str] = {}
    for g in glyph_names:
        u = toUnicode(g)
        if len(u) == 1:
            names.setdefault(u, g)
    return names


def _from_type1(path: Path, letters: str) -> bytes | None:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    from fontTools.t1Lib import T1Font

    t1 = T1Font(str(path))
    t1.parse()
    glyphs = t1.getGlyphSet()
    names = _type1_names(glyphs.keys())
    if any(c not in names for c in letters):
        return None
    info = t1.font
    matrix = info.get("FontMatrix", [0.001, 0, 0, 0.001, 0, 0])
    if [round(v, 6) for v in matrix] != [0.001, 0, 0, 0.001, 0, 0]:
        return None                     # a scaled or slanted grid: CFF could carry it, but it is rare; not attempted
    order = [".notdef"] + sorted({names[c] for c in letters})
    charstrings, metrics = {}, {}
    for g in order:
        glyph = glyphs[g]
        drawn = RecordingPen()
        glyph.draw(drawn)               # running the charstring sets its advance width
        pen = T2CharStringPen(glyph.width, None)
        drawn.replay(pen)
        charstrings[g] = pen.getCharString()
        metrics[g] = (round(glyph.width), 0)
    ps = info["FontName"]
    names_of = info.get("FontInfo", {})     # the names MuPDF reports for the complete font come from here
    full, family = names_of.get("FullName", ps), names_of.get("FamilyName", ps)
    bbox = info.get("FontBBox", [0, -250, 1000, 750])
    private = {k: info["Private"][k] for k in _PRIVATE if k in info.get("Private", {})}
    for k in ("StdHW", "StdVW"):        # one number in CFF, a one-number array in Type 1
        if isinstance(private.get(k), (list, tuple)):
            private[k] = private[k][0] if private[k] else None
    private = {k: v for k, v in private.items() if v is not None}
    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord(c): names[c] for c in letters})
    fb.setupCFF(ps, {"FullName": full, "FamilyName": family},
                charstrings, private)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=round(bbox[3]), descent=round(bbox[1]))
    fb.setupNameTable({"familyName": family, "styleName": names_of.get("Weight", "Regular"), "fullName": full,
                       "psName": ps})
    fb.setupOS2(sTypoAscender=round(bbox[3]), sTypoDescender=round(bbox[1]), usWinAscent=max(0, round(bbox[3])),
                usWinDescent=max(0, -round(bbox[1])))
    fb.setupPost()
    out = io.BytesIO()
    fb.save(out)
    return out.getvalue()
