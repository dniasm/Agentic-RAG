"""Editing a PDF's text in place, so the change looks like the rest of the document.

A PDF has no sentences or paragraphs, only letters drawn at positions. This module works out the structure itself and
re-typesets as little as possible:

  * Layout. Letters are grouped into rows (a shared baseline), rows into segments (text separated by a wide gap, e.g.
    a job title on the left and its dates on the right) and segments into words. Each segment's alignment is worked
    out from the page: justified (a paragraph line ending on the paragraph's right edge), centred, right-aligned (the
    last of several segments on a row) or left-aligned.
  * Style. New text takes the font, size and colour of the text it replaces. The font is the one embedded in the PDF
    when it has every letter needed (only letters the document already uses are trusted, since embedded fonts are
    usually subsets); otherwise the complete font of the same name, from backend/library/fonts, data/fonts or the system's
    fonts; otherwise one of PDF's 14 standard fonts when the name is one of them. Spacing between letter pairs
    (kerning) and any letter-spacing are learned from how the document itself sets that font, and ligatures (fi, fl)
    are used where the document uses them.
  * Placement. Words that don't change keep their exact letter positions; a changed line is re-spaced the way it was
    set (a justified line still fills its width, a centred one stays centred, a right-aligned one keeps its right
    edge). Only the letters that change or move are removed, by redaction that leaves drawings, images and the rest
    of the text alone.
  * Check. The saved file is read back and compared with what was intended: every untouched letter still in place,
    every new letter where it should be in the right font, size and colour, nothing left over, the same drawings,
    images and links. Anything else and the edit is refused (CantKeepLayout) with the original left as it was.

  * Re-wrapping. When the new text doesn't fit its line (or runs over lines), its paragraph is set again from that
    line on, the way it was set: same left edges, line spacing and right edge; justified lines are broken all at
    once as TeX does, keeping the paragraph's own word spacing (wider after a sentence where the document does
    that), ragged ones filled greedily as a word processor does. Lines that come out as they were stay untouched.
  * Moving what is below. A paragraph that needs a line more (or one fewer) moves everything below it on the page,
    down to the body's end, by that many lines: that part of the original page is drawn again lower, so its text,
    rules, boxes and images look exactly as before. Running headers and footers stay put. Refused when the page
    has no room left (the body may reach as low as it does on any page), has columns beside the paragraph, or
    something runs across the boundary.

An edit that can't be made consistently (no room left on the page, columns, a font with no complete copy
available, a scanned page) raises CantKeepLayout saying why; the caller can then offer the Markdown copy instead.
"""
from __future__ import annotations

import functools
import logging
import os
import re
import statistics
import struct
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from backend.library import config, font_fetch, pdf_encoding
from backend.library.tiny_font import tiny_font

log = logging.getLogger(__name__)


class NeedsRewrap(Exception):
    """The new text doesn't fit its line as it is set; re-wrapping the paragraph may still keep the look."""


class CantKeepLayout(Exception):
    """The edit can't be made in the PDF without breaking its look. The message says why, for the user."""


class MissingLetters(CantKeepLayout):
    """The PDF's font for this text has no copy of some of its letters (`missing`)."""

    def __init__(self, message: str, missing: str):
        super().__init__(message)
        self.missing = missing


class NoRoom(CantKeepLayout):
    """The new text needs more lines than the page has room for: a shorter wording may still fit."""


# ---------------------------------------------------------------- fonts
FONT_DIRS = [Path(__file__).parent / "fonts", config.DATA_DIR / "fonts",
             Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
             Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts",
             Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts",
             Path("/Library/Fonts"), Path("/System/Library/Fonts"), Path.home() / "Library" / "Fonts"]
FONT_SUFFIXES = {".ttf", ".otf", ".pfb", ".pfa", ".ttc"}
BASE14 = {"helvetica": "helv", "helveticabold": "hebo", "helveticaoblique": "heit", "helveticaboldoblique": "hebi",
          "timesroman": "tiro", "timesbold": "tibo", "timesitalic": "tiit", "timesbolditalic": "tibi",
          "courier": "cour", "courierbold": "cobo", "courieroblique": "coit", "courierboldoblique": "cobi",
          "symbol": "symb", "zapfdingbats": "zadb",
          # the same designs under the names of their free copies (MuPDF's, Ghostscript's, URW's)
          "nimbussans": "helv", "nimbussansbold": "hebo", "nimbussansitalic": "heit", "nimbussansbolditalic": "hebi",
          "nimbussansoblique": "heit", "nimbussansboldoblique": "hebi", "nimbussanl": "helv", "nimbussanlregu": "helv",
          "nimbussanlbold": "hebo", "nimbusroman": "tiro", "nimbusromanbold": "tibo", "nimbusromanitalic": "tiit",
          "nimbusromanbolditalic": "tibi", "nimbusromno9lregu": "tiro", "nimbusromno9lmedi": "tibo",
          "nimbusromno9lreguital": "tiit", "nimbusromno9lmediital": "tibi", "nimbusmono": "cour", "nimbusmonobold": "cobo",
          "nimbusmonoitalic": "coit", "nimbusmonobolditalic": "cobi", "standardsymbols": "symb", "dingbats": "zadb"}
LIGATURES = [("ffi", "\ufb03"), ("ffl", "\ufb04"), ("ff", "\ufb00"), ("fi", "\ufb01"), ("fl", "\ufb02")]
_SUBSET = re.compile(r"^[A-Z]{6}\+")


def base_name(name: str) -> str:
    """A font name without its subset tag: "ZAGFMN+CMR10" -> "CMR10"."""
    return _SUBSET.sub("", name or "")


def font_key(name: str) -> str:
    """A font name reduced so the spellings of one font agree: "Arial-BoldMT", "Arial,Bold" -> "arialbold";
    "TimesNewRomanPSMT" -> "timesnewroman"."""
    name = base_name(name)
    name = re.sub(r"PS(?=-|,|MT$|$)", "", name)
    name = re.sub(r"MT$", "", name)
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    return key[:-7] if key.endswith("regular") and len(key) > 7 else key


def _ps_name(path: Path) -> str | None:
    """The PostScript name inside a font file (Type 1: /FontName; TrueType/OpenType: name record 6)."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
            if path.suffix.lower() in (".pfb", ".pfa"):
                m = re.search(rb"/FontName\s*/([^\s/]+)", head)
                return m.group(1).decode("latin-1") if m else None
            f.seek(0)
            data = f.read()
    except OSError:
        return None
    try:
        offset = struct.unpack(">I", data[12:16])[0] if data[:4] == b"ttcf" else 0   # a collection: its first font
        tables = struct.unpack(">H", data[offset + 4: offset + 6])[0]
        for i in range(tables):
            tag, _, start, _ = struct.unpack(">4sIII", data[offset + 12 + 16 * i: offset + 28 + 16 * i])
            if tag != b"name":
                continue
            _, count, strings = struct.unpack(">HHH", data[start: start + 6])
            for j in range(count):
                platform, _, _, name_id, length, off = struct.unpack(">HHHHHH", data[start + 6 + 12 * j: start + 18 + 12 * j])
                if name_id == 6:
                    raw = data[start + strings + off: start + strings + off + length]
                    return raw.decode("utf-16-be" if platform in (0, 3) else "latin-1", errors="ignore")
    except (struct.error, IndexError):
        return None
    return None


def _index_folder(folder: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() not in FONT_SUFFIXES:
            continue
        for name in (_ps_name(path), path.stem):
            if name:
                found.setdefault(font_key(name), str(path))
    return found


@functools.cache
def _folder_index(folder: Path) -> dict[str, str]:
    """A folder's fonts by font_key of their PostScript name (and file name). A large folder's index (the system's
    fonts: reading hundreds of files takes seconds) is kept in data/ and rebuilt when the folder changes."""
    if not folder.is_dir():
        return {}
    if folder in FONT_DIRS[:2]:
        return _index_folder(folder)
    import hashlib
    import json
    store = config.DATA_DIR / "font-index" / (hashlib.sha1(str(folder).encode()).hexdigest()[:16] + ".json")
    stamp = folder.stat().st_mtime
    try:
        saved = json.loads(store.read_text(encoding="utf-8"))
        if saved["stamp"] == stamp:
            return saved["fonts"]
    except (OSError, ValueError, KeyError):
        pass
    fonts = _index_folder(folder)
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps({"stamp": stamp, "fonts": fonts}), encoding="utf-8")
    except OSError:
        pass
    return fonts


def find_font_file(name: str) -> Path | None:
    """A complete font file for a PDF font name. The bundled and data folders come first, so they win over a system
    font of the same name; the system folders are only read when those don't have it. Last, a copy downloaded by
    prefetch_fonts (font_fetch): LaTeX's cm-super, or a free copy with the same widths (Liberation for Arial)."""
    key = font_key(name)
    for folder in FONT_DIRS:
        path = _folder_index(folder).get(key)
        if path:
            return Path(path)
    return font_fetch.cached(name)


def prefetch_fonts(path: Path) -> None:
    """Download the complete fonts this PDF may need that the machine doesn't have (see font_fetch). Called before
    an edit takes the library lock; never raises, and gives up after a few seconds."""
    try:
        with pymupdf.open(path) as doc:
            names = {base_name(f[3]) for page in doc for f in page.get_fonts()}
        font_fetch.prefetch(n for n in names if find_font_file(n) is None)
    except Exception as e:
        log.info("Font prefetch for %s skipped: %s", Path(path).name, e)


_FILE_OF: dict[int, Path] = {}     # fonts loaded from a file (kept for good by _file_font) -> the file


@functools.cache
def _file_font(path: Path) -> pymupdf.Font:
    font = pymupdf.Font(fontfile=str(path))
    _FILE_OF[id(font)] = path
    return font


def _small_copy(font: pymupdf.Font, letters: set[str]) -> pymupdf.Font:
    """A complete font from a file, cut down to these letters (see tiny_font), when the cut copy names, measures and
    has every letter as the complete one does; the complete font otherwise."""
    path = _FILE_OF.get(id(font))
    data = tiny_font(path, "".join(letters)) if path else None
    if not data:
        return font
    try:
        small = pymupdf.Font(fontbuffer=data)
    except Exception:
        return font
    same = font_key(small.name) == font_key(font.name) and all(
        small.has_glyph(ord(c)) and abs(small.text_length(c, 1000) - font.text_length(c, 1000)) < 0.01 for c in letters)
    return small if same else font


def _has(font: pymupdf.Font, text: str) -> bool:
    return all(font.has_glyph(ord(c)) for c in text)


# ---------------------------------------------------------------- the layout model
@dataclass(frozen=True)
class Style:
    font: str        # as the PDF names it, subset tag removed
    size: float
    color: int


@dataclass(eq=False)
class Glyph:
    c: str           # the character as extracted (a ligature is one character, e.g. "ﬁ")
    x: float         # origin: left end of the baseline
    y: float
    x1: float        # right edge (the advance)
    bbox: tuple
    style: Style

    @property
    def text(self) -> str:        # what the glyph reads as: a ligature as its letters, a soft hyphen as a hyphen
        if self.c in "\ufb00\ufb01\ufb02\ufb03\ufb04":
            return unicodedata.normalize("NFKC", self.c)
        return "-" if self.c == "\xad" else self.c


@dataclass(eq=False)
class Word:
    glyphs: list[Glyph]

    @property
    def text(self) -> str:
        return "".join(g.text for g in self.glyphs)

    @property
    def x(self) -> float:
        return self.glyphs[0].x

    @property
    def end(self) -> float:
        return max(g.x1 for g in self.glyphs)


@dataclass(eq=False)
class Segment:
    page: int
    words: list[Word]
    size: float
    baseline: float
    locked: bool = False           # vertical or rotated text: not edited
    align: str = "left"            # left, right, center, justify
    left_limit: float = 0.0        # how far it may grow on each side
    right_limit: float = 0.0
    start: int = 0                 # offset of its text in the document text

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return self.words[0].x

    @property
    def x1(self) -> float:
        return self.words[-1].end

    @property
    def top(self) -> float:
        return min(g.bbox[1] for w in self.words for g in w.glyphs)

    @property
    def bottom(self) -> float:
        return max(g.bbox[3] for w in self.words for g in w.glyphs)


def _metric_font(doc: pymupdf.Document, name: str, cache: dict) -> pymupdf.Font | None:
    """A font to measure letters of the PDF font `name` with (embedded, else a complete copy, else standard)."""
    if name not in cache:
        cache[name] = None
        for page in doc:
            for xref, ext, _type, basefont, *_ in page.get_fonts(full=True):
                if base_name(basefont) == name and ext not in ("n/a", ""):
                    try:
                        buffer = doc.extract_font(xref)[3]
                        if buffer:
                            cache[name] = pymupdf.Font(fontbuffer=buffer)
                            return cache[name]
                    except Exception:
                        continue
        path = find_font_file(name)
        if path:
            try:
                cache[name] = _file_font(path)
            except Exception:
                pass
        if cache[name] is None and font_key(name) in BASE14:
            cache[name] = pymupdf.Font(BASE14[font_key(name)])
    return cache[name]


def _join_ligatures(glyphs: list[Glyph | None], doc: pymupdf.Document, cache: dict) -> list[Glyph | None]:
    """MuPDF reads a ligature (one shape for "fi") as an "f" as wide as the whole shape plus an "i" that isn't drawn.
    Where the "f" measures as the ligature, the two become one glyph again: c = "ﬁ"."""
    out = list(glyphs)
    i = 0
    while i < len(out):
        a = out[i]
        if a is not None and a.c == "f":
            for letters, lig in LIGATURES:
                follow = out[i + 1: i + len(letters)]
                if len(follow) != len(letters) - 1 or any(g is None or g.style != a.style for g in follow) \
                        or "f" + "".join(g.c for g in follow) != letters:
                    continue
                font = _metric_font(doc, a.style.font, cache)
                if font is None or not font.has_glyph(ord(lig)):
                    break
                measured, as_lig, as_f = a.x1 - a.x, font.text_length(lig, a.style.size), font.text_length("f", a.style.size)
                if abs(measured - as_lig) < min(abs(measured - as_f), 0.03 * a.style.size):
                    out[i] = Glyph(lig, a.x, a.y, a.x1, a.bbox, a.style)
                    del out[i + 1: i + len(letters)]
                    break
        i += 1
    return out


def _page_glyphs(page: pymupdf.Page, cache: dict | None = None) -> tuple[list[list[Glyph | None]], list[list[Glyph]]]:
    """The page's letters as MuPDF's lines: horizontal lines (None marks a space) and the rest (locked)."""
    cache = {} if cache is None else cache
    flat, locked = [], []
    data = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_PRESERVE_WHITESPACE
                         | pymupdf.TEXT_MEDIABOX_CLIP)
    for block in data["blocks"]:
        for line in block.get("lines", []):
            horizontal = abs(line["dir"][0] - 1) < 1e-3 and abs(line["dir"][1]) < 1e-3
            out = []
            for span in line["spans"]:
                style = Style(base_name(span["font"]), round(span["size"], 2), span["color"])
                for ch in span["chars"]:
                    if ch["c"].isspace():
                        out.append(None)
                    else:
                        out.append(Glyph(ch["c"], ch["origin"][0], ch["origin"][1], ch["bbox"][2], tuple(ch["bbox"]), style))
            if any(out):
                (flat if horizontal else locked).append(_join_ligatures(out, page.parent, cache))
    return flat, locked


def _split_words(items: list[Glyph | None], size: float) -> list[Word]:
    words, cur, prev = [], [], None
    for g in items:
        if g is None or (prev is not None and g.x - prev.x1 > 0.15 * size):
            if cur:
                words.append(Word(cur))
            cur = []
        if g is not None:
            cur.append(g)
        prev = g if g is not None else prev
    if cur:
        words.append(Word(cur))
    return words


