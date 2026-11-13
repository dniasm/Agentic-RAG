"""PDFs: edited as a Markdown copy of their text, only with agreement; undo brings the PDF back."""
import pytest

from renamer import catalog, config, editing, library
from tests.test_agent import turn
from tests.test_editing import chunk_texts

LINES = ["Quarterly Budget Report", "The launch is planned for March in Kathmandu.", "Costs stay within the budget."]


def make_pdf(lines) -> bytes:
    """A one-page PDF with the lines as text (written by hand: no PDF library is needed to make test files)."""
    stream = "BT /F1 12 Tf 72 720 Td 16 TL " + " T* ".join(f"({line}) Tj" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 5 0 R "
        "/Resources << /Font << /F1 4 0 R >> >> >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


@pytest.fixture
def pdf_doc(tmp_path):
    path = tmp_path / "budget.pdf"
    path.write_bytes(make_pdf(LINES))
    doc, _ = library.ingest_path(path)
    assert doc["status"] == "indexed", doc["error"]
    return doc


def stored_files():
    return sorted(p.name for p in config.LIBRARY_DIR.iterdir())


def test_a_pdf_is_not_converted_without_agreement(pdf_doc):
    with pytest.raises(editing.NeedsConversion):
        editing.edit(pdf_doc["id"], "replace", find="March", text="June")
    assert catalog.get_document(pdf_doc["id"])["ext"] == ".pdf" and len(stored_files()) == 1


def test_editing_a_pdf_converts_it_and_keeps_the_original(pdf_doc):
    out = editing.edit(pdf_doc["id"], "replace", find="March", text="June", convert_pdf=True)
    assert out["converted"] and out["changes"] == 1
    now = catalog.get_document(pdf_doc["id"])
    assert now["ext"] == ".md" and now["file_name"].endswith(".md") and stored_files() == [now["file_name"]]
    assert "June in Kathmandu" in (config.LIBRARY_DIR / now["file_name"]).read_text(encoding="utf-8")
    assert "June" in chunk_texts(pdf_doc["id"]) and "March" not in chunk_texts(pdf_doc["id"])
    assert now["pages"] == pdf_doc["pages"]
    saved = list((config.DATA_DIR / "versions" / pdf_doc["id"]).iterdir())
    assert [p.suffix for p in saved] == [".pdf"] and saved[0].read_bytes() == make_pdf(LINES)
    assert catalog.edit_history(pdf_doc["id"])[0]["detail"].startswith("converted the PDF to Markdown")


def test_undo_brings_the_pdf_back(pdf_doc):
    editing.edit(pdf_doc["id"], "replace", find="March", text="June", convert_pdf=True)
    out = editing.undo(pdf_doc["id"])
    now = catalog.get_document(pdf_doc["id"])
    assert out["status"] == "undone" and now["ext"] == ".pdf" and now["file_name"].endswith(".pdf")
    assert stored_files() == [now["file_name"]]
    assert (config.LIBRARY_DIR / now["file_name"]).read_bytes() == make_pdf(LINES)
    assert "March" in chunk_texts(pdf_doc["id"]) and "June" not in chunk_texts(pdf_doc["id"])


def test_a_failed_pdf_edit_leaves_no_stray_copy(pdf_doc):
    with pytest.raises(editing.EditError, match="isn't in the document"):
        editing.edit(pdf_doc["id"], "replace", find="nothing like this", text="x", convert_pdf=True)
    out = editing.edit(pdf_doc["id"], "replace", find="e", text="E", convert_pdf=True)   # several places
    assert out["status"] == "ambiguous"
    assert catalog.get_document(pdf_doc["id"])["ext"] == ".pdf" and len(stored_files()) == 1


def test_agent_asks_before_converting_a_pdf(pdf_doc):
    call = {"doc_id": pdf_doc["id"], "operation": "replace", "find": "March", "text": "June"}
    _, results = turn("pdf1", [("find_documents", {"description": "budget.pdf"})], [("edit_document", call)])
    assert results[-1]["status"] == "needs_confirmation" and results[-1]["reason"] == "pdf_conversion"
    _, results = turn("pdf1", [("edit_document", {**call, "convert_pdf": True})])      # the model can't also agree
    assert results[-1]["status"] == "edited" and results[-1]["converted"]

    editing.undo(pdf_doc["id"])                                                         # a PDF again
    _, results = turn("pdf2", [("find_documents", {"description": "budget.pdf"})],
                      [("edit_document", {**call, "find": "Costs", "text": "Spend", "convert_pdf": True})])
    assert results[-1]["status"] == "needs_confirmation"                               # same message: not enough
