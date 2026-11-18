"""What a simple PDF font's codes read as when it has no copy-text map (ToUnicode): from its /Encoding.

Many PDFs (Word, PowerPoint, ReportLab, Adobe and TeX exports) leave ToUnicode out of simple one-byte fonts and give
an /Encoding instead: a standard table (WinAnsi, MacRoman, Standard), optionally with /Differences that name the glyph
for some codes, or, with no table, the encoding built into the embedded font program. Glyph names are read through
the Adobe Glyph List (fontTools.agl). A name the list can't resolve ("g12", "a1", TeX's "parenleftbig") stays
unmapped: never guessed.

Only for embedded fonts (pdf_edit._OwnFonts skips the others): the glyph check on the saved page
(pdf_edit._check_own_glyphs) compares the glyph actually drawn with the program's glyph for the letter, so a wrong
code is refused there.
"""
from __future__ import annotations

import io
import re

import pymupdf
from fontTools import agl
from fontTools.encodings.StandardEncoding import StandardEncoding

_TOKEN = re.compile(r"(\d+)|/([^\s/\[\]<>(){}%]+)")


def _codec_table(codec: str) -> list[str | None]:
    out: list[str | None] = [None] * 256
    for code in range(32, 256):
        try:
            out[code] = bytes([code]).decode(codec)
        except UnicodeDecodeError:
            pass
    return out


def glyph_text(name: str) -> str | None:
    """The text a glyph name stands for, by the Adobe Glyph List; None when it can't be resolved."""
    text = agl.toUnicode(name) if name and name != ".notdef" else ""
    if not text or any(ord(c) < 32 or 0xE000 <= ord(c) <= 0xF8FF for c in text):   # (private-use: not real text)
        return None
    return text


_TABLES = {
    "WinAnsiEncoding": _codec_table("cp1252"),
    "MacRomanEncoding": _codec_table("mac_roman"),
    "StandardEncoding": [glyph_text(n) for n in StandardEncoding],
}


def builtin_names(program: bytes, ext: str) -> list[str | None] | None:
    """The 256 glyph names of a font program's own encoding (CFF, or a Type 1 font file in text form); None if it has none."""
    try:
        if ext == "cff":
            from fontTools.cffLib import CFFFontSet
            cff = CFFFontSet()
            cff.decompile(io.BytesIO(program), None)
            encoding = cff.topDictIndex[0].Encoding
        elif ext == "pfa":
            from fontTools.t1Lib import T1Font
            font = T1Font.__new__(T1Font)
            font.data, font.encoding = program, "ascii"
            font.parse()
            encoding = font.font.get("Encoding")
        else:
            return None
    except Exception:
        return None
    if encoding == "StandardEncoding":
        return list(StandardEncoding)
    if isinstance(encoding, list) and len(encoding) >= 256:
        return [n if n != ".notdef" else None for n in encoding[:256]]
    return None


def _builtin_table(doc: pymupdf.Document, xref: int) -> list[str | None] | None:
    try:
        _name, ext, _type, program = doc.extract_font(xref)
    except Exception:
        return None
    names = builtin_names(program, ext) if program else None
    return [glyph_text(n) if n else None for n in names] if names else None


def encoding_table(doc: pymupdf.Document, xref: int) -> list[str | None]:
    """The text of each of the font's 256 codes, from its /Encoding (None where unknown)."""
    kind, value = doc.xref_get_key(xref, "Encoding")
    base, differences = None, ""
    if kind == "name":
        base = value.lstrip("/")
    elif kind in ("xref", "dict"):
        owner, prefix = (int(value.split()[0]), "") if kind == "xref" else (xref, "Encoding/")
        k, v = doc.xref_get_key(owner, prefix + "BaseEncoding")
        base = v.lstrip("/") if k == "name" else None
        k, v = doc.xref_get_key(owner, prefix + "Differences")
        differences = v if k == "array" else ""
    if base in _TABLES:
        table = list(_TABLES[base])
    else:   # no table named: the program's own encoding, else Standard
        table = _builtin_table(doc, xref) or list(_TABLES["StandardEncoding"])
    code = None
    for number, name in _TOKEN.findall(differences):
        if number:
            code = int(number)
        elif code is not None:
            if 0 <= code < 256:
                table[code] = glyph_text(name)
            code += 1
    return table


def encoding_codes(doc: pymupdf.Document, xref: int) -> dict[str, bytes]:
    """{text: one-byte code} for a simple font without ToUnicode; {} for a composite (CID) font."""
    kind, subtype = doc.xref_get_key(xref, "Subtype")
    if subtype in ("/Type0", "/Type3"):
        return {}
    codes: dict[str, bytes] = {}
    for code, text in enumerate(encoding_table(doc, xref)):
        if text:
            codes.setdefault(text, bytes([code]))
    return codes