def page_segments(page: pymupdf.Page, number: int, cache: dict | None = None) -> list[list[Segment]]:
    """The page as rows of segments, top to bottom, left to right, with alignment and room to grow worked out."""
    lines, locked = _page_glyphs(page, cache)
    rows: list[list[Glyph | None]] = []
    keyed = []
    for line in lines:
        gs = [g for g in line if g]
        keyed.append((statistics.median(g.y for g in gs), max(g.style.size for g in gs), line))
    keyed.sort(key=lambda k: k[0])
    bases: list[float] = []
    for base, size, line in keyed:      # lines on one baseline (MuPDF splits a row at wide gaps) become one row
        if rows and abs(base - bases[-1]) < 0.25 * size:
            rows[-1].extend([None] + line)
        else:
            rows.append(list(line))
            bases.append(base)
    out: list[list[Segment]] = []
    for row, base in zip(rows, bases):
        gs = sorted((g for g in row if g), key=lambda g: g.x)
        size = statistics.median(g.style.size for g in gs)
        segs, cur = [], [gs[0]]
        for prev, g in zip(gs, gs[1:]):
            if g.x - prev.x1 > 1.0 * max(prev.style.size, g.style.size):
                segs.append(cur)
                cur = []
            cur.append(g)
        segs.append(cur)
        out.append([Segment(number, _split_words(_with_spaces(seg, row), size), size, base) for seg in segs])
    for line in locked:
        gs = [g for g in line if g]
        out.append([Segment(number, [Word(gs)], gs[0].style.size, gs[0].y, locked=True)])
    _classify(out, page.rect)
    return out


def _with_spaces(sorted_glyphs: list[Glyph], row: list[Glyph | None]) -> list[Glyph | None]:
    """The segment's letters in order with a None wherever MuPDF had a space between two of them."""
    after_space = set()
    prev = None
    for g in row:
        if g is None:
            prev = "space"
        else:
            if prev == "space":
                after_space.add(id(g))
            prev = g
    out: list[Glyph | None] = []
    for i, g in enumerate(sorted_glyphs):
        if i and id(g) in after_space:
            out.append(None)
        out.append(g)
    return out


def _classify(rows: list[list[Segment]], rect: pymupdf.Rect) -> None:
    """Alignment and limits for every segment on a page."""
    segs = [s for row in rows for s in row if not s.locked]
    if not segs:
        return
    left, right = min(s.x0 for s in segs), max(s.x1 for s in segs)
    centre = (left + right) / 2
    text_rows = [row for row in rows if not row[0].locked]

    def continues(i: int) -> bool:      # does the next row carry on this row's paragraph?
        if i + 1 >= len(text_rows) or len(text_rows[i]) != 1:
            return False
        a, b = text_rows[i][0], text_rows[i + 1][0]
        return (abs(b.x0 - a.x0) <= 30 and 0.9 * a.size <= b.baseline - a.baseline <= 1.9 * a.size
                and abs(a.size - b.size) <= 0.5)

    def lined_up(s: Segment) -> str | None:
        """How the text just above and below lines up with this segment: the edge they share, if any."""
        votes = Counter()
        near = [t for t in segs if t is not s and 0.25 * s.size <= abs(t.baseline - s.baseline) <= 4 * s.size
                and t.x1 >= s.x0 and t.x0 <= s.x1]
        for side in (-1, 1):            # the two nearest lines above and below: a grid lines up by accident further off
            lines = sorted({round(t.baseline, 1) for t in near if (t.baseline - s.baseline) * side > 0},
                           key=lambda b: abs(b - s.baseline))[:2]
            for b in lines:
                t = min((t for t in near if round(t.baseline, 1) == b),
                        key=lambda t: abs((t.x0 + t.x1) - (s.x0 + s.x1)))
                dl, dr = abs(t.x0 - s.x0), abs(t.x1 - s.x1)
                if dl < 1.6 and dr < 1.6:
                    continue               # as wide as this one: says nothing
                # edges within 1.6pt (italics overhang), a centre within 2.5pt (a footnote mark shifts it) but only
                # when both edges clearly differ: lines of similar width share a centre by accident
                fits = {"left": dl / 1.6, "right": dr / 1.6,
                        "center": abs((t.x0 + t.x1) - (s.x0 + s.x1)) / 2 / 2.5 if min(dl, dr) > 2.5 else 9}
                best = min(fits, key=fits.get)
                if fits[best] < 1:
                    votes[best] += 1
        if not votes:
            return None
        top = max(votes.values())
        tied = [k for k, n in votes.items() if n == top]
        return tied[0] if len(tied) == 1 else ("center" if "center" in tied else None)

    edges = Counter(round(row[0].x1 * 2) / 2 for i, row in enumerate(text_rows) if continues(i))
    justified_edges = [e for e, n in edges.items() if n >= 2]
    for i, row in enumerate(text_rows):
        for j, s in enumerate(row):
            column_right = max([t.x1 for t in segs if abs(t.x0 - s.x0) <= 30] + [s.x1])
            s.left_limit = row[j - 1].x1 + s.size if j else left
            s.right_limit = row[j + 1].x0 - s.size if j + 1 < len(row) else max(column_right, s.x1)
            if len(row) > 1:
                # columns on one row (a job title and its dates, a grid of authors): the text above and below shows
                # how each is aligned; failing that, the last is right-aligned and the others left-aligned
                s.align = lined_up(s) or ("right" if j == len(row) - 1 else "left")
            elif continues(i) and len(s.words) >= 3 and any(abs(s.x1 - e) <= 0.6 for e in justified_edges):
                s.align = "justify"
            elif abs((s.x0 + s.x1) / 2 - centre) < 2 and s.x0 > left + 4:
                s.align = "center"
                s.left_limit, s.right_limit = left, right
            elif lined_up(s) in ("center", "right"):     # e.g. a heading centred over its own column
                s.align = lined_up(s)


# ---------------------------------------------------------------- the document
@dataclass
class _Typeset:
    """One letter to write."""
    x: float
    y: float
    c: str
    style: Style
    font: pymupdf.Font
    resource: str | None = None   # written in the page's own font: the page's resource name for that font object


@dataclass
class _Plan:
    remove: list[Glyph] = field(default_factory=list)
    write: list[_Typeset] = field(default_factory=list)
    ordered: bool = False       # write is already in reading order (a re-wrapped paragraph: line by line)
    segments: list = field(default_factory=list)   # the lines it re-sets, when more than one
    shift: object = None        # a _Shift: what is below a re-wrapped paragraph moves to make room
    own: list = field(default_factory=list)   # letters written by code in the page's own fonts (checked by glyph)
    added_lines: int = 0        # lines a re-wrapped paragraph gained (fewer when negative)
    chrome: bool = False        # a page number renumbered because a page was inserted


class PdfText:
    """A PDF opened for editing: its text (as matched by edits) and its layout."""

    def __init__(self, path: Path):
        self.path = path
        self.doc = pymupdf.open(str(path))
        if self.doc.is_encrypted:
            raise CantKeepLayout("The PDF is encrypted, so its text can't be changed in place.")
        self._metrics: dict = {}
        self.pages = [page_segments(page, n, self._metrics) for n, page in enumerate(self.doc)]
        parts, offset = [], 0
        self.segments: list[Segment] = []
        for n, rows in enumerate(self.pages):
            if n:
                parts.append("\n\n")
                offset += 2
            for r, row in enumerate(rows):
                for k, seg in enumerate(row):
                    if r or k:
                        parts.append("\n")
                        offset += 1
                    seg.start = offset
                    parts.append(seg.text)
                    offset += len(seg.text)
                    self.segments.append(seg)
        self.text = "".join(parts)
        self._fonts: dict[tuple[str, str], pymupdf.Font | None] = {}
        self._embedded: dict[str, pymupdf.Font | None] = {}
        self._used = defaultdict(set)              # characters the document sets in each font
        for seg in self.segments:
            for w in seg.words:
                for g in w.glyphs:
                    self._used[g.style.font].add(g.c)
        self._spacing: dict[str, tuple[dict, float, float]] = {}

    def close(self):
        self.doc.close()

    def segment_at(self, offset: int) -> Segment | None:
        for seg in self.segments:
            if seg.start <= offset < seg.start + len(seg.text):
                return seg
        return None

    # ------------------------------------------------------------ fonts for a style
    def _embedded_font(self, name: str) -> pymupdf.Font | None:
        if name not in self._embedded:
            self._embedded[name] = None
            for page in self.doc:
                for xref, ext, _type, basefont, *_ in page.get_fonts(full=True):
                    if base_name(basefont) != name or ext in ("n/a", ""):
                        continue
                    try:
                        buffer = self.doc.extract_font(xref)[3]
                        if buffer:
                            self._embedded[name] = pymupdf.Font(fontbuffer=buffer)
                            return self._embedded[name]
                    except Exception:   # a font MuPDF can't load on its own: look for a complete copy instead
                        continue
        return self._embedded[name]

    def font_for(self, style: Style, text: str) -> pymupdf.Font:
        """The font to write `text` in `style`: the embedded one when it surely has the letters, else a complete copy."""
        cached = self._fonts.get((style.font, text))
        if cached:
            return cached
        embedded = self._embedded_font(style.font)
        if embedded and set(text) <= self._used[style.font] and _has(embedded, text):
            font = embedded
        else:
            font = None
            path = find_font_file(style.font)
            if path:
                try:
                    candidate = _file_font(path)
                    font = candidate if _has(candidate, text) else None
                except Exception:
                    font = None
            if font is None and font_key(style.font) in BASE14:
                candidate = pymupdf.Font(BASE14[font_key(style.font)])
                font = candidate if _has(candidate, text) else None
        if font is None:
            missing = "".join(sorted({c for c in text if c not in self._used[style.font]}))
            raise MissingLetters(
                f'The PDF sets this text in the font "{style.font}", and no complete copy of that font is available'
                + (f' for the letters "{missing}"' if missing else "")
                + ". (A copy can be added to data/fonts.)", missing)
        self._fonts[(style.font, text)] = font
        return font

    def spacing(self, style: Style) -> tuple[dict, float, float]:
        """How the document spaces letters in this font: (kerning by letter pair, letter-spacing, horizontal scale),
        all in em, learned from its own words."""
        if style.font in self._spacing:
            return self._spacing[style.font]
        pairs, track, scale = defaultdict(list), [], []
        for seg in self.segments:
            for w in seg.words:
                for a, b in zip(w.glyphs, w.glyphs[1:]):
                    if a.style.font != style.font or a.style != b.style or abs(a.y - b.y) > 0.01:
                        continue
                    try:
                        font = self.font_for(a.style, a.c)
                    except CantKeepLayout:
                        continue
                    advance = font.text_length(a.c, a.style.size)
                    if advance <= 0:
                        continue
                    measured = a.x1 - a.x
                    if measured > 0:
                        scale.append(measured / advance)
                    gap = (b.x - a.x - advance) / a.style.size
                    pairs[(a.c, b.c)].append(gap)
                    track.append(gap)
        kern = {p: statistics.median(v) for p, v in pairs.items()}
        tracking = statistics.median(track) if track else 0.0
        if abs(tracking) < 0.002:
            tracking = 0.0
        result = (kern, tracking, statistics.median(scale) if scale else 1.0)
        self._spacing[style.font] = result
        return result

    def hyphenates(self) -> bool:
        """Whether the document breaks words at line ends (TeX does; most word processors don't by default):
        only then may a re-wrap break one."""
        if not hasattr(self, "_hyphenates"):
            self._hyphenates = any(
                a.page == b.page and len(a.words[-1].text) > 1 and a.words[-1].text.endswith("-")
                and line_break_hyphen(a.words[-1].text, b.words[0].text, self.text)
                for a, b in zip(self.segments, self.segments[1:]) if not a.locked and not b.locked)
        return self._hyphenates

    def sentence_space(self) -> float:
        """How much wider than the other gaps on its line the gap after a sentence is (TeX sets it wider; most
        word processors don't): 1.0 when the document doesn't."""
        if not hasattr(self, "_sentence_space"):
            ratios = []
            for seg in self.segments:
                gaps = [b.x - a.end for a, b in zip(seg.words, seg.words[1:])]
                if len(gaps) < 4:
                    continue
                usual = statistics.median(gaps)
                for (a, b), g in zip(zip(seg.words, seg.words[1:]), gaps):
                    if usual > 0 and a.text.endswith((".", "!", "?")) and b.text[:1].isupper():
                        ratios.append(g / usual)
            ratio = statistics.median(ratios) if len(ratios) >= 2 else 1.0
            self._sentence_space = ratio if ratio > 1.1 else 1.0
        return self._sentence_space

    def natural_space(self, style: Style) -> float:
        """The usual gap between words in this font and size, from lines that aren't stretched to a width. Kept per
        style: the line breaker asks for it at every gap it tries."""
        cache = self.__dict__.setdefault("_natural_spaces", {})
        if style not in cache:
            cache[style] = self._natural_space(style)
        return cache[style]

    def _natural_space(self, style: Style) -> float:
        def gaps(only_unjustified: bool):
            for seg in self.segments:
                if seg.locked or (only_unjustified and seg.align == "justify"):
                    continue
                for a, b in zip(seg.words, seg.words[1:]):
                    if a.glyphs[-1].style == style:
                        yield b.x - a.end
        found = list(gaps(True)) or list(gaps(False))
        return statistics.median(found) if found else 0.333 * style.size

    # ------------------------------------------------------------ typesetting
    def typeset(self, chars: list[tuple[str, Style, float]]) -> tuple[list[tuple[float, float, str, Style, pymupdf.Font]], float]:
        """Letters of one new word as (x from the word's start, baseline, letter, style, font), and its width."""
        runs: list[list] = []
        for c, style, y in chars:
            if runs and runs[-1][0] == style and runs[-1][2] == y:
                runs[-1][1] += c
            else:
                runs.append([style, c, y])
        out, x = [], 0.0
        for style, text, y in runs:
            if any(lig in self._used[style.font] for _, lig in LIGATURES):
                for letters, lig in LIGATURES:
                    if lig in self._used[style.font]:
                        text = text.replace(letters, lig)
            font = self.font_for(style, text)
            kern, tracking, scale = self.spacing(style)
            if abs(scale - 1) > 0.04:
                raise CantKeepLayout(f'The PDF stretches or squeezes the letters of "{style.font}", which an edit '
                                     "can't reproduce exactly.")
            prev = None
            for c in text:
                if prev is not None:
                    x += kern.get((prev, c), tracking) * style.size
                out.append((x, y, c, style, font))
                x += font.text_length(c, style.size)
                prev = c
        return out, x


BULLETS = {"–", "—", "-", "•", "·", "◦", "▪", "*", "○", "■", "►", "‣", "⁃"}


def _replace_entries(entries: list[tuple], ranges: list[tuple[int, int, str]]) -> None:
    """Put each new text in place of entries[a:b], every new word taking the style (and baseline) of the text it
    replaces. An entry is (char, style, glyph, old word index, baseline); a space or line break has style None."""
    for a, b, new in sorted(ranges, reverse=True):
        old = entries[a:b]
        styled = [(e[1], e[3], e[4]) for e in old if e[1] is not None]
        if not styled:
            raise CantKeepLayout("The text to replace has no letters in the PDF.")
        styles = list(dict.fromkeys((s, y) for s, _, y in styled))
        new_words = new.split()
        if len(styles) == 1:
            per_word = [styles[0]] * len(new_words)
        else:
            old_words = list(dict.fromkeys(wi for _, wi, _ in styled))
            word_styles = [list(dict.fromkeys((s, y) for s, wi2, y in styled if wi2 == wi)) for wi in old_words]
            if len(old_words) != len(new_words) or any(len(ws) != 1 for ws in word_styles):
                raise CantKeepLayout("The text being replaced mixes styles (for example bold and regular), so it isn't "
                                     "clear which style the new text should take. Replace each part separately.")
            per_word = [ws[0] for ws in word_styles]
        repl, k = [], 0
        for token in re.split(r"(\s+)", new):
            if not token:
                continue
            if token.isspace():
                repl.append((" ", None, None, None, None))
            else:
                style, y = per_word[min(k, len(per_word) - 1)]
                repl.extend((ch, style, None, None, y) for ch in token)
                k += 1
        entries[a:b] = repl


