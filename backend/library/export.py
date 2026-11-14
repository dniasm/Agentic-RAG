"""A document's text as a PDF, for downloading (an edited PDF is a Markdown copy; this turns it back into a PDF).

The layout is simple: the text, with Markdown headings, bullet and numbered lists, and emphasis markers tidied.
The original PDF's layout and images are not recreated. A system font with wide character coverage is embedded
(Arial with Segoe UI as fallback on Windows, DejaVu Sans on Linux); without one, the built-in Helvetica is used and
characters it lacks become "?".
"""
import re
from pathlib import Path

from renamer import config, editing

# (regular, bold) per family, first found wins; fallbacks cover characters the main font lacks
_FONTS = [
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"),
]
_FALLBACKS = [r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\seguisym.ttf"]
_LOOKALIKES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u00a0": " ", "\u200b": ""})

_HEADING = re.compile(r"^(#{1,6})\s+(.*)")
_BULLET = re.compile(r"^(\s*)[-*+•]\s+(.*)")
_NUMBERED = re.compile(r"^(\s*)(\d+[.)])\s+(.*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_EMPHASIS = re.compile(r"(\*\*|__|`)")
_SIZES = {1: 18, 2: 15, 3: 13}


def _inline(text: str) -> str:
    """Markdown inline marks out: links become 'text (url)', bold and code markers go."""
    text = _LINK.sub(lambda m: m.group(1) if m.group(1) == m.group(2) else f"{m.group(1)} ({m.group(2)})", text)
    return _EMPHASIS.sub("", text).translate(_LOOKALIKES)


def _setup_fonts(pdf) -> tuple[str, bool]:
    """Returns (font family, unicode). Embeds the first system font found."""
    for regular, bold in _FONTS:
        if Path(regular).exists():
            pdf.add_font("main", "", regular)
            pdf.add_font("main", "B", bold if Path(bold).exists() else regular)
            fallbacks = []
            for i, path in enumerate(p for p in _FALLBACKS if Path(p).exists()):
                pdf.add_font(f"fallback{i}", "", path)
                fallbacks.append(f"fallback{i}")
            if fallbacks:
                pdf.set_fallback_fonts(fallbacks)
            return "main", True
    return "helvetica", False


def to_pdf(doc: dict) -> bytes:
    """The document's text, laid out as a PDF."""
    from fpdf import FPDF

    text = editing.document_text(doc)
    pdf = FPDF(format="A4")
    pdf.set_title(doc["title"])
    pdf.set_creator("Document Renaming Agent")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(20, 18, 20)
    family, unicode = _setup_fonts(pdf)
    pdf.add_page()

    def write(line: str, size: float = 11, bold: bool = False, indent: float = 0, before: float = 0) -> None:
        if not unicode:
            line = line.encode("latin-1", "replace").decode("latin-1")
        pdf.set_font(family, "B" if bold else "", size)
        if before:
            pdf.ln(before)
        pdf.set_x(pdf.l_margin + indent)
        pdf.multi_cell(0, size * 0.55, line, new_x="LMARGIN", new_y="NEXT")

    markdown = doc["ext"] == ".md"
    in_code = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if markdown and line.strip().startswith("```"):
            in_code = not in_code
            continue
        if not line.strip():
            pdf.ln(2.5)
            continue
        if in_code or not markdown:
            write(line.translate(_LOOKALIKES))
            continue
        if m := _HEADING.match(line):
            level = len(m.group(1))
            write(_inline(m.group(2)), size=_SIZES.get(level, 11.5), bold=True, before=3 if level <= 2 else 1.5)
        elif m := _BULLET.match(line):
            depth = len(m.group(1).expandtabs(4)) // 2
            write("• " + _inline(m.group(2)), indent=4 + 5 * depth)
        elif m := _NUMBERED.match(line):
            depth = len(m.group(1).expandtabs(4)) // 2
            write(f"{m.group(2)} " + _inline(m.group(3)), indent=4 + 5 * depth)
        elif set(line.strip()) <= set("-*_ ") and len(line.strip()) >= 3:
            pdf.ln(2)                                       # a horizontal rule: just space
        else:
            write(_inline(line))
    return bytes(pdf.output())


def pdf_file_name(doc: dict) -> str:
    """The download's name: the stored file's, with .pdf."""
    return Path(doc["file_name"]).stem + ".pdf"


def stored_pdf(doc: dict) -> Path | None:
    """A document that is still a PDF is downloaded as it is."""
    path = config.LIBRARY_DIR / doc["file_name"]
    return path if doc["ext"] == ".pdf" and path.exists() else None
