"""Headings for the agent, and the document preview."""
import docx

from backend.library import agent, editing, library
from test_export import client, md_doc  # noqa: F401  (fixtures)
from test_pdf_edit import pdf_doc  # noqa: F401  (fixture)


def test_markdown_headings(md_doc):  # noqa: F811
    assert editing.outline(md_doc) == ["Measles Outbreak in Morocco 2023‐2025", "Key facts"]


def test_word_heading_styles(tmp_path):
    d = docx.Document()
    d.add_heading("Introduction", 1)
    d.add_paragraph("Some text that is not a heading.")
    d.add_heading("Method", 2)
    d.save(tmp_path / "report.docx")
    doc, _ = library.ingest_path(tmp_path / "report.docx")
    assert editing.outline(doc) == ["Introduction", "Method"]


def test_plain_text_guesses_capitals_and_numbers():
    lines = ["AT A GLANCE", "A normal sentence.", "2.1 Results", "Ends with a colon:", "**SUMMARY**", "x"]
    assert [line for line in lines if editing._looks_like_heading(line)] == ["AT A GLANCE", "2.1 Results", "**SUMMARY**"]


def test_document_details_includes_headings(md_doc):  # noqa: F811
    guard = agent.Guard({}, 1)
    guard.owner = md_doc["owner"]
    out = agent.run_tool("document_details", {"doc_id": md_doc["id"]}, guard, [])
    assert out["headings"] == ["Measles Outbreak in Morocco 2023‐2025", "Key facts"]


def test_preview_endpoints(md_doc, pdf_doc, client):  # noqa: F811
    r = client.get(f"/api/library/documents/{md_doc['id']}/preview")
    assert r.status_code == 200 and "Key facts" in r.json()["text"] and r.json()["truncated"] is False
    r = client.get(f"/api/library/documents/{pdf_doc['id']}/file?inline=true")
    assert r.headers["content-disposition"].startswith("inline")
    assert client.get(f"/api/library/documents/{pdf_doc['id']}/file").headers["content-disposition"].startswith("attachment")
    assert client.get("/api/library/documents/nope/preview").status_code == 404