def _plan_segment(pdf: PdfText, seg: Segment, ranges: list[tuple[int, int, str]]) -> _Plan:
    """Re-set one segment with the replacements (local start, end, new text) applied."""
    if seg.locked:
        raise CantKeepLayout("The text is rotated or vertical in the PDF, which an edit can't re-set.")
    entries: list[tuple] = []        # (char, style, glyph, old word index, baseline); a space is (" ", None, ...)
    for wi, w in enumerate(seg.words):
        if wi:
            entries.append((" ", None, None, None, None))
        for g in w.glyphs:
            for ch in g.text:
                entries.append((ch, g.style, g, wi, g.y))
    _replace_entries(entries, ranges)

    # the new words: an old word kept whole, or letters to set
    words, cur = [], []
    for e in entries + [(" ", None, None, None, None)]:
        if e[1] is None:
            if cur:
                words.append(cur)
            cur = []
        else:
            cur.append(e)
    laid = []      # (old word index or None, glyph offsets or typeset letters, width)
    for w in words:
        wis = {e[3] for e in w}
        glyphs = list(dict.fromkeys(e[2] for e in w))
        if len(wis) == 1 and None not in wis and None not in glyphs and glyphs == seg.words[w[0][3]].glyphs \
                and "".join(e[0] for e in w) == seg.words[w[0][3]].text:
            old = seg.words[w[0][3]]
            laid.append((w[0][3], None, old.end - old.x))
        else:
            letters, width = pdf.typeset([(e[0], e[1], e[4]) for e in w])
            laid.append((None, letters, width))
    if not laid:
        if len(_paragraph(pdf, seg)) > 1:
            raise NeedsRewrap("The edit empties a line inside a paragraph.")
        return _Plan(remove=[g for w in seg.words for g in w.glyphs])

    def gap(i: int) -> float:       # between laid[i-1] and laid[i]
        a, b = laid[i - 1][0], laid[i][0]
        if a is not None and b is not None and b == a + 1:
            return seg.words[b].x - seg.words[a].end
        last = seg.words[a].glyphs[-1].style if a is not None else laid[i - 1][1][-1][3]
        return pdf.natural_space(last)

    n = len(laid)
    xs = [0.0] * n
    widths = [w for _, _, w in laid]
    if seg.align == "justify" and n >= 2:
        first = 0
        if laid[0][0] == 0 and seg.words[0].text in BULLETS:     # a list bullet keeps its own fixed gap
            xs[0], first = seg.words[0].x, 1
            start = seg.words[0].end + gap(1) if laid[1][0] == 1 else seg.words[0].end + (seg.words[1].x - seg.words[0].end)
        else:
            start = seg.x0
        if n - first < 2:
            raise NeedsRewrap("The line would be left with a single word stretched across it; the paragraph "
                                 "would need re-wrapping.")
        # every gap keeps its share (a wider one after a full stop stays wider); all of them stretch or shrink
        # together so the line still ends on the paragraph's edge
        old_gaps = [b.x - a.end for a, b in zip(seg.words[first:], seg.words[first + 1:])]
        typical = statistics.median(old_gaps) if old_gaps else pdf.natural_space(seg.words[-1].glyphs[-1].style)
        base = []
        for i in range(first + 1, n):
            a, b = laid[i - 1][0], laid[i][0]
            base.append(seg.words[b].x - seg.words[a].end if a is not None and b is not None and b == a + 1 else typical)
        factor = (seg.x1 - start - sum(widths[first:])) / sum(base)
        natural = pdf.natural_space(seg.words[-1].glyphs[-1].style)
        # as tight or loose as TeX or a word processor would set a line, or as the line already was
        if not min(0.66 * natural, 0.95 * typical) <= typical * factor <= max(1.45 * natural, 1.1 * typical):
            raise NeedsRewrap("The new text doesn't fit its justified line without the word spacing looking "
                                 "visibly different; the paragraph would need re-wrapping.")
        x = start
        for i in range(first, n):
            xs[i] = x
            x += widths[i] + (base[i - first] * factor if i + 1 < n else 0)
    elif seg.align == "right":
        keep = 0
        while keep < n and laid[n - 1 - keep][0] == len(seg.words) - 1 - keep:
            keep += 1
        end = seg.x1
        for i in range(n - 1, -1, -1):
            if n - 1 - i < keep:
                xs[i] = seg.words[laid[i][0]].x
            else:
                xs[i] = end - widths[i]
            end = xs[i] - (gap(i) if i else 0)
    else:
        keep = 0
        while keep < n and laid[keep][0] == keep:
            keep += 1
        x = seg.x0
        for i in range(n):
            if i < keep:
                xs[i] = seg.words[i].x
            else:
                xs[i] = x if i == 0 else xs[i - 1] + widths[i - 1] + gap(i)
            x = xs[i]
        if seg.align == "center":
            total = xs[-1] + widths[-1] - xs[0]
            shift = (seg.x0 + seg.x1) / 2 - total / 2 - xs[0]
            xs = [x + shift for x in xs]
    lo, hi = xs[0], xs[-1] + widths[-1]
    if lo < seg.left_limit - 0.01 or hi > seg.right_limit + 0.01:
        raise NeedsRewrap("The new text is too long for its line in the PDF; the text after it would have to "
                             "move to the next line.")
    if seg.align == "left" and hi < seg.x1 - 0.01:
        # a ragged line got shorter: if the next line's first word now fits after it, the paragraph is re-wrapped,
        # as a word processor would pull that word up
        para = _paragraph(pdf, seg)
        at = next((i for i, s in enumerate(para) if s is seg), len(para) - 1)
        if at + 1 < len(para):
            nxt = para[at + 1].words[0]
            if hi + pdf.natural_space(nxt.glyphs[0].style) + _width(nxt) <= max(s.x1 for s in para) + 0.01:
                raise NeedsRewrap("The line got shorter, and the next line's first word now fits on it.")

    plan, used = _Plan(), set()
    written = [not (wi is not None and abs(x - seg.words[wi].x) < 0.01) for (wi, _, _), x in zip(laid, xs)]
    for i, ((wi, letters, _), x) in enumerate(zip(laid, xs)):
        if i and (written[i - 1] or written[i]):
            # a real space between a written word and its neighbour (it has no ink), so copied and searched text
            # reads the words apart; written in line with the letters, so text readers meet it between the words
            style = laid[i - 1][1][-1][3] if laid[i - 1][1] else seg.words[laid[i - 1][0]].glyphs[-1].style
            plan.write.append(_Typeset(xs[i - 1] + widths[i - 1], seg.baseline, " ", style, _space_font()))
        if wi is not None:
            old = seg.words[wi]
            if abs(x - old.x) < 0.01:
                used.add(wi)
                continue
            used.add(wi)
            plan.remove.extend(old.glyphs)
            for g in old.glyphs:
                plan.write.append(_Typeset(g.x + x - old.x, g.y, g.c, g.style, pdf.font_for(g.style, g.c)))
        else:
            plan.write.extend(_Typeset(x + dx, y, c, style, font) for dx, y, c, style, font in letters)
    for wi, w in enumerate(seg.words):
        if wi not in used:
            plan.remove.extend(w.glyphs)
    for t in plan.write:
        if t.c == "\ufffd":
            raise CantKeepLayout("Some letters on that line can't be read from the PDF, so the line can't be re-set.")
    return plan


# ---------------------------------------------------------------- moving what is below a paragraph
@dataclass
class _Shift:
    """Everything on the page in `region` (text, drawings, images, links) moves down by dy (up when negative).
    When the page can't hold it all, what is in `overflow` (the part of the page below `region`) goes to a new page
    inserted after it, its top at `to_top` (where the page's body starts), and `moved_out` are its lines."""
    region: pymupdf.Rect
    dy: float
    overflow: pymupdf.Rect | None = None
    to_top: float = 0.0
    moved_out: list = field(default_factory=list)
    # the page's running headers and footers, on the new page too: bands drawn as they are (less the boxes of page
    # numbers, which are written again with the new page's number)
    chrome_bands: list = field(default_factory=list)
    chrome_skip: list = field(default_factory=list)
    chrome_write: list = field(default_factory=list)
    # a paragraph that runs past the page goes on at the top of the new page: its lines there (`split`, letters
    # placed on the new page), where the first of them sits, and (for the text check) its span and those lines' text
    split: list = field(default_factory=list)
    split_base: float = 0.0
    body_top: float = 0.0
    page: int = 0
    para_span: tuple = (0, 0)
    split_text: str = ""
    split_own: list = field(default_factory=list)   # of `split`, the letters written in the document's own fonts

    @property
    def adds_page(self) -> bool:
        return self.overflow is not None or bool(self.split)


def _repeated(pdf: PdfText) -> set[int]:
    """Segments that repeat on several pages at the same height (running headers and footers, page numbers)."""
    if not hasattr(pdf, "_repeated_ids"):
        keyed = defaultdict(set)
        for s in pdf.segments:
            keyed[(round(s.baseline), re.sub(r"\d+", "#", s.text))].add(s.page)
        pdf._repeated_ids = {id(s) for s in pdf.segments
                             if len(keyed[(round(s.baseline), re.sub(r"\d+", "#", s.text))]) >= 2}
    return pdf._repeated_ids


def _body_bottom_limit(pdf: PdfText) -> float:
    """How low a page's body text may reach: as low as it reaches on any page; with one page, as far from the
    bottom as the text starts from the top."""
    repeated = _repeated(pdf)
    body = [s for s in pdf.segments if id(s) not in repeated]
    if len(pdf.doc) > 1:
        return max(s.bottom for s in body)
    return pdf.doc[0].rect.height - min(s.top for s in body)


def _push(pdf: PdfText, para: list[Segment], left: float, right: float, dy: float,
          split: tuple[int, float, float] | None = None) -> _Shift | None:
    """Make room for a paragraph that grew (dy > 0) or close the gap of one that shrank (dy < 0) by moving
    everything below it on the page, as a word processor would. None when nothing is below it.
    CantKeepLayout when that can't be done without changing the rest of the page.

    split = (lines, ascent, step): the paragraph runs past the page and goes on with that many lines at the top of
    a new page; everything below it goes there too, after those lines, as far below them as it was below the
    paragraph's last line."""
    n = para[0].page
    page = pdf.doc[n]
    width = page.rect.width
    mine = {id(s) for s in para}
    repeated = _repeated(pdf)
    top, bottom = min(s.top for s in para), max(s.bottom for s in para)
    others = [s for row in pdf.pages[n] for s in row if id(s) not in mine]
    footer_top = min([s.top for s in others if id(s) in repeated and s.top >= bottom] + [page.rect.height])
    below = [s for s in others if s.top >= bottom - 0.5 and s.bottom <= footer_top + 0.5 and id(s) not in repeated
             and not s.locked]
    body_left = min([s.x0 for s in below] + [left]) - 1
    body_right = max([s.x1 for s in below] + [right]) + 1
    if any(s.top < bottom - 0.5 and s.bottom > top + 0.5 and (s.x1 < left - 1 or s.x0 > right + 1)
           and s.x1 > body_left and s.x0 < body_right and id(s) not in repeated for s in others):
        raise CantKeepLayout("The page has text beside the paragraph (columns), so what is below it can't be moved "
                             "to make room.")
    # a drawing as far as its ink reaches: a stroke spreads half its width past the path
    drawings = [pymupdf.Rect(d["rect"]) + (-(d.get("width") or 0) / 2, -(d.get("width") or 0) / 2,
                                         (d.get("width") or 0) / 2, (d.get("width") or 0) / 2)
                for d in page.get_drawings()]
    images = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    inside = [r for r in drawings + images if r.y0 >= bottom and r.y1 <= footer_top
              and r.x1 > body_left and r.x0 < body_right]
    lowest = max([s.bottom for s in below] + [r.y1 for r in inside] + [bottom])
    limit = _body_bottom_limit(pdf)
    body_top = min(s.top for s in pdf.segments if s.page == n and id(s) not in repeated)
    cut = None
    if split is not None:
        lines_on, ascent, step = split
        split_base = body_top + ascent
        off = split_base + step * (lines_on - 1) - para[-1].baseline   # the paragraph's last line, there
        if lowest + off > limit + 0.5:
            raise NoRoom("The new text would push more than a page onto new pages; a shorter wording may fit.")
        if lowest <= bottom:
            return _Shift(pymupdf.Rect(0, bottom, width, bottom), 0.0, split_base=split_base, body_top=body_top,
                          page=n)
        cut = bottom + 0.05                # all of what is below goes
    elif dy > 0 and bottom + dy > limit + 0.5:
        raise NoRoom("The paragraph would run past the bottom of its page, so it can't take that much more text "
                     "there; a shorter wording may fit.")
    if lowest <= bottom:
        return None
    # what is below doesn't fit any more: the part that doesn't goes to a new page after this one
    if split is None and dy > 0 and lowest + dy > limit + 0.5:
        units = [(s.top, s.bottom) for s in below] + [(r.y0, r.y1) for r in inside]
        cut = _overflow_cut(units, bottom, dy, limit)
        body_top = min(s.top for s in pdf.segments if s.page == n and id(s) not in repeated)
        if lowest - cut > limit - body_top:
            raise NoRoom("The new text would push more than a page of what follows it onto new pages; a shorter "
                         "wording may fit.")
    # 2pt to spare at the sides: a rule's stroke reaches past its path, and a drawing only counts as inside (and so is
    # taken away before it is drawn again, moved) when all of it is
    x0 = max(0, min([body_left] + [r.x0 for r in inside]) - 2)
    x1 = min(width, max([body_right] + [r.x1 for r in inside]) + 2)
    region = pymupdf.Rect(x0, bottom + 0.05, x1, lowest + 0.5 if cut is None else cut)
    overflow = None if cut is None else pymupdf.Rect(x0, cut, x1, lowest + 0.5)
    whole = region if overflow is None else pymupdf.Rect(x0, region.y0, x1, overflow.y1)
    for r in drawings + images:
        if not r.intersects(whole) and r.width > 0 and r.height > 0:
            continue
        crosses = (r.y0 < whole.y0 < r.y1 - 0.01) or (r.y0 + 0.01 < whole.y1 < r.y1)
        if crosses and not (r.y0 <= whole.y0 and r.y1 >= whole.y1):   # a background or frame around it stays
            raise CantKeepLayout("A drawing or image on the page runs across the text below the paragraph, so it "
                                 "can't be moved to make room.")
        if overflow is not None and r.y0 <= whole.y0 and r.y1 >= whole.y1:
            raise CantKeepLayout("A background or frame runs behind the text below the paragraph, so that text can't "
                                 "be moved onto a new page.")
    for s in others:
        box = pymupdf.Rect(s.x0, s.top, s.x1, s.bottom)
        if s.locked and box.intersects(whole):
            raise CantKeepLayout("Rotated text runs into what is below the paragraph, so it can't be moved.")
        if id(s) not in repeated and s.top < whole.y0 < s.bottom and box.intersects(whole):
            raise CantKeepLayout("Text on the page overlaps the paragraph's last line, so what is below it can't be "
                                 "moved.")
    for annot in page.annots() or []:
        if annot.rect.intersects(whole):
            raise CantKeepLayout("The page has annotations or form fields below the paragraph, which can't be moved.")
    if overflow is None:
        return _Shift(region, dy)
    moved_out = [s for s in below if s.top >= overflow.y0 - 0.01]
    if split is not None:                   # below the paragraph's last line on the new page, as it was here
        return _Shift(region, 0.0, overflow, overflow.y0 + off, moved_out, split_base=split_base,
                      body_top=body_top, page=n)
    return _Shift(region, dy, overflow, body_top, moved_out, body_top=body_top, page=n)


def _overflow_cut(units: list[tuple[float, float]], bottom: float, dy: float, limit: float) -> float:
    """Where to cut what is below a grown paragraph: the lowest place between lines (never through a line, drawing
    or image) above which everything still fits on the page once moved down by dy. Below it goes to a new page."""
    best = bottom + 0.05                   # nothing fits: all of it goes
    for top, _ in sorted(units):
        c = top - 0.1
        if c <= bottom + 0.05 or any(t < c < b for t, b in units):
            continue
        above = max([b for t, b in units if b <= c] + [bottom])
        if above + dy <= limit + 0.5:
            best = max(best, c)
    return best


