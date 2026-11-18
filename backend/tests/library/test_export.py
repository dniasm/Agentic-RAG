"""Downloading a document as a PDF."""
import io

import docx
import pytest
from pypdf import PdfReader

from backend.library import config, editing, export, library
from test_pdf_edit import CROSS, LINES, make_pdf, pdf_doc  # noqa: F401  (fixture)

MARKDOWN = """# Measles Outbreak in Morocco 2023‐2025

## Key facts

- **Cases:** 25,000 suspected, in all 12 regions
- Deaths: 120
  - mostly children under five

1. Vaccinate
2. Report cases

See the [WHO page](https://www.who.int) for more. Kathmandu – नेपाल
"""


def pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


@pytest.fixture
def md_doc(tmp_path):
    path = tmp_path / "measles.md"
    path.write_text(MARKDOWN, encoding="utf-8")
    doc, _ = library.ingest_path(path)
    assert doc["status"] == "indexed"
    return doc


@pytest.fixture
def client(signed_in):
    return signed_in


def test_markdown_becomes_a_tidy_pdf(md_doc):
    data = export.to_pdf(md_doc)
    assert data.startswith(b"%PDF")
    text = pdf_text(data)
    assert "Measles Outbreak in Morocco 2023-2025" in text            # the odd hyphen is a plain one now
    assert "Cases: 25,000 suspected" in text and "**" not in text and "#" not in text
    assert "• Deaths: 120" in text and "1. Vaccinate" in text
    assert "WHO page (https://www.who.int)" in text


def test_text_and_word_documents_too(library_docs, tmp_path):
    assert "maximum margin" in pdf_text(export.to_pdf(library_docs["svm.txt"]))
    path = tmp_path / "memo.docx"
    d = docx.Document()
    d.add_paragraph("The launch is planned for March in Kathmandu.")
    d.save(str(path))
    doc, _ = library.ingest_path(path)
    assert "planned for March in Kathmandu" in pdf_text(export.to_pdf(doc))


def test_the_download_endpoint(md_doc, client):
    md_doc = editing.catalog.get_document(md_doc["id"])
    r = client.get(f"/api/library/documents/{md_doc['id']}/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and ".pdf" in r.headers["content-disposition"]
    assert client.get("/api/library/documents/nope/pdf").status_code == 404


def test_an_edited_pdf_comes_back_as_a_pdf_with_the_edit(pdf_doc, client):  # noqa: F811
    r = client.get(f"/api/library/documents/{pdf_doc['id']}/pdf")
    assert r.content == make_pdf(LINES)                                # still a PDF: the file itself
    editing.edit(pdf_doc["id"], "replace", find="March", text="June")             # in place: still the PDF itself
    r = client.get(f"/api/library/documents/{pdf_doc['id']}/pdf")
    stored = config.LIBRARY_DIR / editing.catalog.get_document(pdf_doc["id"])["file_name"]
    assert r.content == stored.read_bytes() and "June in Kathmandu" in pdf_text(r.content)
    editing.edit(pdf_doc["id"], "replace", convert_pdf=True, **CROSS)             # converted: laid out again
    r = client.get(f"/api/library/documents/{pdf_doc['id']}/pdf")
    text = pdf_text(r.content)
    assert "June in Pokhara" in text and "Kathmandu" not in text   # both edits kept
    assert (config.LIBRARY_DIR / editing.catalog.get_document(pdf_doc["id"])["file_name"]).suffix == ".md"
