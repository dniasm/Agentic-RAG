"""OCR for PDF pages without a text layer (backend/rag/ocr.py): read into the index only, the stored file unchanged,
marked in sources, cached, limited, and an upload still works when tesseract is missing."""
import hashlib
import logging
import subprocess

import pymupdf
import pytest

from backend.config.settings import settings
from backend.library import catalog, config, editing, embed_budget, library
from services.document import db, ingestion, ocr, vectorstore
from backend.tools import rag as rag_tool

needs_tesseract = pytest.mark.skipif(ocr.tesseract() is None, reason="tesseract isn't installed")
SCANNED = "Quarterly maintenance report for the pumping station"
TYPED = "This page has a real text layer about valves"


def _scan(lines: list[str]) -> bytes:
    """A page of text turned into a picture of itself: what a scanner gives."""
    src = pymupdf.open()
    page = src.new_page()
    for i, line in enumerate(lines):
        page.insert_text((72, 100 + 30 * i), line, fontsize=16)
    return page.get_pixmap(dpi=200).tobytes("png")


def _pdf(pages: list[tuple[str, list[str]]]) -> bytes:
    """pages: ("scan", lines) or ("text", lines)."""
    doc = pymupdf.open()
    for kind, lines in pages:
        page = doc.new_page()
        if kind == "scan":
            page.insert_image(page.rect, stream=_scan(lines))
        else:
            for i, line in enumerate(lines):
                page.insert_text((72, 100 + 30 * i), line, fontsize=14)
    return doc.tobytes()


def _chunks(doc_id: str, owner: str) -> list[dict]:
    points, _ = vectorstore.get_client().scroll(settings.collection_name, scroll_filter=vectorstore.user_filter(owner, doc_id),
                                                limit=100, with_payload=True)
    return [p.payload for p in points]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(ocr, "_warned", False)
    with db.shared().tx() as c:
        c.execute("DELETE FROM page_text")


@needs_tesseract
def test_a_scanned_page_is_read_into_the_index_and_the_file_is_unchanged():
    data = _pdf([("text", [TYPED]), ("scan", [SCANNED])])
    doc, _ = library.add_file("scan.pdf", data, owner="u1")
    doc = library.process(doc["id"])
    assert doc["status"] == "indexed", doc["error"]
    stored = (config.LIBRARY_DIR / doc["file_name"]).read_bytes()
    assert hashlib.sha256(stored).hexdigest() == hashlib.sha256(data).hexdigest()   # no text layer added
    payloads = _chunks(doc["id"], "u1")
    scanned = [p for p in payloads if p["metadata"].get("ocr")]
    assert scanned and scanned[0]["metadata"]["page"] == 2
    assert "pumping station" in scanned[0]["page_content"].lower()
    assert not any(p["metadata"].get("ocr") for p in payloads if p["metadata"]["page"] == 1)


@needs_tesseract
def test_ocr_passages_are_charged_to_the_allowance():
    doc, _ = library.add_file("scan.pdf", _pdf([("scan", [SCANNED])]), owner="u2")
    doc = library.process(doc["id"])
    assert doc["status"] == "indexed" and embed_budget.used("u2") == doc["chunks"] > 0


@needs_tesseract
def test_a_source_from_a_scanned_page_says_so():
    block = rag_tool.format_block(1, {"source": "Report", "page": 2, "ocr": True, "content": "x"})
    assert "read by OCR" in block
    assert rag_tool.to_sources([{"source": "Report", "page": 2, "ocr": True, "content": "x"}])[0]["ocr"] is True


@needs_tesseract
def test_a_page_is_read_once_then_comes_from_the_cache(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    path.write_bytes(_pdf([("scan", [SCANNED])]))
    first = ocr.read_pages(path, [1])
    runs = []
    real = ocr._run
    monkeypatch.setattr(ocr, "_run", lambda *a: runs.append(1) or real(*a))
    assert ocr.read_pages(path, [1]) == first and not runs


@needs_tesseract
def test_editing_text_on_a_scanned_page_says_why_it_cant(monkeypatch):
    doc, _ = library.add_file("scan.pdf", _pdf([("text", [TYPED]), ("scan", [SCANNED])]), owner="u3")
    doc = library.process(doc["id"])
    with pytest.raises(editing.EditError, match="page 2, a scanned image"):
        editing.edit(doc["id"], "replace", find="maintenance report", text="service report")


def test_without_tesseract_an_upload_is_indexed_as_before(monkeypatch, caplog):
    monkeypatch.setattr(ocr, "tesseract", lambda: None)
    caplog.set_level(logging.INFO)
    mixed, _ = library.add_file("mixed.pdf", _pdf([("text", [TYPED]), ("scan", [SCANNED])]), owner="u4")
    assert library.process(mixed["id"])["status"] == "indexed"
    only_scan, _ = library.add_file("only.pdf", _pdf([("scan", [SCANNED])]), owner="u4")
    failed = library.process(only_scan["id"])
    assert failed["status"] == "failed" and "No extractable text" in failed["error"]
    assert sum("ocr_unavailable" in r.getMessage() for r in caplog.records) == 1     # logged once


@needs_tesseract
def test_a_page_that_times_out_is_skipped(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    path.write_bytes(_pdf([("scan", [SCANNED])]))

    def slow(*a):
        raise subprocess.TimeoutExpired("tesseract", settings.ocr_page_timeout)
    monkeypatch.setattr(ocr, "_run", slow)
    assert ocr.read_pages(path, [1]) == {}


def test_an_oversized_page_is_rendered_smaller():
    doc = pymupdf.open()
    huge = doc.new_page(width=72 * 40, height=72 * 40)         # 40 x 40 inches
    assert ocr.render_dpi(huge) < settings.ocr_dpi
    assert (40 * ocr.render_dpi(huge)) ** 2 <= settings.ocr_max_pixels
    assert ocr.render_dpi(doc.new_page()) == settings.ocr_dpi   # a letter-size page: the full resolution


@needs_tesseract
def test_pages_past_the_cap_are_left_out(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ocr_max_pages", 1)
    path = tmp_path / "scan.pdf"
    path.write_bytes(_pdf([("scan", [SCANNED]), ("scan", ["Second scanned page about filters"])]))
    assert list(ocr.read_pages(path, [1, 2])) == [1]


def test_a_page_with_text_or_without_images_isnt_ocred(tmp_path):
    path = tmp_path / "plain.pdf"
    doc = pymupdf.open()
    doc.new_page()                                               # blank: no text, no image
    doc.new_page().insert_text((72, 100), TYPED)
    doc.save(path)
    with pymupdf.open(path) as d:
        assert not ocr.needs_ocr(d[0]) and not ocr.needs_ocr(d[1])