# ---------------------------------------------------------------- re-wrapping a paragraph
SHRINK, STRETCH = 0.85, 2.2      # how much tighter or looser than its usual word gap a justified line may be set
JOIN = "join"                    # a line break inside a word (it was hyphenated there): no space when re-wrapped
DROP = "\x00"                    # a letter not written again: the hyphen of a word broken at a line end


# first parts of compounds that keep their hyphen ("well-known", "real-time") when nothing in the document says otherwise
COMPOUND_FIRST = {"self", "non", "multi", "pre", "post", "co", "re", "anti", "semi", "well", "high", "low", "long",
                  "short", "real", "so", "full", "half", "state", "end", "cross", "open", "two", "three", "one", "first",
                  "second", "third", "top", "user", "data", "time", "fine", "large", "small", "deep", "fast", "read",
                  "write", "follow", "up", "out", "in", "on", "off", "under", "over", "sub", "inter", "mid", "ex",
                  "all", "built", "hand", "life", "world", "day", "year", "month", "week", "age", "best", "most"}


@functools.cache
def _hyphenator():
    import pyphen
    return pyphen.Pyphen(lang="en_US")


def line_break_hyphen(left: str, right: str, text: str) -> bool:
    """Whether the hyphen ending `left` at a line end only split a word for the line break ("exam-" + "ple"), as
    opposed to belonging to it ("well-" + "known"). The document is asked first: the hyphenated form elsewhere in a
    line means a real hyphen, the joined word elsewhere means a break. Failing that, a common compound start keeps its
    hyphen, and otherwise a hyphenation dictionary says whether the word may be broken there."""
    if left.endswith("\xad"):
        return True
    a = re.search(r"[A-Za-z]+$", left[:-1])
    b = re.match(r"[a-z]+", right)
    if not left.endswith("-") or not a or not b:
        return False                      # "2026-", "Phelan-" + "McDermid": not a broken word
    a, b = a.group(0), b.group(0)
    if f"{a}-{b}" in text:                # the hyphenated form within a line
        return False
    if re.search(rf"(?<![A-Za-z]){a}{b}(?![a-z])", text, re.I):
        return True
    if a.lower() in COMPOUND_FIRST:
        return False
    return len(a) in _hyphenator().positions((a + b).lower())


def _text_rows(pdf: PdfText, page: int) -> list[list[Segment]]:
    return [row for row in pdf.pages[page] if not row[0].locked]


def _width(w: Word) -> float:
    return w.end - w.x


def _paragraph(pdf: PdfText, seg: Segment) -> list[Segment]:
    """The lines of the paragraph `seg` is in, top to bottom. Lines belong together when they sit one line apart at
    a steady spacing, share a size and a left edge (the first may be indented), and the first word of each would
    not have fitted at the end of the line before it (so the break was a wrap, not the end of a paragraph)."""
    rows = _text_rows(pdf, seg.page)
    at = next((i for i, row in enumerate(rows) if row == [seg]), None)
    if at is None:
        return [seg]

    def joins(a: Segment, b: Segment, step: float | None) -> bool:
        if b.locked or abs(a.size - b.size) > 0.5 or b.words[0].text in BULLETS:
            return False
        gap = b.baseline - a.baseline
        if not 0.9 * a.size <= gap <= 1.9 * a.size or (step is not None and abs(gap - step) > 0.6):
            return False
        if abs(b.x0 - a.x0) > 30:
            return False
        # the column's right edge, from the other lines (a line can't show that it was full by itself)
        edge = max((s.x1 for row in rows for s in row if s is not a and abs(s.x0 - a.x0) <= 30), default=None)
        if edge is None or a.x1 > edge + 2:      # 2pt: punctuation may hang past the edge
            # the column's longest line: full only if it reaches as far as any text on the page does
            # (else nothing shows where the measure is: it carries on unless it ends a sentence)
            page_edge = max((s.x1 for row in rows for s in row if s is not a), default=0)
            if page_edge < a.x1 - 2 and a.words[-1].text.endswith((".", "!", "?", ":")):
                return False
            edge = max(edge or 0, a.x1)
        space = pdf.natural_space(a.words[-1].glyphs[-1].style)
        return a.x1 + space + _width(b.words[0]) > edge + 0.5

    lines, step = [seg], None
    i = at
    while i > 0 and len(rows[i - 1]) == 1 and joins(rows[i - 1][0], lines[0], step):
        step = lines[0].baseline - rows[i - 1][0].baseline
        lines.insert(0, rows[i - 1][0])
        i -= 1
    i = at
    while i + 1 < len(rows) and len(rows[i + 1]) == 1 and joins(lines[-1], rows[i + 1][0], step):
        step = rows[i + 1][0].baseline - lines[-1].baseline
        lines.append(rows[i + 1][0])
        i += 1
    return lines


def _room_below(pdf: PdfText, para: list[Segment], left: float, right: float, top: float, bottom: float) -> bool:
    """Nothing on the page (text, drawings, images) in the band a new line of the paragraph would take, and the band
    within the area the document's text uses (its lowest line on any page)."""
    page = pdf.doc[para[0].page]
    if bottom > max(s.bottom for s in pdf.segments) + 0.5:
        return False
    band = pymupdf.Rect(left, top, right, bottom)
    mine = {id(s) for s in para}
    for row in pdf.pages[para[0].page]:
        for s in row:
            if id(s) not in mine and pymupdf.Rect(s.x0, s.top, s.x1, s.bottom).intersects(band):
                return False
    if any(pymupdf.Rect(d["rect"]).intersects(band) for d in page.get_drawings()):
        return False
    return not any(pymupdf.Rect(info["bbox"]).intersects(band) for info in page.get_image_info())


def _plan_paragraph(pdf: PdfText, para: list[Segment], ranges: list[tuple[int, int, str]]) -> _Plan:
    """Re-wrap a paragraph with the replacements (start, end, new text; offsets in the paragraph's text) applied.

    Lines above the first change stay as they are. From there the words are laid out again the way the paragraph is
    set: on the same left edges and baselines, up to its right edge, justified lines still filling the width (word
    gaps within SHRINK..STRETCH of the usual gap) and the last line ragged. Words that don't change keep their own
    letters and spacing; only their place moves. Where the new lines come back in step with the old ones, the rest
    stays untouched. The paragraph may end a line shorter, or take one more line where the space below is empty."""
    if any(s.locked for s in para):
        raise CantKeepLayout("The text is rotated or vertical in the PDF, which an edit can't re-set.")
    text = "\n".join(s.text for s in para)
    if pdf.text[para[0].start: para[0].start + len(text)] != text:
        raise CantKeepLayout("The paragraph's text couldn't be lined up with the PDF's layout.")
    starts = [s.start - para[0].start for s in para]
    first = max(i for i, st in enumerate(starts) if st <= min(a for a, _, _ in ranges))
    lines = para[first:]
    ranges = [(a - starts[first], b - starts[first], new) for a, b, new in ranges]

    # the words from the first changed line on, as entries (char, style, glyph, word index, offset from the baseline)
    old_words: list[tuple[Word, int]] = []          # (word, its line within `lines`)
    entries: list[tuple] = []
    for li, seg in enumerate(lines):
        if li:
            prev = lines[li - 1].words[-1].text
            hyphenated = prev.endswith("-") and len(prev) > 1
            if hyphenated and line_break_hyphen(prev, seg.words[0].text, pdf.text):
                e = entries[-1]                 # the hyphen only split the word: it goes when the word is rejoined
                entries[-1] = (DROP, e[1], e[2], e[3], e[4])
            entries.append(("\n", None, None, JOIN if hyphenated else None, None))
        for wj, w in enumerate(seg.words):
            if wj:
                entries.append((" ", None, None, None, None))
            wi = len(old_words)
            old_words.append((w, li))
            for g in w.glyphs:
                for ch in g.text:
                    entries.append((ch, g.style, g, wi, round(g.y - seg.baseline, 2)))
    _replace_entries(entries, ranges)

    # the words again: an old word kept whole, or letters to set (a word hyphenated across lines is set whole,
    # without its hyphen when that only broke the word: line_break_hyphen)
    words, cur = [], []
    for e in entries + [(" ", None, None, None, None)]:
        if e[1] is None and e[3] != JOIN:
            if cur:
                words.append(cur)
            cur = []
        elif e[1] is not None:
            cur.append(e)
    laid = []      # (old word index or None, typeset letters or None, width, last style)
    word_chars: list[list[tuple]] = []    # each laid word's letters as (char, style, offset from the baseline)
    for w in words:
        wis = {e[3] for e in w}
        glyphs = list(dict.fromkeys(e[2] for e in w))
        old = old_words[w[0][3]][0] if len(wis) == 1 and None not in wis else None
        word_chars.append([(e[0], e[1], e[4]) for e in w if e[0] != DROP])
        if old is not None and None not in glyphs and glyphs == old.glyphs and "".join(e[0] for e in w) == old.text:
            laid.append((w[0][3], None, _width(old), old.glyphs[-1].style))
        else:
            letters, width = pdf.typeset(word_chars[-1])
            laid.append((None, letters, width, w[-1][1]))
    if not laid and first == 0:
        raise CantKeepLayout("The edit would leave the paragraph empty.")

    # the paragraph's measure and how it is set
    justify = len(para) >= 2 and all(s.align == "justify" for s in para[:-1])
    if justify:
        right = statistics.median(s.x1 for s in para[:-1])
    else:
        bounds = [a.x1 + pdf.natural_space(a.words[-1].glyphs[-1].style) + _width(b.words[0]) - 0.01
                  for a, b in zip(para, para[1:])]
        right = max(max(s.x1 for s in para), min([s.right_limit for s in para] + bounds))
    rows = _text_rows(pdf, para[0].page)
    runs_on = justify and rows[-1] == [para[-1]] and para[-1].x1 >= right - 1   # it carries on on the next page
    steps = [b.baseline - a.baseline for a, b in zip(para, para[1:])]
    step = statistics.median(steps) if steps else 1.2 * para[0].size
    later_left = para[-1].x0 if len(para) > 1 else para[0].x0

    def line_left(t: int) -> float:
        return lines[t].x0 if t < len(lines) else later_left

    def line_base(t: int) -> float:
        return lines[t].baseline if t < len(lines) else lines[-1].baseline + step * (t - len(lines) + 1)

    def word_text(k: int) -> str:
        wi, letters = laid[k][0], laid[k][1]
        return old_words[wi][0].text if wi is not None else "".join(c for _, _, c, _, _ in letters)

    def gap_before(k: int) -> float:
        """The gap before laid[k]. A ragged line keeps the gap two words had when they stay neighbours from one old
        line. Otherwise the paragraph's usual gap, wider after a sentence where the document sets it wider; a justified
        line then stretches all its gaps alike."""
        a, b = laid[k - 1][0], laid[k][0]
        if not justify and a is not None and b is not None and b == a + 1 and old_words[a][1] == old_words[b][1]:
            return old_words[b][0].x - old_words[a][0].end
        space = pdf.natural_space(laid[k - 1][3])
        if word_text(k - 1).endswith((".", "!", "?")) and word_text(k)[:1].isupper():
            space *= pdf.sentence_space()
        return space

    bullet = first == 0 and para[0].words[0].text in BULLETS and laid[0][0] == 0 and len(laid) > 1
    # how loose or tight the paragraph's own justified lines are set: re-wrapped lines may be set as they are
    factors = [statistics.median(b.x - a.end for a, b in zip(s.words, s.words[1:]))
               / pdf.natural_space(s.words[0].glyphs[-1].style) for s in para[:-1] if justify and len(s.words) >= 3]
    low, high = (min(factors), max(factors)) if factors else (1.0, 1.0)

    # a word may be broken at a line end (with a hyphen) only where the document itself breaks words, and only where
    # the hyphen will read as a line break (line_break_hyphen: the same rule the text check uses)
    def cuts_of(k: int) -> list[int]:
        if not (justify and pdf.hyphenates()) or (bullet and k < 2):
            return []
        text = "".join(c for c, _, _ in word_chars[k])
        core = re.match(r"[a-z]+", text)
        if not core or len(core.group(0)) < 5:
            return []
        core = core.group(0)
        try:
            pdf.font_for(word_chars[k][0][1], "-")
        except CantKeepLayout:
            return []
        return [p for p in _hyphenator().positions(core) if 2 <= p <= len(core) - 3
                and line_break_hyphen(core[:p] + "-", text[p:], pdf.text)]

    # pieces: (word, from char, to char); a word that may be broken is several pieces glued together
    pieces: list[tuple[int, int, int]] = []
    for k in range(len(laid)):
        bounds = [0] + cuts_of(k) + [len(word_chars[k])]
        pieces += [(k, a, b) for a, b in zip(bounds, bounds[1:])]

    @functools.lru_cache(maxsize=None)
    def piece(k: int, a: int, b: int, hyphen: bool) -> tuple:
        """(old word index or None, typeset letters, width, last style) for part of a word, or the whole word."""
        if a == 0 and b == len(word_chars[k]) and not hyphen:
            return laid[k]
        chars = word_chars[k][a:b]
        chars = chars + [("-", chars[-1][1], chars[-1][2])] if hyphen else chars
        letters, width = pdf.typeset(chars)
        return (None, letters, width, chars[-1][1])

    def p_width(i: int, hyphen: bool = False) -> float:
        return piece(*pieces[i], hyphen)[2]

    def p_gap(i: int) -> float:
        k, a, _ = pieces[i]
        return 0.0 if a else gap_before(k)            # within a word: no gap

    def in_step(i: int, t: int) -> bool:
        """Back in step with the old lines at piece i starting line t: the rest stays as it is."""
        k, a, _ = pieces[i]
        return bool(not a and t and t < len(lines) and laid[k][0] is not None
                    and old_words[laid[k][0]][0] is lines[t].words[0]
                    and [w for w, _, _, _ in laid[k:]] == list(range(laid[k][0], len(old_words))))

    def line_start(t: int, i: int) -> tuple[int, float]:
        """(pieces placed before the line's spacing starts, where the spacing starts)."""
        if bullet and t == 0:              # a list bullet keeps its place and its gap
            return 2, lines[0].words[1].x + p_width(1)
        return 1, line_left(t) + p_width(i)

    def broken(j: int) -> bool:           # does a line ending before piece j end inside a word?
        return 0 < j < len(pieces) and pieces[j][1] > 0

    def natural_end(t: int, i: int, j: int, squeeze: float) -> float:
        lead, x = line_start(t, i)
        x += sum(p_width(m) + squeeze * p_gap(m) for m in range(i + lead, j))
        if broken(j):
            x += p_width(j - 1, True) - p_width(j - 1)
        return x

    def line_gaps(t: int, i: int, j: int) -> list[float]:
        lead = line_start(t, i)[0]
        return [p_gap(m) for m in range(i + lead, j) if p_gap(m) > 0]

    def fits(t: int, i: int, j: int, squeeze: float) -> bool:
        lead = line_start(t, i)[0]
        if j - i < lead:
            return False
        return natural_end(t, i, j, squeeze) <= right + 0.01

    chosen_lines: list[tuple[int, int]] = []          # (first piece, end piece) of each new line
    converged = None
    n = len(pieces)
    if not justify:
        # greedy line filling, as a word processor does it
        i, t = 0, 0
        while i < n:
            if in_step(i, t):
                converged = t
                break
            lead = line_start(t, i)[0]
            if not fits(t, i, i + lead, 1.0):
                raise CantKeepLayout("A word is wider than the paragraph's lines.")
            j = i + lead
            while j < n and fits(t, i, j + 1, 1.0):
                j += 1
            chosen_lines.append((i, j))
            i, t = j, t + 1
    else:
        # all the breaks chosen together, as TeX does: every line's word spacing as close to the paragraph's own
        # as it can be, no more lines than before unless the text needs them, and a word broken only when that
        # spaces the lines clearly better
        target = statistics.median(factors) if factors else 1.0
        lo, hi = min(SHRINK, 0.95 * low), max(STRETCH, 1.1 * high)
        inf = float("inf")
        # Every line past the paragraph's old ones is alike (same left edge, nothing to come back in step with), so
        # they share one state: the search stays as small as the paragraph, however many lines it grows by. How
        # much more the page can take is _push's to decide. Filled from the paragraph's end backwards (no
        # recursion: a paragraph that grows by a page has hundreds of lines).
        top_t = len(lines)
        best_cost: dict[tuple[int, int], float] = {}
        best_next: dict[tuple[int, int], object] = {}     # the next break, or "in step" (the rest stays as it is)
        for i in range(n, -1, -1):
            for t in range(top_t, -1, -1):
                if i == n:
                    best_cost[i, t], best_next[i, t] = 0.0, None
                    continue
                if in_step(i, t):
                    best_cost[i, t], best_next[i, t] = 0.0, "in step"
                    continue
                found, nxt = inf, None
                lead = line_start(t, i)[0]
                for j in range(i + lead, n + 1):
                    if not fits(t, i, j, SHRINK if j < n or runs_on else 1.0):
                        break
                    last_line = j == n and not runs_on
                    gaps = line_gaps(t, i, j)
                    if last_line:
                        cost = 0.0
                    elif not gaps:
                        cost = 5000.0            # a lone word stretched across: only when nothing else works
                    else:
                        f = (right - natural_end(t, i, j, 0.0)) / sum(gaps)
                        if f < lo - 0.01 or f > 1.6 * hi:
                            continue
                        cost = 100 * ((f - target) / target) ** 2
                        if f > hi:               # looser than the paragraph's loosest line: only when nothing
                            cost += 500          # fits better
                    if broken(j):
                        cost += 40 + (60 if i and broken(i) else 0)    # a broken word; two lines running: worse
                    if t >= len(lines):
                        cost += 1000             # a line more than before
                    total_cost = cost + best_cost[j, min(t + 1, top_t)]
                    if total_cost < found:
                        found, nxt = total_cost, j
                best_cost[i, t], best_next[i, t] = found, nxt

        cost, chosen = best_cost[0, 0], []
        i, t = 0, 0
        while cost < inf and i < n:
            nxt = best_next[i, t]
            if nxt == "in step":
                chosen.append("in step")
                break
            chosen.append((i, nxt))
            i, t = nxt, min(t + 1, top_t)
        if cost == inf:
            raise CantKeepLayout("Re-wrapped, the paragraph's lines can't all be set as tightly or loosely as the "
                                 "rest of it.")
        for item in chosen:
            if item == "in step":
                converged = len(chosen_lines)
            else:
                chosen_lines.append(item)

    # each line as the words (or parts of words) it shows: parts of one word on one line are that word again
    out_lines: list[list[tuple]] = []      # per line: (laid index, from char, to char, ends in a hyphen)
    for i, j in chosen_lines:
        items: list[list] = []
        for m in range(i, j):
            k, a, b = pieces[m]
            if items and items[-1][0] == k:
                items[-1][2] = b
            else:
                items.append([k, a, b, False])
        if broken(j):
            items[-1][3] = True
        out_lines.append([tuple(x) for x in items])

    total = len(out_lines) + (len(lines) - converged if converged is not None else 0)
    if runs_on and total != len(lines):
        raise CantKeepLayout("The paragraph carries on to the next page, so its lines can't be re-wrapped here.")
    shift = None
    left = min(line_left(t) for t in range(max(total, len(lines))))
    keep = None                    # when the paragraph runs past its page: how many of its new lines stay there
    if total > len(lines):
        descent = para[-1].bottom - para[-1].baseline
        limit = _body_bottom_limit(pdf)
        if converged is None and line_base(len(out_lines) - 1) + descent > limit + 0.5:
            keep = sum(1 for t in range(len(out_lines)) if line_base(t) + descent <= limit + 0.5)
            if keep < 1:
                raise NoRoom("The paragraph's page has no room for its first changed line; a shorter wording may fit.")
            if out_lines[keep - 1][-1][3]:
                raise CantKeepLayout("Re-wrapped, the paragraph would break a word across the page break.")
            ascent = lines[0].baseline - lines[0].top
            shift = _push(pdf, para, left, right, 0.0, split=(len(out_lines) - keep, ascent, step))
        elif not _room_below(pdf, para, left, right, lines[-1].bottom + 0.1,
                             line_base(total - 1) + 0.35 * para[-1].size + 0.5 * step):
            shift = _push(pdf, para, left, right, step * (total - len(lines)))
    elif total < len(lines):
        try:                               # the gap a shorter paragraph leaves is closed when that can be done
            shift = _push(pdf, para, left, right, -step * (len(lines) - total))
        except CantKeepLayout as e:
            log.info("A re-wrapped paragraph ends a line shorter; the space stays: %s", e)

    plan = _Plan(ordered=True, shift=shift, added_lines=total - len(lines))
    rewritten = lines[:converged] if converged is not None else lines
    for seg in rewritten:
        for w in seg.words:
            plan.remove.extend(w.glyphs)
    last = len(out_lines) - 1
    for t, line in enumerate(out_lines):
        on_next = keep is not None and t >= keep          # this line goes on at the top of the new page
        base = shift.split_base + step * (t - keep) if on_next else line_base(t)
        target = shift.split if on_next else plan.write
        parts = [piece(*item) for item in line]
        gaps = [gap_before(item[0]) for item in line[1:]]
        lead = 2 if bullet and t == 0 else 1             # words placed before the line's spacing starts
        xs = [lines[0].words[0].x, lines[0].words[1].x] if lead == 2 else [line_left(t)]
        x = xs[-1] + parts[lead - 1][2]
        gaps = gaps[lead - 1:]
        stretch = justify and (t < last or converged is not None or runs_on) and len(line) > lead
        if stretch:
            spare = right - x - sum(part[2] for part in parts[lead:])
            factor = spare / sum(gaps)
            if not min(SHRINK, 0.95 * low) - 0.02 <= factor <= 1.6 * max(STRETCH, 1.1 * high) + 0.01:
                raise CantKeepLayout("Re-wrapped, a line of the paragraph would be set visibly looser or tighter "
                                     "than the rest.")
            gaps = [g * factor for g in gaps]
        for part, g in zip(parts[lead:], gaps):
            x += g
            xs.append(x)
            x += part[2]
        for n, (part, x) in enumerate(zip(parts, xs)):
            wi, letters, _, _ = part
            if n:
                prev = parts[n - 1]
                target.append(_Typeset(xs[n - 1] + prev[2], base, " ", prev[3], _space_font()))
            if wi is not None:
                w, li = old_words[wi]
                for g in w.glyphs:
                    target.append(_Typeset(g.x - w.x + x, base + (g.y - lines[li].baseline), g.c, g.style,
                                           pdf.font_for(g.style, g.c)))
            else:
                target.extend(_Typeset(x + dx, base + dy, c, st, font) for dx, dy, c, st, font in letters)
        if on_next and t < last:
            target.append(_Typeset(xs[-1] + parts[-1][2], base, " ", parts[-1][3], _space_font()))   # reads as a
            # space between the lines (split_text, the text check)
    if any(t.c == "�" for t in plan.write + (shift.split if keep is not None else [])):
        raise CantKeepLayout("Some letters in that paragraph can't be read from the PDF, so it can't be re-set.")
    if keep is not None:
        shift.split_text = "".join(t.c for t in shift.split)
        shift.para_span = (rewritten[0].start, rewritten[-1].start + len(rewritten[-1].text))
    plan.segments = rewritten
    return plan


