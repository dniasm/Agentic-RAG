"""OCR for PDF pages without a text layer (scans), for the index only.

The stored PDF is never changed: the text read here goes into the search index, marked as OCR so a cited source can
say so, and editing those pages stays refused. Each page is rendered at no more than OCR_DPI and OCR_MAX_PIXELS (an
oversized page is rendered smaller) and read by the tesseract program, one page at a time with a time limit. Pages are
read on the upload worker, outside the library lock.

Results are cached by the rendered image (page_text in the service's document.db): an edit re-reads the whole file
(editing._reindex, under the library lock), and an unchanged scanned page then costs a render, not another OCR run.

No tesseract installed (or OCR turned off): the PDF is indexed as before, from its text layer only, and one event is
logged.
"""
import hashlib
import logging
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

from backend.config.settings import settings
from services.document import db

log = logging.getLogger(__name__)

_WINDOWS_DEFAULT = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
_warned = False


def tesseract() -> str | None:
    """The tesseract program: OCR_TESSERACT, else on the PATH, else its usual Windows place; None if there is none."""
    if settings.ocr_tesseract:
        return settings.ocr_tesseract if Path(settings.ocr_tesseract).exists() else None
    found = shutil.which("tesseract")
    if found:
        return found
    return str(_WINDOWS_DEFAULT) if _WINDOWS_DEFAULT.exists() else None


def _unavailable(reason: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("ocr_unavailable: %s", reason)


def render_dpi(page: pymupdf.Page) -> int:
    """OCR_DPI, lowered so the image stays within OCR_MAX_PIXELS."""
    area_in = (page.rect.width / 72) * (page.rect.height / 72)
    cap = math.sqrt(settings.ocr_max_pixels / area_in) if area_in > 0 else settings.ocr_dpi
    return max(50, min(settings.ocr_dpi, int(cap)))


def needs_ocr(page: pymupdf.Page) -> bool:
    """A page with no text layer but an image on it."""
    return not page.get_text().strip() and bool(page.get_images())


def _run(program: str, image: Path) -> str:
    result = subprocess.run([program, str(image), "stdout", "-l", settings.ocr_language, "--psm", "3"],
                            capture_output=True, timeout=settings.ocr_page_timeout, check=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return result.stdout.decode("utf-8", "replace")


def page_text(page: pymupdf.Page, program: str, cached_only: bool = False) -> str:
    """The page's text as tesseract reads it (cached by the rendered image). "" when it fails or times out, or, with
    cached_only, when it isn't in the cache."""
    pix = page.get_pixmap(dpi=render_dpi(page), colorspace=pymupdf.csGRAY, alpha=False)
    key = hashlib.sha256(f"{pix.width}x{pix.height}|{settings.ocr_language}|".encode() + pix.samples).hexdigest()
    with db.shared().tx() as c:
        row = c.execute("SELECT text FROM page_text WHERE key = ?", (key,)).fetchone()
    if row:
        return row[0]
    if cached_only:
        return ""
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / "page.png"
        pix.save(str(image))
        try:
            text = _run(program, image)
        except subprocess.TimeoutExpired:
            log.warning("OCR of page %d timed out after %d s", page.number + 1, settings.ocr_page_timeout)
            return ""
        except (subprocess.CalledProcessError, OSError) as e:
            log.warning("OCR of page %d failed: %s", page.number + 1, str(e)[:200])
            return ""
    with db.shared().tx() as c:
        c.execute("INSERT OR REPLACE INTO page_text (key, text) VALUES (?, ?)", (key, text))
    return text


def read_pages(path: Path, numbers: list[int], cached_only: bool = False, progress=None) -> dict[int, str]:
    """OCR text for the given 1-based pages that need it (no text layer, an image on them), at most OCR_MAX_PAGES.
    progress(done, total) is called before each page is read (the Ingest stream's "ocr page n of N")."""
    if not numbers or not settings.ocr_enabled:
        return {}
    program = tesseract()
    if program is None:
        _unavailable("tesseract not found")
        return {}
    out: dict[int, str] = {}
    with pymupdf.open(str(path)) as doc:
        todo = [n for n in numbers if 1 <= n <= len(doc) and needs_ocr(doc[n - 1])]
        if len(todo) > settings.ocr_max_pages:
            log.info("OCR: %d pages need it; reading the first %d", len(todo), settings.ocr_max_pages)
            todo = todo[:settings.ocr_max_pages]
        for done, n in enumerate(todo, start=1):
            if progress and not cached_only:
                progress(done, len(todo))
            text = page_text(doc[n - 1], program, cached_only)
            if text.strip():
                out[n] = text
    return out
