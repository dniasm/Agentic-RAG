"""After an edit adds a page to a PDF: the printed page numbers in its table of contents.

What needs nothing (checked on a real growth edit): bookmarks and internal links point at page objects, not page
numbers, so they follow their pages by themselves; page labels (/PageLabels) shift with the inserted page too.

What does: a contents line printed on a page ("Results ........ 9"). A line is only changed when we know which page it
points to: an internal link over the line (Word's contents lines have one), or a bookmark with the same title. Its
number must be that page's old label (roman numerals and offsets included: labels, not page indexes) and is changed
to the page's new label, on its own line, through the same checked editor (pdf_edit.replace, layout "line" only).
A line that can't be changed that way is listed for the reply, not forced. Page references in the body text ("see
page 12") are listed, never changed: guessing what they mean is too risky.

Everything happens on the edited file before it is saved over the document, so the edit and its contents lines are one
change and one undo.
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import pymupdf

log = logging.getLogger(__name__)

_CONTENTS_LINE = re.compile(r"^(?P<title>.*?[A-Za-z].*?)[\s.·…_–-]*?\s*(?P<num>\d{1,4}|[ivxlcdm]{1,7})$",
                            re.I)
_PAGE_REFERENCE = re.compile(r"\b(?:pages?|p\.|pp\.)\s*(\d{1,4})\b", re.I)


_FOOTER = re.compile(r"^\s*(?:page\s+)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$", re.I)


def _title_key(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text).casefold().split())


def _label(doc: pymupdf.Document, index: int) -> str:
    return doc[index].get_label() or str(index + 1)


def _old_index(new_index: int, inserted: list[int]) -> int | None:
    """The page's index before the edit; None for a page the edit added. `inserted`: 0-based, in the edited file."""
    if new_index in inserted:
        return None
    return new_index - sum(1 for i in inserted if i < new_index)


def _lines(page: pymupdf.Page) -> list[tuple[str, pymupdf.Rect]]:
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if text:
                out.append((text, pymupdf.Rect(line["bbox"])))
    return out


def find(before: Path, after: Path, inserted: list[int]) -> tuple[list[dict], list[dict]]:
    """Contents lines to renumber in `after`, and body-text page references to report.
    Each fix: {"page" (1-based), "text", "new_text", "old", "new"}; each reference: {"page", "text"}."""
    fixes, references = [], []
    with pymupdf.open(str(before)) as old, pymupdf.open(str(after)) as new:
        outline = {}
        for _level, title, page, *_ in new.get_toc(simple=False):
            if page > 0:
                outline.setdefault(_title_key(title), page - 1)
        first_moved = min(inserted) if inserted else len(new)
        for index, page in enumerate(new):
            if index in inserted:
                continue
            links = [l for l in page.get_links() if l.get("kind") == pymupdf.LINK_GOTO and l.get("page", -1) >= 0]
            for text, box in _lines(page):
                m = _CONTENTS_LINE.match(text)
                target = None
                if m:
                    over = [l["page"] for l in links if pymupdf.Rect(l["from"]).intersects(box)]
                    target = over[0] if over else outline.get(_title_key(m["title"]))
                if target is not None and target < len(new):
                    was = _old_index(target, inserted)
                    if was is None or was >= len(old):
                        continue
                    old_label, new_label = _label(old, was), _label(new, target)
                    if m["num"].casefold() == old_label.casefold() and old_label != new_label:
                        fixes.append({"page": index + 1, "text": text, "old": m["num"], "new": new_label,
                                      "new_text": text[:m.start("num")] + new_label + text[m.end("num"):]})
                    continue
                if _FOOTER.match(text):      # a running footer ("Page 3 of 4"): the growth code renumbers those
                    continue
                for r in _PAGE_REFERENCE.finditer(text):
                    if int(r.group(1)) > first_moved:      # a page at or after the first new one may have moved
                        references.append({"page": index + 1, "text": text})
                        break
    return fixes, references


def renumber(before: Path, out: Path, inserted: list[int], replace) -> dict:
    """Renumber the contents lines in `out` (the edited file) in place, each through `replace` (pdf_edit.replace),
    checked. Returns {"contents_renumbered": [...], "contents_kept": [...], "page_references": [...]} (empty lists
    left out)."""
    fixes, references = find(before, out, inserted)
    done, kept = [], []
    for fix in fixes:
        words = fix["text"].split()
        pattern = re.compile(r"\s*".join(re.escape(w) for w in words))
        tmp = out.with_name(out.name + ".contents")
        info: dict = {}
        try:
            from backend.library import pdf_edit
            count, _spans, text = replace(out, tmp, pattern, fix["new_text"], False, info)
            if len(pattern.findall(text)) != 1 or info.get("layout") != "line":
                raise pdf_edit.CantKeepLayout("not a single line changed in place")
            shutil.move(str(tmp), str(out))
            done.append(fix)
        except Exception as e:   # (CantKeepLayout, or anything else: the line is listed instead)
            log.info("Contents line %r on page %d not renumbered: %s", fix["text"], fix["page"], e)
            tmp.unlink(missing_ok=True)
            kept.append(fix)
    result = {"contents_renumbered": [{"page": f["page"], "line": f["new_text"]} for f in done],
              "contents_kept": [{"page": f["page"], "line": f["text"], "should_be": f["new"]} for f in kept],
              "page_references": references[:10]}
    return {k: v for k, v in result.items() if v}