@functools.cache
def _space_font() -> pymupdf.Font:
    return pymupdf.Font("helv")


# ---------------------------------------------------------------- applying and checking
def _rgb(color: int) -> tuple[float, float, float]:
    return ((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255


def _glyph_list(page: pymupdf.Page) -> list[Glyph]:
    lines, locked = _page_glyphs(page)
    return [g for line in lines + locked for g in line if g]


def _redact_rect(g: Glyph) -> pymupdf.Rect:
    """A small box in the middle of the letter: the letter overlaps it, its neighbours don't."""
    x0, y0, x1, y1 = g.bbox
    w, h = max(x1 - x0, 0.2), max(y1 - y0, 0.2)
    return pymupdf.Rect(x0 + 0.35 * w, y0 + 0.4 * h, x1 - 0.35 * w, y1 - 0.4 * h)


def _redact_rects(page: pymupdf.Page, remove: list[Glyph]) -> list[pymupdf.Rect]:
    """Boxes to redact: one per run of neighbouring letters to remove on a line (each redaction box costs MuPDF time
    in proportion to the boxes already there), a thin band through their middles so nothing else is touched. A run
    ends at a letter that stays, or where the letters' height changes (a superscript)."""
    gone = {(g.c, round(g.x, 2), round(g.y, 2)) for g in remove}
    stay = defaultdict(list)
    for g in _glyph_list(page):
        if (g.c, round(g.x, 2), round(g.y, 2)) not in gone:
            stay[round(g.y, 1)].append(g.x)
    rows = defaultdict(list)
    for g in remove:
        rows[round(g.y, 1)].append(g)
    rects = []
    for y, gs in rows.items():
        gs.sort(key=lambda g: g.x)
        run = [gs[0]]

        def close():
            boxes = [_redact_rect(g) for g in run]
            rects.append(pymupdf.Rect(boxes[0].x0, max(b.y0 for b in boxes), boxes[-1].x1, min(b.y1 for b in boxes)))

        for g in gs[1:]:
            a = run[-1]
            mid_a, mid_g = (a.bbox[1] + a.bbox[3]) / 2, (g.bbox[1] + g.bbox[3]) / 2
            between = any(a.x < x < g.x for x in stay[y])
            if between or abs(mid_a - mid_g) > 0.15 * (a.bbox[3] - a.bbox[1]):
                close()
                run = []
            run.append(g)
        close()
    return [r for r in rects if r.x1 > r.x0 and r.y1 > r.y0]


def _clear_region(page: pymupdf.Page, shift: _Shift) -> None:
    """Remove everything in shift.region and shift.overflow (drawn again, moved, by _draw_region)."""
    for rect in (shift.region, shift.overflow):
        if rect is not None and rect.height > 0.1:
            page.add_redact_annot(rect, fill=False, cross_out=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED, text=pymupdf.PDF_REDACT_TEXT_REMOVE)


def _draw_from(target: pymupdf.Page, original: pymupdf.Document, number: int, clip: pymupdf.Rect, dy: float,
               without: list | None = None) -> None:
    """Draw `clip` of the original page `number` on `target`, moved by dy, so its text, drawings and images keep
    exactly their look. Text outside the clip (and in the boxes `without`) is taken out of the copy that is drawn,
    so text readers don't meet it twice."""
    source = pymupdf.open()
    source.insert_pdf(original, from_page=number, to_page=number)
    sp = source[0]
    for rect in [pymupdf.Rect(sp.rect.x0, sp.rect.y0, sp.rect.x1, clip.y0),
                 pymupdf.Rect(sp.rect.x0, clip.y1, sp.rect.x1, sp.rect.y1)] + list(without or []):
        if rect.height > 0:
            sp.add_redact_annot(rect, fill=False, cross_out=False)
    sp.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                        text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    target.show_pdf_page(clip + (0, dy, 0, dy), source, 0, clip=clip, keep_proportion=False)
    source.close()


def _draw_region(page: pymupdf.Page, shift: _Shift, original: pymupdf.Document, links: list[dict],
                 own_fonts: bool = True) -> None:
    """Draw shift.region of the original page again, moved by shift.dy; its links move with it. Drawn last, so it
    reads after the paragraph above it. shift.overflow goes onto a new page after this one, with its links."""
    if shift.region.height > 0.1:
        _draw_from(page, original, page.number, shift.region, shift.dy)
    for link in page.get_links():
        page.delete_link(link)
    out_links = []
    for link in links:
        link = {k: v for k, v in link.items() if k not in ("xref", "id")}
        if shift.overflow is not None and shift.overflow.contains(link["from"]):
            out_links.append(link)
            continue
        if shift.region.contains(link["from"]):
            link["from"] = link["from"] + (0, shift.dy, 0, shift.dy)
        page.insert_link(link)
    if not shift.adds_page:
        return
    doc, number, rect = page.parent, page.number, page.rect     # (the page object goes stale once a page is added)
    fonts = _OwnFonts(page) if own_fonts and shift.split else None
    new = doc.new_page(pno=number + 1, width=rect.width, height=rect.height)
    if shift.split:                          # the paragraph goes on here first, in the page's own fonts when it can
        runs = (_own_font_runs(new, _Plan(write=shift.split), fonts) if fonts else None) or [(False, shift.split)]
        shift.split_own = [t for own, letters in runs if own for t, *_ in letters]
        for own, letters in runs:
            if own:
                _write_own_run(new, letters)
            elif letters:
                _write_with_writer(new, letters)
    if shift.overflow is not None:
        off = shift.to_top - shift.overflow.y0
        _draw_from(new, original, number, shift.overflow, off)
        for link in out_links:
            link["from"] = link["from"] + (0, off, 0, off)
            new.insert_link(link)
    for band in shift.chrome_bands:          # its running headers and footers, page numbers written again
        _draw_from(new, original, number, band, 0, shift.chrome_skip)
    if shift.chrome_write:
        _write_with_writer(new, [t for t in shift.chrome_write if not t.c.isspace()])


def _apply_page(page: pymupdf.Page, plan: _Plan, original: pymupdf.Document | None = None,
                own_fonts: bool = True) -> None:
    if plan.shift is not None:
        original_links = page.get_links()
        _clear_region(page, plan.shift)
        _write_plan(page, plan, own_fonts)
        _draw_region(page, plan.shift, original, original_links, own_fonts)
    else:
        _write_plan(page, plan, own_fonts)


# ---------------------------------------------------------------- writing in the document's own fonts
# New text is written with the font objects the PDF already has, by their own codes, so nothing is added to the file
# but the text itself. Only a letter none of them can draw (a subset without it, no copy-text map to find its code)
# goes through MuPDF's text writer, which embeds the font it is given (the complete copy font_for found).
def _utf16(hexstr: bytes) -> str:
    try:
        return bytes.fromhex(hexstr.decode()).decode("utf-16-be")
    except (ValueError, UnicodeDecodeError):
        return ""


def _font_codes(doc: pymupdf.Document, xref: int) -> dict[str, bytes]:
    """What the font's codes read as, turned round: {"a": b"a", "fi": b"\x0c"}, from its copy-text map (ToUnicode).
    For a simple font without one, from its /Encoding instead (pdf_encoding).
    Codes keep the map's byte width (one for a simple font, two for a CID font)."""
    kind, ref = doc.xref_get_key(xref, "ToUnicode")
    if kind != "xref":
        codes = pdf_encoding.encoding_codes(doc, xref)
        for letters, lig in LIGATURES:
            if letters in codes:
                codes.setdefault(lig, codes[letters])
        return codes
    cmap = doc.xref_stream(int(ref.split()[0])) or b""
    codes: dict[str, bytes] = {}

    def add(code: int, digits: int, text: str) -> None:
        if text:
            codes.setdefault(text, code.to_bytes(digits // 2, "big"))
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", cmap, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            add(int(src, 16), len(src), _utf16(dst))
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", cmap, re.S):
        for lo, hi, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])", block):
            a, b = int(lo, 16), int(hi, 16)
            if b - a > 0xFFFF:
                continue
            if dst.startswith(b"["):
                for k, d in enumerate(re.findall(rb"<([0-9A-Fa-f]*)>", dst)):
                    add(a + k, len(lo), _utf16(d))
            else:
                try:
                    first = bytes.fromhex(dst[1:-1].decode())
                except ValueError:   # (malformed: MuPDF itself writes some with an odd number of digits)
                    continue
                for k in range(b - a + 1):   # the last byte counts up
                    value = (int.from_bytes(first, "big") + k).to_bytes(len(first), "big")
                    add(a + k, len(lo), value.decode("utf-16-be", "ignore"))
    for letters, lig in LIGATURES:          # a ligature reads as its letters; the plan writes it as one character
        if letters in codes:
            codes.setdefault(lig, codes[letters])
    return codes


class _OwnFonts:
    """A page's font objects by name, with each one's codes and program (to be sure a subset has the letter)."""

    def __init__(self, page: pymupdf.Page):
        self.page, self.doc = page, page.parent
        self.by_name: dict[str, list[int]] = defaultdict(list)
        self._codes: dict[int, dict[str, bytes]] = {}
        self._program: dict[int, pymupdf.Font | None] = {}
        for xref, ext, _type, basefont, *_ in page.get_fonts(full=True):
            if ext in ("n/a", ""):
                continue
            # the text reads back under the name in the font's program when it has one ("Helvetica" is embedded as
            # NimbusSans-Regular), else under the PDF's name for it
            program = self._load(xref)
            for name in {font_key(basefont), font_key(program.name) if program else ""} - {""}:
                if xref not in self.by_name[name]:
                    self.by_name[name].append(xref)

    def _load(self, xref: int) -> pymupdf.Font | None:
        if xref not in self._program:
            try:
                self._program[xref] = pymupdf.Font(fontbuffer=self.doc.extract_font(xref)[3])
            except Exception:
                self._program[xref] = None
        return self._program[xref]

    def _has(self, xref: int, c: str) -> bool:
        program = self._load(xref)
        return program is not None and all(program.has_glyph(ord(ch)) for ch in c)

    def _agrees(self, xref: int, codes: dict[str, bytes]) -> bool:
        """For a map taken from /Encoding (no ToUnicode): every letter MuPDF reads in this font on the page is drawn
        with the glyph the program has for that letter, so letters and glyphs line up in this font at all. It can't
        see a wrong code in the map itself: the checks on the saved page (letter positions, _check_own_glyphs) are
        what refuse that, and the edit is then written the old way."""
        program = self._load(xref)
        if program is None:
            return False
        names = {font_key(n) for n in (self.doc.xref_get_key(xref, "BaseFont")[1].lstrip("/"), program.name)}
        for span in self.page.get_texttrace():
            if font_key(span["font"]) not in names:
                continue
            for u, gid, _o, _b in span["chars"]:
                if u > 32 and chr(u) in codes and program.has_glyph(u) != gid:
                    return False
        return True

    def code(self, name: str, c: str) -> tuple[int, bytes] | None:
        """The font object of that name that draws `c`, and its code for it."""
        for xref in self.by_name.get(font_key(name), []):
            if xref not in self._codes:
                codes = _font_codes(self.doc, xref)
                if codes and self.doc.xref_get_key(xref, "ToUnicode")[0] == "null" and not self._agrees(xref, codes):
                    log.info("Font %s: its /Encoding doesn't read the page as MuPDF does; not written in", name)
                    codes = {}
                self._codes[xref] = codes
            code = self._codes[xref].get(c)
            if code is not None and self._has(xref, c):
                return xref, code
        return None


def _font_resource(page: pymupdf.Page, xref: int) -> str:
    """The name the page's resources give the font object, adding one when only a form on the page uses it."""
    doc = page.parent
    for fx, _ext, _type, _base, name, _enc, *ref in page.get_fonts(full=True):
        if fx == xref and (not ref or not ref[0]):
            return name
    name = f"FE{xref}"
    kind, value = doc.xref_get_key(page.xref, "Resources")
    owner, path = (int(value.split()[0]), "") if kind == "xref" else (page.xref, "Resources/")
    kind, value = doc.xref_get_key(owner, path + "Font")
    if kind == "xref":
        doc.xref_set_key(int(value.split()[0]), name, f"{xref} 0 R")
    else:
        doc.xref_set_key(owner, path + "Font/" + name, f"{xref} 0 R")
    return name


def _own_font_runs(page: pymupdf.Page, plan: _Plan, fonts: _OwnFonts) -> list[tuple[bool, list]] | None:
    """The plan's letters in order, in runs: (True, [(letter, font xref, code)]) for letters the page's own fonts
    draw, (False, [letter]) for the rest. None when none of them can be (or the page is rotated): all go the old way.
    A space the font has no glyph for (TeX's fonts have none) isn't written: the gap reads as a space anyway."""
    if page.rotation or page.parent.xref_get_key(page.xref, "Resources")[0] == "null":
        return None     # (resources inherited from the page tree: a page dictionary of its own would hide them)
    runs: list[tuple[bool, list]] = []
    for t in plan.write:
        found = fonts.code(t.style.font, t.c)
        own = found is not None
        if not own and t.c.isspace():
            if runs and runs[-1][0]:
                continue
            own = False
        if not runs or runs[-1][0] != own:
            runs.append((own, []))
        runs[-1][1].append((t, *found) if own else t)
    return runs if any(own for own, _ in runs) else None


def _write_own_run(page: pymupdf.Page, placed: list[tuple[_Typeset, int, bytes]]) -> None:
    """Letters drawn with the page's own font objects, by their codes, in a content stream added to the page."""
    to_pdf = ~page.transformation_matrix
    ops, current = [b"q BT 0 Tc 0 Tw 100 Tz 0 Ts 0 Tr"], (None, None, None)
    names: dict[int, str] = {}
    for t, xref, code in placed:
        if xref not in names:
            names[xref] = _font_resource(page, xref)
            if not re.fullmatch(r"[A-Za-z0-9_.+-]+", names[xref]):
                raise CantKeepLayout(f"The font resource name {names[xref]!r} can't be written as it is.")
        t.resource = names[xref]   # which font object drew it: the check compares with that one only
        if current[0] != t.style.color:
            ops.append(b"%.4f %.4f %.4f rg" % _rgb(t.style.color))
        if current[1:] != (xref, t.style.size):
            ops.append(b"/%s %.4f Tf" % (names[xref].encode(), t.style.size))
        current = (t.style.color, xref, t.style.size)
        at = pymupdf.Point(t.x, t.y) * to_pdf
        ops.append(b"1 0 0 1 %.3f %.3f Tm <%s> Tj" % (at.x, at.y, code.hex().encode()))
    ops.append(b"ET Q")
    doc = page.parent
    if not page.is_wrapped:
        page.wrap_contents()
    stream = doc.get_new_xref()
    doc.update_object(stream, "<<>>")
    doc.update_stream(stream, b"\n".join(ops))
    contents = page.get_contents()
    doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in [*contents, stream]) + "]")


def _small_copies(letters: list[_Typeset]) -> dict[int, pymupdf.Font]:
    """Each font the letters use (by id), cut down to the letters written in it on this page."""
    needed: dict[int, tuple[pymupdf.Font, set[str]]] = {}
    for t in letters:
        needed.setdefault(id(t.font), (t.font, set()))[1].add(t.c)
    return {key: _small_copy(font, chars) for key, (font, chars) in needed.items()}


def _write_with_writer(page: pymupdf.Page, letters: list[_Typeset], small: dict[int, pymupdf.Font] | None = None
                       ) -> None:
    """Letters through MuPDF's text writer, which embeds each font it is given."""
    small = small or _small_copies(letters)       # only the letters written, not the whole font file
    runs: list[tuple[int, pymupdf.TextWriter]] = []     # in order: text readers read the letters as written
    for t in letters:
        if not runs or runs[-1][0] != t.style.color:
            runs.append((t.style.color, pymupdf.TextWriter(page.rect)))
        runs[-1][1].append((t.x, t.y), t.c, font=small[id(t.font)], fontsize=t.style.size)
    for color, tw in runs:
        tw.write_text(page, color=_rgb(color))


def _write_plan(page: pymupdf.Page, plan: _Plan, own_fonts: bool = True) -> None:
    links = page.get_links()
    # the page's fonts as they are before the old text goes: removing every letter in a font drops it from the page
    fonts = _OwnFonts(page) if own_fonts else None
    for rect in _redact_rects(page, plan.remove):
        page.add_redact_annot(rect, fill=False, cross_out=False)
    if plan.remove:
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    have = {(tuple(round(v, 1) for v in link["from"]), link.get("uri"), link.get("page")) for link in page.get_links()}
    for link in links:      # links a redaction took with it come back
        if (tuple(round(v, 1) for v in link["from"]), link.get("uri"), link.get("page")) not in have:
            page.insert_link({k: v for k, v in link.items() if k not in ("xref", "id")})
    fonts_before = {f[0] for f in page.get_fonts(full=True)}
    runs = (_own_font_runs(page, plan, fonts) if fonts else None) or [(False, plan.write)]
    plan.own = [t for own, letters in runs if own for t, *_ in letters]
    small = _small_copies([t for own, letters in runs if not own for t in letters])   # one per font for the page
    for own, letters in runs:
        if own:
            _write_own_run(page, letters)
        elif letters:
            _write_with_writer(page, letters, small)
    doc = page.parent
    for xref, *_ in page.get_fonts(full=True):      # the fonts just added: their copy-text maps are corrected
        if xref in fonts_before:
            continue
        for font in [xref] + [int(x) for x in re.findall(r"(\d+) 0 R", doc.xref_get_key(xref, "DescendantFonts")[1])]:
            kind, ref = doc.xref_get_key(font, "ToUnicode")
            if kind == "xref":
                cmap_xref = int(ref.split()[0])
                cmap = doc.xref_stream(cmap_xref)
                fixed = _fix_cmap(cmap)
                if fixed != cmap:
                    doc.update_stream(cmap_xref, fixed)


_LIGATURE_UTF16 = {f"{ord(lig):04x}": "".join(f"{ord(c):04X}" for c in letters) for letters, lig in LIGATURES}


def _fix_cmap(cmap: bytes) -> bytes:
    """MuPDF's copy-text map for a font it adds reads a hyphen as a soft hyphen (invisible when pasted) and a
    ligature as one character ("ﬁ", which search doesn't match to "fi"). Both are put right: "-", and "f" "i".
    A range of codes running through one of those ("<0002> <0003> <fb03>": ﬃ, ﬄ) is split into single codes.
    Only what codes mean is changed, never the codes themselves (a font may well use code 00AD for a letter)."""
    def fixes(code: int) -> bool:
        return code == 0x00AD or 0xFB00 <= code <= 0xFB04

    def target(code: int) -> bytes:
        if code == 0x00AD:
            return b"<002D>"
        letters = _LIGATURE_UTF16.get(f"{code:04x}")
        return b"<" + (letters.encode() if letters else f"{code:04X}".encode()) + b">"

    def ranges(m: re.Match) -> bytes:
        entries, changed = [], False
        for a, b, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])", m.group(2)):
            lo, hi = int(a, 16), int(b, 16)
            first = int(dst[1:-1], 16) if dst.startswith(b"<") and len(dst) == 6 else None
            if first is not None and any(fixes(first + k) for k in range(hi - lo + 1)):
                changed = True
                width = len(a)
                entries += [b"<%s> <%s> %s" % (f"{c:0{width}X}".encode(), f"{c:0{width}X}".encode(), target(first + c - lo))
                            for c in range(lo, hi + 1)]
            else:
                entries.append(b"<%s> <%s> %s" % (a, b, dst))
        if not changed:
            return m.group(0)
        return b"%d beginbfrange\n" % len(entries) + b"\n".join(entries) + b"\nendbfrange"

    def chars(m: re.Match) -> bytes:
        # each entry is "<code> <meaning>": only a meaning (the second of the pair) is replaced
        return re.sub(rb"(<[0-9A-Fa-f]+>[ \t]*)<([Ff][Bb]0[0-4]|00[Aa][Dd])>",
                      lambda e: e.group(1) + target(int(e.group(2), 16)), m.group(0))

    cmap = re.sub(rb"(\d+) beginbfrange(.*?)endbfrange", ranges, cmap, flags=re.S)
    return re.sub(rb"\d+ beginbfchar.*?endbfchar", chars, cmap, flags=re.S)


def _check(before: pymupdf.Page, after: pymupdf.Page, plan: _Plan, counts: tuple) -> None:
    """The saved page is what was intended, or CantKeepLayout."""
    removed = {(g.c, round(g.x, 2), round(g.y, 2)) for g in plan.remove}
    shift = plan.shift

    def middle(g: Glyph) -> pymupdf.Point:
        return pymupdf.Point((g.bbox[0] + g.bbox[2]) / 2, (g.bbox[1] + g.bbox[3]) / 2)

    def moved(g: Glyph) -> float:
        return g.y + shift.dy if shift is not None and shift.region.contains(middle(g)) else g.y

    def gone(r) -> bool:          # sent to the new page
        return shift is not None and shift.overflow is not None and shift.overflow.contains(r)
    expected = [(g.c, g.x, moved(g), None) for g in _glyph_list(before)
                if (g.c, round(g.x, 2), round(g.y, 2)) not in removed and not gone(middle(g))]
    expected += [(t.c, t.x, t.y, t) for t in plan.write if not t.c.isspace()]
    actual = _glyph_list(after)
    buckets = defaultdict(list)
    for g in actual:
        buckets[g.c].append(g)
    for c, x, y, t in expected:
        match = next((g for g in buckets[c] if abs(g.x - x) < 0.25 and abs(g.y - y) < 0.25), None)
        if match is None:
            raise CantKeepLayout(f'Checking the edited page failed: the letter "{c}" is not where it should be.')
        buckets[c].remove(match)
        if t is not None:
            if abs(match.style.size - t.style.size) > 0.05 or match.style.color != t.style.color:
                raise CantKeepLayout("Checking the edited page failed: the new text's size or colour came out different.")
            names = {font_key(t.style.font), font_key(t.font.name)}
            got = font_key(match.style.font)
            # PDF's standard fonts are drawn with MuPDF's metric-identical copies, named Nimbus, Standard Symbols, Dingbats
            standard = font_key(t.style.font) in BASE14 and got.startswith(("nimbus", "standardsymbols", "dingbats"))
            if got not in names and not standard:
                raise CantKeepLayout(f'Checking the edited page failed: the new text came out in "{match.style.font}" '
                                     f'instead of "{t.style.font}".')
    left = [g.c for gs in buckets.values() for g in gs]
    if left:
        raise CantKeepLayout(f'Checking the edited page failed: old text was not fully removed ("{"".join(left)[:20]}").')
    if shift is None:
        now = (len(after.get_drawings()), len(after.get_images(full=True)), len(after.get_links()))
        if now != counts:
            raise CantKeepLayout("Checking the edited page failed: a drawing, image or link on the page changed.")
        return

    def boxes(page: pymupdf.Page, kind: str) -> list[pymupdf.Rect]:
        found = [d["rect"] for d in page.get_drawings()] if kind == "d" else [i["bbox"] for i in page.get_image_info()]
        return [pymupdf.Rect(r) for r in found]
    for kind in ("d", "i"):            # every drawing and image is where it was, or moved with the region
        have = boxes(after, kind)
        for r in boxes(before, kind):
            if gone(r):
                continue
            want = r + (0, shift.dy, 0, shift.dy) if shift.region.contains(r) else r
            hit = next((h for h in have if all(abs(a - b) < 0.3 for a, b in zip(h, want))), None)
            if hit is None:
                raise CantKeepLayout("Checking the edited page failed: a drawing or image didn't move with the text "
                                     "below the paragraph.")
            have.remove(hit)
    if len(after.get_links()) != counts[2] - sum(1 for link in before.get_links() if gone(link["from"])):
        raise CantKeepLayout("Checking the edited page failed: a link on the page changed.")


def _check_new_page(before: pymupdf.Page, new: pymupdf.Page, shift: _Shift) -> None:
    """The page inserted for what no longer fit holds exactly that: the paragraph's lines that went on there (each
    letter where it was set, in its size and colour), what was below the paragraph moved up to follow them (the same
    letters, drawings, images and links, looking the same pixel for pixel), the page's running headers and footers,
    and nothing else."""
    import numpy as np
    off = shift.to_top - shift.overflow.y0 if shift.overflow is not None else 0.0

    def inside(r) -> bool:
        return shift.overflow is not None and shift.overflow.contains(r)

    def mid(g: Glyph) -> pymupdf.Point:
        return pymupdf.Point((g.bbox[0] + g.bbox[2]) / 2, (g.bbox[1] + g.bbox[3]) / 2)

    def copied(g: Glyph) -> bool:        # a header or footer letter drawn as it was
        return any(b.contains(mid(g)) for b in shift.chrome_bands) and not any(r.contains(mid(g))
                                                                            for r in shift.chrome_skip)
    written_letters = [t for t in shift.chrome_write + shift.split if not t.c.isspace()]
    want = sorted([(g.c, round(g.x, 1), round(g.y + off, 1)) for g in _glyph_list(before) if inside(mid(g))]
                  + [(g.c, round(g.x, 1), round(g.y, 1)) for g in _glyph_list(before) if copied(g)]
                  + [(t.c, round(t.x, 1), round(t.y, 1)) for t in written_letters])
    got = sorted((g.c, round(g.x, 1), round(g.y, 1)) for g in _glyph_list(new))
    if len(want) != len(got) or any(a[0] != b[0] or abs(a[1] - b[1]) > 0.25 or abs(a[2] - b[2]) > 0.25
                                    for a, b in zip(want, got)):
        raise CantKeepLayout("Checking the new page failed: the text on it isn't as it should be.")
    drawn = _glyph_list(new)
    for t in shift.split:                 # the paragraph's lines: in their own size and colour
        if t.c.isspace():
            continue
        g = next((g for g in drawn if g.c == t.c and abs(g.x - t.x) < 0.25 and abs(g.y - t.y) < 0.25), None)
        if g is None or abs(g.style.size - t.style.size) > 0.05 or g.style.color != t.style.color:
            raise CantKeepLayout("Checking the new page failed: the paragraph's lines there came out in another "
                                 "size or colour.")
    for kind in ("d", "i"):
        old = [pymupdf.Rect(d["rect"]) for d in before.get_drawings()] if kind == "d" else \
              [pymupdf.Rect(i["bbox"]) for i in before.get_image_info()]
        have = [pymupdf.Rect(d["rect"]) for d in new.get_drawings()] if kind == "d" else \
               [pymupdf.Rect(i["bbox"]) for i in new.get_image_info()]
        for r in [r + (0, off, 0, off) for r in old if inside(r)]:
            hit = next((h for h in have if all(abs(a - b) < 0.3 for a, b in zip(h, r))), None)
            if hit is None:
                raise CantKeepLayout("Checking the new page failed: a drawing or image didn't move onto it.")
            have.remove(hit)
    if len(new.get_links()) != sum(1 for link in before.get_links() if inside(link["from"])):
        raise CantKeepLayout("Checking the new page failed: a link didn't move onto it.")
    a = _render(before)
    if shift.overflow is not None:
        b = _render(new, off)
        # (from just inside its top edge: the mask spreads a pixel, which would reach the descenders of the
        # paragraph's last line right above it, a different line before and after the edit)
        area = _mask(a.shape, [shift.overflow + (0, 1.5, 0, 0)])
        diff = (np.abs(a - b).max(axis=2) > INK) & area & (b[:, :, 0] >= 0)
        if diff.any():
            ys, xs = np.nonzero(diff)
            raise CantKeepLayout(f"Checking the new page failed: what moved onto it doesn't look as it did (near "
                                 f"{xs[0] / CHECK_ZOOM:.0f}, {ys[0] / CHECK_ZOOM:.0f} pt of the old page).")
    plain = _render(new)
    written = _changed_boxes(_Plan(write=list(shift.chrome_write) + list(shift.split)))
    bands = _mask(a.shape, shift.chrome_bands) & ~_mask(a.shape, list(shift.chrome_skip) + written)
    if ((np.abs(a - plain).max(axis=2) > INK) & bands).any():
        raise CantKeepLayout("Checking the new page failed: a running header or footer didn't come out as it was.")
    moved = [shift.overflow + (0, off, 0, off)] if shift.overflow is not None else []
    allowed = _mask(plain.shape, moved + list(shift.chrome_bands) + written)
    if ((plain.min(axis=2) < 255 - INK) & ~allowed).any():
        raise CantKeepLayout("Checking the new page failed: it has something on it besides what moved.")

def _check_own_glyphs(after: pymupdf.Page, letters: list[_Typeset]) -> None:
    """Every letter written by code in the page's own fonts draws the glyph that font has for the letter. _check
    reads the letters back through the same copy-text map that chose their codes, so a map that is wrong, or gives
    one letter several codes, would pass it; the glyph actually drawn can't.

    The glyph is compared with the one font object the letter was written in (its resource name on the saved page:
    object numbers can change on save). Two subsets of a font with the same name number their glyphs differently, so
    a wrong glyph could match the other subset's number for the letter."""
    if not letters:
        return
    fonts = _OwnFonts(after)
    by_resource = {name: xref for xref, _e, _t, _b, name, _enc, *ref in after.get_fonts(full=True) if not ref or not ref[0]}
    drawn = [(span["font"], gid, origin) for span in after.get_texttrace() for _u, gid, origin, _b in span["chars"]]
    for t in letters:
        here = [(font, gid) for font, gid, (x, y) in drawn if abs(x - t.x) < 0.25 and abs(y - t.y) < 0.25]
        if t.resource is not None:
            xref = by_resource.get(t.resource)
            program = fonts._load(xref) if xref is not None else None
            expected = {gid} if program is not None and (gid := program.has_glyph(ord(t.c))) else set()
        else:   # (not written by _write_own_run: any same-named font object)
            expected = {gid for xref in fonts.by_name.get(font_key(t.style.font), [])
                        if (program := fonts._load(xref)) is not None and (gid := program.has_glyph(ord(t.c)))}
        if not here or not expected or not any(gid in expected for _f, gid in here):
            raise CantKeepLayout(f'Checking the edited page failed: the letter "{t.c}" was drawn with another glyph.')


def _spaced(items: list[_Typeset]) -> list[_Typeset]:
    """Letters with a (blank) space wherever two words part on a line, so copied and searched text keeps them apart."""
    out, prev = [], None
    for t in items:
        if t.c.isspace():
            continue
        if prev is not None and abs(prev.y - t.y) < 0.5 * t.style.size:
            end = prev.x + prev.font.text_length(prev.c, prev.style.size)
            if t.x - end > 0.15 * t.style.size:
                out.append(_Typeset(end, prev.y, " ", prev.style, _space_font()))
        out.append(t)
        prev = t
    return out


def _in_reading_order(pdf: PdfText, n: int, edits: list[tuple[list[Segment], _Plan]]) -> _Plan | None:
    """The page's edit with the text order kept for text readers (search, copy and paste, the Markdown copy).

    New text can only be added at the end of a page's content, and text readers read a page in content order, so an
    edited line would read as the page's last. Instead every letter after the edit in content order is removed and
    written again, in order, exactly where it was and in its own font, size and colour (the check confirms it):
    the page looks the same and reads in its original order. None when that isn't safe (text after the edit that is
    outlined, invisible, see-through or not in the layout model): the edit then goes at the end."""
    page = pdf.doc[n]
    where: dict[tuple, tuple[Glyph, Segment]] = {}
    for row in pdf.pages[n]:
        for seg in row:
            for w in seg.words:
                for g in w.glyphs:
                    where[(round(g.x, 1), round(g.y, 1))] = (g, seg)
    edited = {id(seg): plan for segs, plan in edits for seg in segs}
    removed = {id(g) for _, plan in edits for g in plan.remove}
    sequence, seen, after = [], set(), False
    for span in page.get_texttrace():
        for ch in span["chars"]:
            hit = where.get((round(ch[2][0], 1), round(ch[2][1], 1)))
            if hit is None:
                if after and not chr(ch[0]).isspace():
                    return None             # text the model doesn't know: it can't be written again faithfully
                continue
            if id(hit[0]) in seen:
                continue                    # the second letter of a ligature
            seen.add(id(hit[0]))
            after = after or id(hit[0]) in removed or id(hit[1]) in edited
            if after:
                if span["type"] != 0 or span["opacity"] != 1:
                    return None
                sequence.append(hit)
    if not sequence:
        return None

    out, done = _Plan(), set()
    for g, seg in sequence:
        if id(seg) in edited:
            plan = edited[id(seg)]
            if id(plan) in done:
                continue
            done.add(id(plan))
            gone = {id(x) for x in plan.remove}
            mine = {id(s) for s, p in ((s, p) for segs, p in edits for s in segs) if p is plan}
            # the line's letters that stay are written again with it (from the edit point on), all in order
            later = [h[0] for h in sequence if id(h[1]) in mine and id(h[0]) not in gone]
            out.remove.extend(plan.remove + later)
            line = plan.write + [_Typeset(h.x, h.y, h.c, h.style, pdf.font_for(h.style, h.c)) for h in later]
            line = [t for t in line if not t.c.isspace()]
            out.write.extend(line if plan.ordered else sorted(line, key=lambda t: t.x))
        elif id(g) not in removed:
            out.remove.append(g)
            out.write.append(_Typeset(g.x, g.y, g.c, g.style, pdf.font_for(g.style, g.c)))
    out.write = _spaced(out.write)
    return out


# ---------------------------------------------------------------- the whole-result checks
CHECK_ZOOM = 2.0          # renders at 144 dpi for the picture check
INK = 48                  # a pixel differs when a colour channel changes by more than this (0-255)


def _words(text: str, original: str) -> list[str]:
    """The text as words. A word broken over two lines counts as one: without its hyphen when that only broke it
    (line_break_hyphen, judged against the original document), else with it."""
    def rejoin(m: re.Match) -> str:
        return m[1] + m[2] if line_break_hyphen(m[1] + "-", m[2], original) else m[1] + "-" + m[2]
    return re.sub(r"(\S+)-\s*\n\s*(\S+)", rejoin, text.replace("\xad", "-")).split()


def _check_text(out: Path, expected: str, original: str) -> None:
    """The whole edited document reads as the original with only the change made: no word lost, added, repeated or
    out of order anywhere (a paragraph worked out wrongly would show up here)."""
    want, got = _words(expected, original), _words(document_text(out), original)
    if got != want:
        at = next((i for i, (a, b) in enumerate(zip(want, got)) if a != b), min(len(want), len(got)))
        near = " ".join(want[max(0, at - 3): at + 3])
        raise CantKeepLayout(f'Checking the edited document failed: its text no longer reads as intended near "{near}".')


def _render(page: pymupdf.Page, dy: float = 0.0):
    """The page as pixels. With dy: as seen moved up by dy, so what moved down by dy lines up with the original
    pixel for pixel (the whole-pixel part of the move is a row offset; the rest is rendered, so edges match)."""
    import numpy as np
    whole = round(dy * CHECK_ZOOM)
    rest = dy * CHECK_ZOOM - whole
    pix = page.get_pixmap(matrix=pymupdf.Matrix(CHECK_ZOOM, 0, 0, CHECK_ZOOM, 0, -rest),
                          colorspace=pymupdf.csRGB, alpha=False)
    rows = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).astype(np.int16)
    if not dy:
        return rows
    height = round(page.rect.height * CHECK_ZOOM)
    out = np.full((height, pix.width, 3), -1000, dtype=np.int16)      # -1000: nothing there to compare
    for r in range(height):
        source = r + whole - pix.y
        if 0 <= source < pix.height:
            out[r] = rows[source]
    return out


def _mask(shape, rects: list[pymupdf.Rect]):
    import numpy as np
    mask = np.zeros(shape[:2], dtype=bool)
    for r in rects:
        y0, y1 = max(0, int(r.y0 * CHECK_ZOOM) - 1), min(shape[0], int(r.y1 * CHECK_ZOOM) + 2)
        x0, x1 = max(0, int(r.x0 * CHECK_ZOOM) - 1), min(shape[1], int(r.x1 * CHECK_ZOOM) + 2)
        if y1 > y0 and x1 > x0:
            mask[y0:y1, x0:x1] = True
    return mask


def _changed_boxes(plan: _Plan) -> list[pymupdf.Rect]:
    """Where letters were removed or written. (A letter written again where it was, to keep the reading order, is
    left out of the picture check: its faint edge pixels can differ, and the letter check has already confirmed its
    place and font.)"""
    boxes = [pymupdf.Rect(g.bbox) + (-1, -1, 1, 1) for g in plan.remove]
    for t in plan.write:
        if t.c.isspace():
            continue
        width = t.font.text_length(t.c, t.style.size)
        boxes.append(pymupdf.Rect(t.x - 1, t.y - 1.1 * t.style.size, t.x + width + 1, t.y + 0.4 * t.style.size))
    return boxes


def _check_picture(before: pymupdf.Page, after: pymupdf.Page, plan: _Plan) -> None:
    """The edited page looks like the original everywhere except where letters changed: rendered and compared
    pixel by pixel. What moved (to make room below a paragraph) must look exactly as it did, moved."""
    import numpy as np
    a, b = _render(before), _render(after)
    if a.shape != b.shape:
        raise CantKeepLayout("Checking the edited page failed: the page size changed.")
    changed = _changed_boxes(plan)
    skip = _mask(a.shape, changed)
    shift = plan.shift
    if shift is not None:
        band = pymupdf.Rect(shift.region.x0, min(shift.region.y0, shift.region.y0 + shift.dy),
                            shift.region.x1, max(shift.region.y1, shift.region.y1 + shift.dy))
        skip |= _mask(a.shape, [band] + ([shift.overflow] if shift.overflow is not None else []))   # compared
        # below, moved (and what went to the new page is checked there)
    diff = (np.abs(a - b).max(axis=2) > INK) & ~skip
    if diff.any():
        ys, xs = np.nonzero(diff)
        raise CantKeepLayout(f"Checking the edited page failed: it looks different outside the edit (near "
                             f"{xs[0] / CHECK_ZOOM:.0f}, {ys[0] / CHECK_ZOOM:.0f} pt).")
    if shift is None:
        return
    moved = _render(after, shift.dy)                         # the edited page, looked at one move up (or down)
    inside = _mask(a.shape, [shift.region]) & ~_mask(a.shape, changed) \
        & ~_mask(a.shape, [r + (0, -shift.dy, 0, -shift.dy) for r in changed])
    diff = (np.abs(a - moved).max(axis=2) > INK) & inside & (moved[:, :, 0] >= 0)
    if diff.any():
        ys, xs = np.nonzero(diff)
        raise CantKeepLayout(f"Checking the edited page failed: what moved below the paragraph doesn't look as it "
                             f"did (near {xs[0] / CHECK_ZOOM:.0f}, {ys[0] / CHECK_ZOOM:.0f} pt).")


def _save_small(doc: pymupdf.Document, out: Path) -> None:
    """Save without the weight an edit adds: fonts it embedded (each text run embeds its own full copy) are cut to
    the letters used, copies of the same font or object (a moved region brings the page's fonts again) are merged,
    and objects are packed into compressed streams. The checks after saving cover the result."""
    try:
        doc.subset_fonts()
    except Exception as e:     # a font that can't be subset stays whole: larger, still right
        log.info("Couldn't subset the fonts: %s", str(e)[:200])
    doc.save(str(out), garbage=4, deflate=True, use_objstms=1)


def _write_checked(path: Path, out: Path, plans: dict[int, _Plan], expected: tuple[str, str] | None = None,
                   own_fonts: bool = True) -> None:
    """Apply the plans to a fresh copy of the PDF, save it to `out` and check it: every edited page letter by letter
    and picture by picture, and (given `expected`) the whole document's text. CantKeepLayout, with `out` removed,
    when anything isn't what was intended."""
    with pymupdf.open(str(path)) as original, pymupdf.open(str(path)) as target:
        counts = {n: (len(original[n].get_drawings()), len(original[n].get_images(full=True)),
                      len(original[n].get_links())) for n in plans}
        inserts = sorted(n for n, plan in plans.items() if plan.shift is not None and plan.shift.adds_page)

        def now(n: int) -> int:       # the page's number in the edited document: a new page before it moves it on
            return n + sum(1 for m in inserts if m < n)
        try:
            for n in sorted(plans, reverse=True):      # from the end: a page inserted doesn't renumber those to come
                _apply_page(target[n], plans[n], original, own_fonts)
            _save_small(target, out)
            with pymupdf.open(str(out)) as edited:
                for n, plan in plans.items():
                    _check(original[n], edited[now(n)], plan, counts[n])
                    _check_own_glyphs(edited[now(n)], plan.own if own_fonts else [])
                    _check_picture(original[n], edited[now(n)], plan)
                    if n in inserts:
                        _check_new_page(original[n], edited[now(n) + 1], plan.shift)
                        _check_own_glyphs(edited[now(n) + 1], plan.shift.split_own if own_fonts else [])
            if expected is not None:
                _check_text(out, *expected)
        except Exception as e:
            out.unlink(missing_ok=True)
            if own_fonts:   # written with the document's own fonts and not as intended: the proven way once more
                log.warning("Edit of %s in its own fonts failed (%s); embedding fonts instead", path.name, e)
                _write_checked(path, out, plans, expected, own_fonts=False)
                return
            raise


def _changed_words(old: str, new: str) -> tuple[int, int, str]:
    """The part of `old` that `new` really changes, by whole words: (start, end, its replacement). "1.1 Introduction"
    to "1.1 Hello World" changes only "Introduction", so a contents line that breaks after "1.1" is still one line's
    change. Both sides keep at least one word."""
    ow, nw = list(re.finditer(r"\S+", old)), list(re.finditer(r"\S+", new))
    i = 0
    while i < min(len(ow), len(nw)) - 1 and ow[i].group() == nw[i].group():
        i += 1
    j = 0
    while (j < min(len(ow), len(nw)) - 1 - i and ow[len(ow) - 1 - j].group() == nw[len(nw) - 1 - j].group()):
        j += 1
    if not ow or not nw or (i == 0 and j == 0):
        return 0, len(old), new
    o_end, n_end = ow[len(ow) - 1 - j].end(), nw[len(nw) - 1 - j].end()
    return (ow[i].start(), o_end if j else len(old), new[nw[i].start():n_end if j else len(new)])


def replace(path: Path, out: Path, pattern: re.Pattern, new: str, replace_all: bool,
            info: dict | None = None) -> tuple[int, list[tuple[int, int]], str]:
    """Write `path` with the pattern's matches (the first, or all) replaced by `new` to `out`, checked.
    Returns (changes, the matches' (start, end) in the document text, the document text). `info`, when given, gets
    "layout": "line" (each change stayed on its line), "rewrapped" (a paragraph was set again) or "moved" (and what
    is below it moved)."""
    pdf = PdfText(path)
    try:
        matches = list(pattern.finditer(pdf.text))
        if not replace_all:
            matches = matches[:1]
        if not matches:
            raise CantKeepLayout("The text couldn't be found in the PDF's layout.")
        # each match goes to its line, or (when it runs over lines, or its line can't take the new text) to its
        # paragraph, which is re-wrapped; every other change in that paragraph then goes with it
        changes = []                          # (start, end, new text) of what each match really changes
        for m in matches:
            a, b, text = _changed_words(m.group(0), new)
            changes.append((m.start() + a, m.start() + b, text))
        by_segment: dict[int, tuple[Segment, list]] = {}
        by_paragraph: dict[int, tuple[list[Segment], list]] = {}
        para_of: dict[int, int] = {}            # id(segment) -> id(its paragraph's first line), once re-wrapped

        def to_paragraph(seg: Segment, ranges: list) -> None:
            para = _paragraph(pdf, seg)
            key = id(para[0])
            entry = by_paragraph.setdefault(key, (para, []))
            entry[1].extend((a + seg.start - para[0].start, b + seg.start - para[0].start, t) for a, b, t in ranges)
            for line in para:
                para_of[id(line)] = key
                if id(line) in by_segment and line is not seg:
                    moved = by_segment.pop(id(line))
                    entry[1].extend((a + line.start - para[0].start, b + line.start - para[0].start, t)
                                    for a, b, t in moved[1])

        for m_start, m_end, text in changes:
            seg = pdf.segment_at(m_start)
            if seg is None:
                raise CantKeepLayout("The text couldn't be found in the PDF's layout.")
            local = [(m_start - seg.start, min(m_end, seg.start + len(seg.text)) - seg.start, text)]
            if m_end > seg.start + len(seg.text):
                para = _paragraph(pdf, seg)
                if m_end > para[-1].start + len(para[-1].text):
                    raise CantKeepLayout("The text runs over more than one paragraph of the PDF, which an edit can't "
                                         "re-set.")
                to_paragraph(seg, [(m_start - seg.start, m_end - seg.start, text)])
            elif id(seg) in para_of:
                to_paragraph(seg, local)
            else:
                by_segment.setdefault(id(seg), (seg, []))[1].extend(local)
        edits: dict[int, list[tuple[list[Segment], _Plan]]] = defaultdict(list)
        for seg, ranges in list(by_segment.values()):
            if id(seg) in para_of:
                continue
            try:
                edits[seg.page].append(([seg], _plan_segment(pdf, seg, ranges)))
            except NeedsRewrap:
                to_paragraph(seg, ranges)
        edits = {n: [(segs, plan) for segs, plan in page_edits if not any(id(s) in para_of for s in segs)]
                 for n, page_edits in edits.items()}
        edits = defaultdict(list, edits)
        for para, ranges in by_paragraph.values():
            edits[para[0].page].append((para, _plan_paragraph(pdf, para, ranges)))
        for n, page_edits in edits.items():
            if len(page_edits) > 1 and any(p.shift is not None for _, p in page_edits):
                raise CantKeepLayout("One of the changes moves the rest of its page; make the changes on that page one "
                                     "at a time.")
        renumbering, head, foot = _plan_new_page(pdf, edits, info)
        for n, seg, plan, ranges in renumbering:
            edits[n].append(([seg], plan))
            changes.extend((seg.start + a, seg.start + b, t) for a, b, t in ranges)
        changes.sort()
        simple = {n: _Plan([g for _, p in page_edits for g in p.remove], [t for _, p in page_edits for t in p.write],
                           shift=next((p.shift for _, p in page_edits if p.shift is not None), None))
                  for n, page_edits in edits.items()}
        # where the lower part of a page moves, the re-set paragraph is drawn before it, so it reads in order anyway
        ordered = {n: simple[n] if simple[n].shift is not None else _in_reading_order(pdf, n, page_edits)
                   for n, page_edits in edits.items()}
        plans = [p for page_edits in edits.values() for _, p in page_edits if not p.chrome]
        if info is not None:
            overflows = sorted(n for n, page_edits in edits.items()
                               if any(p.shift is not None and p.shift.adds_page for _, p in page_edits))
            info["layout"] = ("new_page" if overflows else "moved" if any(p.shift is not None for p in plans)
                              else "rewrapped" if any(p.ordered for p in plans) else "line")
            grew = sum(p.added_lines for p in plans)
            if grew > 0:
                info["added_lines"] = grew
            if overflows:      # 1-based numbers of the pages inserted (each after its page, earlier ones counted)
                info["new_pages"] = [n + 2 + i for i, n in enumerate(overflows)]
        expected, at = [], 0                 # the document's text as it should read afterwards
        for m_start, m_end, text in changes:
            expected += [pdf.text[at:m_start], text]
            at = m_end
        expected = "".join(expected) + pdf.text[at:]
        for plan in plans:
            if plan.shift is not None and plan.shift.adds_page:
                expected = _moved_to_new_page(pdf, expected, changes, plan.shift, head, foot)
        written = ordered
        try:
            if any(p is None for p in ordered.values()):
                raise CantKeepLayout("The page's reading order can't be kept.")
            _write_checked(path, out, ordered, (expected, pdf.text))
        except CantKeepLayout as e:
            log.info("Edit of %s: keeping the reading order failed (%s); the edit goes at the end", path.name, e)
            written = simple
            _write_checked(path, out, simple, (expected, pdf.text))
        if info is not None:
            stand_ins = _stand_ins(written.values())
            if stand_ins:
                info["stand_in"] = stand_ins
            if info.get("new_pages"):   # printed contents lines pointing past the new page (pdf_contents)
                from backend.library import pdf_contents
                info.update(pdf_contents.renumber(path, out, [p - 1 for p in info["new_pages"]], replace))
        return len(matches), [(m.start(), m.end()) for m in matches], pdf.text
    finally:
        pdf.close()


def _whole_line(pdf: PdfText, seg: Segment, plan: _Plan) -> _Plan:
    """The plan with the rest of the line written again too, in order: a page's new text goes at the end of its
    content, so a renumbered "Page 1 of 3" would otherwise read "Page 1 of ... 3" to text readers."""
    gone = {id(g) for g in plan.remove}
    kept = [_Typeset(g.x, g.y, g.c, g.style, pdf.font_for(g.style, g.c))
            for w in seg.words for g in w.glyphs if id(g) not in gone]
    plan.remove = [g for w in seg.words for g in w.glyphs]
    plan.write = _spaced(sorted([t for t in plan.write if not t.c.isspace()] + kept, key=lambda t: t.x))
    return plan


def _page_numbers(pdf: PdfText) -> dict[int, list[tuple[int, int, str, int]]]:
    """The page numbers in running headers and footers: {id(segment): [(start, end, kind, base)]} for each number
    in its text that is the page's number ("page": its value is the page index + base) or the page count ("total").
    A number counts only when the same header or footer on every page it is on agrees."""
    repeated = _repeated(pdf)
    groups: dict[tuple, list[Segment]] = defaultdict(list)
    for s in pdf.segments:
        if id(s) in repeated:
            groups[(round(s.baseline), re.sub(r"\d+", "#", s.text))].append(s)
    fields: dict[int, list] = {}
    for segs in groups.values():
        if len({s.page for s in segs}) < 2:
            continue
        nums = [[(m.start(), m.end(), int(m.group())) for m in re.finditer(r"\d+", s.text)] for s in segs]
        if not nums[0] or len({len(x) for x in nums}) != 1:
            continue
        for k in range(len(nums[0])):
            values = [nums[i][k][2] for i in range(len(segs))]
            bases = {v - s.page for v, s in zip(values, segs)}
            if len(bases) == 1 and len(set(values)) > 1:
                kind, base = "page", bases.pop()
            elif set(values) == {len(pdf.doc)}:
                kind, base = "total", 0
            else:
                continue
            for i, s in enumerate(segs):
                fields.setdefault(id(s), []).append((nums[i][k][0], nums[i][k][1], kind, base))
    return fields


def _renumbered(fields: list, page: int, total: int) -> list[tuple[int, int, str]]:
    """The replacements that give a header or footer the numbers of page `page` (0-based) of `total`."""
    return [(a, b, str(page + base if kind == "page" else total)) for a, b, kind, base in fields]


def _moved_to_new_page(pdf: PdfText, expected: str, changes: list, shift: _Shift, head: str = "",
                       foot: str = "") -> str:
    """The expected text once a page was added after shift.page: what went there (the paragraph's lines that went
    on there, then shift.moved_out) now reads after the rest of the old page (a footer there, say), between the new
    page's own header (head) and footer (foot)."""
    n = shift.page
    page_end = min([s.start for s in pdf.segments if s.page > n] + [len(pdf.text)])

    def at(offset: int) -> int:        # an offset in the original text, in the expected one
        return offset + sum(len(t) - (b - a) for a, b, t in changes if a < offset)
    p = at(page_end)
    lines = sorted(shift.moved_out, key=lambda s: s.start)
    if shift.split:
        ps, pe = at(shift.para_span[0]), at(shift.para_span[1])
        words, going = _words(expected[ps:pe], pdf.text), _words(shift.split_text, pdf.text)
        if not going or words[-len(going):] != going:
            raise CantKeepLayout("The paragraph's lines on the new page couldn't be matched to its text.")
        staying, going = " ".join(words[:-len(going)]), " ".join(going)
        s, e = (at(lines[0].start), at(lines[-1].start + len(lines[-1].text))) if lines else (pe, pe)
        return (expected[:ps] + " " + staying + " " + expected[e:p] + " " + head + " " + going + " "
                + expected[s:e] + " " + foot + " " + expected[p:])
    if not lines:
        return expected
    s, e = at(lines[0].start), at(lines[-1].start + len(lines[-1].text))
    return expected[:s] + " " + expected[e:p] + " " + head + " " + expected[s:e] + " " + foot + " " + expected[p:]

def _plan_new_page(pdf: PdfText, edits: dict, info: dict | None) -> tuple[list, str, str]:
    """When an edit sends part of a page to a new page: that page gets the old page's running headers and footers,
    and every page number in them is renumbered (the pages after it move on one, the page count grows by one).
    Returns ([(page, segment, plan, ranges)] for the renumbering, the new page's header text, its footer text).
    Page numbers that can't be re-set are left as they were (info["page_numbers_kept"]), never guessed at."""
    overflows = [(n, p.shift) for n, page_edits in edits.items() for _, p in page_edits
                 if p.shift is not None and p.shift.adds_page]
    if not overflows:
        return [], "", ""
    if len(overflows) > 1:
        raise CantKeepLayout("Two of the changes would each need a new page; make them one at a time.")
    n, shift = overflows[0]
    total = len(pdf.doc) + 1
    repeated = _repeated(pdf)
    fields = _page_numbers(pdf)
    renumbering = []
    try:
        for s in pdf.segments:
            if id(s) in fields:
                ranges = [r for r in _renumbered(fields[id(s)], s.page + (s.page > n), total)
                          if s.text[r[0]:r[1]] != r[2]]
                if ranges:
                    plan = _whole_line(pdf, s, _plan_segment(pdf, s, ranges))
                    plan.chrome = True
                    renumbering.append((s.page, s, plan, ranges))
        own = [s for s in pdf.segments if s.page == n and id(s) in repeated]
        written = []
        for s in own:
            if id(s) in fields:
                written.append((s, _whole_line(pdf, s, _plan_segment(pdf, s, _renumbered(fields[id(s)], n + 1,
                                                                                            total)))))
    except (CantKeepLayout, NeedsRewrap) as e:
        log.info("Page numbers can't be re-set (%s); they stay as they were", e)
        if info is not None:
            info["page_numbers_kept"] = True
        renumbering, written = [], []
        own = [s for s in pdf.segments if s.page == n and id(s) in repeated and id(s) not in fields]
    width = pdf.doc[n].rect.width
    shift.chrome_bands = [pymupdf.Rect(0, s.top - 1, width, s.bottom + 1) for s in own]
    shift.chrome_skip = [pymupdf.Rect(s.x0 - 0.5, s.top - 0.5, s.x1 + 0.5, s.bottom + 0.5) for s, _ in written]
    shift.chrome_write = [t for _, plan in written for t in plan.write]
    texts = {id(s): s.text for s in own}
    for s, plan in written:
        texts[id(s)] = "".join(t.c for t in plan.write) if plan.write else s.text
    head = " ".join(texts[id(s)] for s in own if s.top < shift.body_top)
    foot = " ".join(texts[id(s)] for s in own if s.top >= shift.body_top)
    return renumbering, head, foot


def _stand_ins(plans) -> dict[str, str]:
    """{the PDF's font: the look-alike drawn instead} for new letters written with a downloaded stand-in font
    (Liberation Sans for Arial: same widths, a slightly different design)."""
    found: dict[str, str] = {}
    for plan in plans:
        split = plan.shift.split if plan.shift is not None else []
        own = {id(t) for t in plan.own + (plan.shift.split_own if plan.shift is not None else [])}
        for t in plan.write + split:
            if id(t) not in own and not t.c.isspace() and font_fetch.stand_in(_FILE_OF.get(id(t.font))):
                found.setdefault(base_name(t.style.font), t.font.name)
    return found


def document_text(path: Path) -> str:
    """The text edits are matched against (rows top to bottom, segments left to right)."""
    pdf = PdfText(path)
    try:
        return pdf.text
    finally:
        pdf.close()
