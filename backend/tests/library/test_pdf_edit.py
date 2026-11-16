"""PDFs: changed in place in the document's own font, size, colour and spacing when the look can be kept (checked
after writing); otherwise converted to a Markdown copy of their text, only with agreement. Undo brings the PDF back."""
import re
from pathlib import Path

import pymupdf
import pytest

from backend.library import catalog, config, editing, library, pdf_edit
from test_agent import turn
from test_editing import chunk_texts

LINES = ["Quarterly Budget Report", "The launch is planned for March in Kathmandu.", "Costs stay within the budget."]
CROSS = {"find": "Kathmandu. Costs stay", "text": "Pokhara. Spending stays"}   # runs over two lines


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


def spans(path: Path, text: str) -> list[dict]:
    """The text spans of the PDF's first page that contain `text`."""
    with pymupdf.open(str(path)) as d:
        return [s for b in d[0].get_text("dict")["blocks"] for line in b.get("lines", []) for s in line["spans"]
                if text in s["text"]]


# ---------------------------------------------------------------- in place, through the library
def test_a_pdf_is_edited_in_place_in_its_own_font(pdf_doc):
    out = editing.edit(pdf_doc["id"], "replace", find="March", text="June")
    assert out["status"] == "edited" and not out["converted"] and out["changes"] == 1
    now = catalog.get_document(pdf_doc["id"])
    path = config.LIBRARY_DIR / now["file_name"]
    assert now["ext"] == ".pdf" and stored_files() == [now["file_name"]]
    (june,) = spans(path, "June")
    (costs,) = spans(path, "Costs")
    assert june["size"] == costs["size"] == 12 and june["color"] == costs["color"]
    assert abs(costs["origin"][1] - june["origin"][1] - 16) < 0.05             # on March's line, 16pt above Costs
    assert "June in Kathmandu" in chunk_texts(pdf_doc["id"]) and "March" not in chunk_texts(pdf_doc["id"])
    assert out["previews"][0]["old"] == "March" and out["previews"][0]["new"] == "June"
    saved = list((config.DATA_DIR / "versions" / pdf_doc["id"]).iterdir())
    assert [p.suffix for p in saved] == [".pdf"] and saved[0].read_bytes() == make_pdf(LINES)
    assert not catalog.edit_history(pdf_doc["id"])[0]["detail"].startswith("converted")


def test_undo_restores_an_in_place_edit(pdf_doc):
    editing.edit(pdf_doc["id"], "replace", find="March", text="June")
    editing.undo(pdf_doc["id"])
    now = catalog.get_document(pdf_doc["id"])
    assert (config.LIBRARY_DIR / now["file_name"]).read_bytes() == make_pdf(LINES)
    assert "March" in chunk_texts(pdf_doc["id"])


def test_a_change_that_cant_keep_the_look_asks_first_and_says_why(pdf_doc):
    with pytest.raises(editing.NeedsConversion, match="more than one paragraph"):
        editing.edit(pdf_doc["id"], "replace", **CROSS)
    with pytest.raises(editing.NeedsConversion, match="end of a PDF"):
        editing.edit(pdf_doc["id"], "append", text="A new closing line.")
    assert catalog.get_document(pdf_doc["id"])["ext"] == ".pdf" and len(stored_files()) == 1
    assert not (config.DATA_DIR / "versions" / pdf_doc["id"]).exists() or \
        not list((config.DATA_DIR / "versions" / pdf_doc["id"]).iterdir())       # no backup left behind


# ---------------------------------------------------------------- converted, when the look can't be kept
def test_editing_a_pdf_converts_it_when_agreed_and_keeps_the_original(pdf_doc):
    out = editing.edit(pdf_doc["id"], "replace", convert_pdf=True, **CROSS)
    assert out["converted"] and out["changes"] == 1 and "more than one paragraph" in out["conversion_reason"]
    now = catalog.get_document(pdf_doc["id"])
    assert now["ext"] == ".md" and now["file_name"].endswith(".md") and stored_files() == [now["file_name"]]
    assert "March in Pokhara" in (config.LIBRARY_DIR / now["file_name"]).read_text(encoding="utf-8")
    assert "Pokhara" in chunk_texts(pdf_doc["id"]) and "Kathmandu" not in chunk_texts(pdf_doc["id"])
    assert now["pages"] == pdf_doc["pages"]
    saved = list((config.DATA_DIR / "versions" / pdf_doc["id"]).iterdir())
    assert [p.suffix for p in saved] == [".pdf"] and saved[0].read_bytes() == make_pdf(LINES)
    assert catalog.edit_history(pdf_doc["id"])[0]["detail"].startswith("converted the PDF to Markdown")


def test_an_agreed_conversion_still_edits_in_place_when_it_can(pdf_doc):
    out = editing.edit(pdf_doc["id"], "replace", find="March", text="June", convert_pdf=True)
    assert not out["converted"] and catalog.get_document(pdf_doc["id"])["ext"] == ".pdf"


def test_undo_brings_the_pdf_back(pdf_doc):
    editing.edit(pdf_doc["id"], "replace", convert_pdf=True, **CROSS)
    out = editing.undo(pdf_doc["id"])
    now = catalog.get_document(pdf_doc["id"])
    assert out["status"] == "undone" and now["ext"] == ".pdf" and now["file_name"].endswith(".pdf")
    assert stored_files() == [now["file_name"]]
    assert (config.LIBRARY_DIR / now["file_name"]).read_bytes() == make_pdf(LINES)
    assert "Kathmandu" in chunk_texts(pdf_doc["id"]) and "Pokhara" not in chunk_texts(pdf_doc["id"])


def test_a_failed_pdf_edit_leaves_no_stray_copy(pdf_doc):
    with pytest.raises(editing.EditError, match="isn't in the document"):
        editing.edit(pdf_doc["id"], "replace", find="nothing like this", text="x", convert_pdf=True)
    out = editing.edit(pdf_doc["id"], "replace", find="e", text="E", convert_pdf=True)   # several places
    assert out["status"] == "ambiguous"
    assert catalog.get_document(pdf_doc["id"])["ext"] == ".pdf" and len(stored_files()) == 1


def test_agent_asks_before_converting_a_pdf(pdf_doc):
    call = {"doc_id": pdf_doc["id"], "operation": "replace", **CROSS}
    _, results = turn("pdf1", [("find_documents", {"description": "budget.pdf"})], [("edit_document", call)])
    assert results[-1]["status"] == "needs_confirmation" and results[-1]["reason"] == "pdf_conversion"
    assert "more than one paragraph" in results[-1]["why"]
    _, results = turn("pdf1", [("edit_document", {**call, "convert_pdf": True})])      # the model can't also agree
    assert results[-1]["status"] == "edited" and results[-1]["converted"]

    editing.undo(pdf_doc["id"])                                                         # a PDF again
    _, results = turn("pdf2", [("find_documents", {"description": "budget.pdf"})],
                      [("edit_document", {**call, "find": "Report The launch", "text": "Report. A launch",
                                          "convert_pdf": True})])
    assert results[-1]["status"] == "needs_confirmation"                               # same message: not enough


def test_agent_edits_a_pdf_in_place_without_asking(pdf_doc):
    call = {"doc_id": pdf_doc["id"], "operation": "replace", "find": "March", "text": "June"}
    _, results = turn("pdf3", [("find_documents", {"description": "budget.pdf"})], [("edit_document", call)])
    assert results[-1]["status"] == "edited" and not results[-1]["converted"]


# ---------------------------------------------------------------- the layout rules, on a made-up page
HELV = pymupdf.Font("helv")


def write(page, x, y, text, size=11):
    tw = pymupdf.TextWriter(page.rect)
    tw.append((x, y), text, font=HELV, fontsize=size)
    tw.write_text(page)


def justified(page, y, words, left, right, size=11):
    """One line of words spread so it runs from `left` to `right`."""
    widths = [HELV.text_length(w, size) for w in words]
    gap = (right - left - sum(widths)) / (len(words) - 1)
    x = left
    for w, width in zip(words, widths):
        write(page, x, y, w, size)
        x += width + gap


PARAGRAPH = ["Our team builds tools that help people find and change their documents quickly and safely",
             "with every change checked, saved as a version and easy to undo whenever the result looks wrong",
             "so nobody loses work, and the library stays tidy and searchable for everyone who uses it daily."]


@pytest.fixture
def layout_pdf(tmp_path) -> Path:
    d = pymupdf.open()
    page = d.new_page(width=612, height=792)
    title = "Annual Project Summary"
    write(page, 306 - HELV.text_length(title, 18) / 2, 80, title, 18)               # centred
    write(page, 72, 120, "Senior Analyst")                                           # left ...
    write(page, 540 - HELV.text_length("2021 - 2024", 11), 120, "2021 - 2024")       # ... and right on one row
    write(page, 72, 135, "Kathmandu Office")
    write(page, 540 - HELV.text_length("Full time", 11), 135, "Full time")
    for i, line in enumerate(PARAGRAPH[:-1]):                                        # a justified paragraph
        justified(page, 170 + 14 * i, line.split(), 72, 540)
    write(page, 72, 170 + 14 * 2, PARAGRAPH[-1])                                     # its last line, ragged
    path = tmp_path / "layout.pdf"
    d.save(str(path))
    return path


def edited(src: Path, find: str, new: str, replace_all=False) -> Path:
    out = src.with_name("out.pdf")
    pdf_edit.replace(src, out, re.compile(r"\s+".join(map(re.escape, find.split()))), new, replace_all)
    return out


def extent(path: Path, text: str) -> tuple[float, float]:
    """Left and right edge of the line holding `text`."""
    pdf = pdf_edit.PdfText(path)
    try:
        seg = next(s for s in pdf.segments if text in s.text)
        return seg.x0, seg.x1
    finally:
        pdf.close()


def test_the_page_layout_is_read(layout_pdf):
    pdf = pdf_edit.PdfText(layout_pdf)
    aligns = {s.text.split()[0]: s.align for s in pdf.segments}
    pdf.close()
    assert aligns["Annual"] == "center" and aligns["Senior"] == "left" and aligns["2021"] == "right"
    assert aligns["Our"] == aligns["with"] == "justify" and aligns["so"] == "left"


def test_a_right_aligned_edit_keeps_its_right_edge(layout_pdf):
    out = edited(layout_pdf, "2021 - 2024", "2019 - 2024")
    assert abs(extent(out, "2019 - 2024")[1] - extent(layout_pdf, "2021 - 2024")[1]) < 0.3


def test_a_centred_edit_stays_centred(layout_pdf):
    out = edited(layout_pdf, "Annual", "Final Annual")
    x0, x1 = extent(out, "Final Annual Project Summary")
    assert abs((x0 + x1) / 2 - 306) < 0.5


def test_a_justified_edit_still_fills_the_line(layout_pdf):
    out = edited(layout_pdf, "quickly and", "swiftly and")
    x0, x1 = extent(out, "swiftly and safely")
    assert abs(x0 - 72) < 0.3 and abs(x1 - 540) < 0.3


def test_text_that_wont_fit_beside_other_text_is_refused(layout_pdf):
    """A job title with its dates on the same row: a longer title has nowhere to go."""
    with pytest.raises(pdf_edit.CantKeepLayout, match="beside the paragraph"):
        edited(layout_pdf, "Senior Analyst", "Senior Analyst and Head of the Regional Data Platform Engineering and "
                                             "Infrastructure Group")
    assert not layout_pdf.with_name("out.pdf").exists()


def lines_of(path: Path, page: int = 0) -> list:
    pdf = pdf_edit.PdfText(path)
    try:
        return [(round(s.x0, 1), round(s.x1, 1), round(s.baseline, 1), s.text) for row in pdf.pages[page] for s in row]
    finally:
        pdf.close()


def test_a_longer_edit_rewraps_its_justified_paragraph(layout_pdf):
    out = edited(layout_pdf, "quickly", "very quickly and without any fuss at all")
    body = [line for line in lines_of(out) if line[2] >= 170]
    text = " ".join(t for *_, t in body)
    assert text == " ".join(PARAGRAPH).replace("quickly", "very quickly and without any fuss at all")
    assert len(body) == 4 and [b for *_, b, _ in body] == [170, 184, 198, 212]      # one more line, same spacing
    for x0, x1, _, _ in body[:-1]:
        assert abs(x0 - 72) < 0.3 and abs(x1 - 540) < 0.3                           # still justified
    assert body[-1][1] < 540                                                        # the last line ragged
    (new,) = spans(out, "fuss")
    (old,) = spans(out, "daily.")        # a word the edit didn't touch (the saved file's fonts are subset, renamed)
    (before,) = spans(layout_pdf, "daily.")
    assert new["font"] == old["font"] and new["size"] == old["size"] == before["size"]


def test_a_shorter_edit_pulls_words_back_up(layout_pdf):
    out = edited(layout_pdf, "quickly and safely", "fast")
    body = [line for line in lines_of(out) if line[2] >= 170]
    assert " ".join(t for *_, t in body) == " ".join(PARAGRAPH).replace("quickly and safely", "fast")
    assert abs(body[0][1] - 540) < 0.3                                              # the first line still full


@pytest.fixture
def stacked_pdf(tmp_path) -> Path:
    """A justified paragraph whose last line is full, then a rule, a heading and a footer that repeats."""
    d = pymupdf.open()
    for n in range(2):
        page = d.new_page(width=612, height=792)
        write(page, 72, 760, f"Page {n + 1} of 2", 9)                                # a running footer
        if n:
            write(page, 72, 700, "The second page reaches lower down.")
            continue
        words = (PARAGRAPH[0] + " " + PARAGRAPH[1]).split()
        justified(page, 100, words[:15], 72, 540)
        justified(page, 114, words[15:], 72, 540)
        write(page, 72, 128, "and that is all.")
        page.draw_line((72, 140), (540, 140))
        page.draw_line((72, 170), (72, 200), width=4.25)                          # a thick bar at the body's edge
        write(page, 72, 160, "Next Section", 14)
    path = tmp_path / "stacked.pdf"
    d.save(str(path))
    return path


def test_a_paragraph_that_needs_a_line_more_moves_what_is_below_down(stacked_pdf):
    out = edited(stacked_pdf, "and that is all.", "and that is all, with every word set again in its own font and "
                                                   "spacing so the paragraph needs another line.")
    before, after = lines_of(stacked_pdf), lines_of(out)
    heading = lambda ls: next(b for *_, b, t in ls if t == "Next Section")      # noqa: E731
    footer = lambda ls: next(b for *_, b, t in ls if t.startswith("Page 1"))    # noqa: E731
    assert abs(heading(after) - heading(before) - 14) < 0.05                        # one line lower
    assert footer(after) == footer(before)                                          # the footer stays
    with pymupdf.open(str(out)) as doc:
        rules = sorted((d["rect"] for d in doc[0].get_drawings()), key=lambda r: r.y0)
    assert len(rules) == 2                                                          # moved, not copied
    assert abs(rules[0].y0 - 154) < 0.3 and abs(rules[1].y0 - 184) < 0.3            # both one line lower
    from backend.rag import ingestion
    text = " ".join(ingestion.load_file(out)[0][0].page_content.split())
    assert text.count("Next Section") == 1 and "needs another line." in text        # read once, in order
    assert text.index("needs another line.") < text.index("Next Section")


def test_a_paragraph_that_needs_a_line_less_closes_the_gap(stacked_pdf):
    out = edited(stacked_pdf, "and that is all.", "")
    before, after = lines_of(stacked_pdf), lines_of(out)
    heading = lambda ls: next(b for *_, b, t in ls if t == "Next Section")      # noqa: E731
    assert abs(heading(before) - heading(after) - 14) < 0.05                        # one line higher


def test_what_no_longer_fits_on_a_full_page_moves_to_a_new_page(stacked_pdf, tmp_path):
    with pymupdf.open(str(stacked_pdf)) as d:
        write(d[0], 72, 705, "Body text already as low as the other page goes.")
        full = tmp_path / "full.pdf"
        d.save(str(full))
    out, info = tmp_path / "grown.pdf", {}
    pdf_edit.replace(full, out, re.compile(r"and that is all\."), "and that is all, with every word set again in its "
                     "own font and spacing so the paragraph needs another line.", False, info)
    assert info["layout"] == "new_page" and info["new_pages"] == [2] and info["added_lines"] == 1
    with pymupdf.open(str(out)) as doc:
        assert len(doc) == 3
        assert "Body text already as low" not in doc[0].get_text()           # moved off its page...
        assert "Page 1 of 3" in doc[0].get_text()                            # ...its footer stays, renumbered
        assert doc[1].get_text().split() == "Body text already as low as the other page goes. Page 2 of 3".split()
        assert "The second page reaches lower down." in doc[2].get_text()   # the old page 2, now 3
        assert "Page 3 of 3" in doc[2].get_text()
        assert all("of 2" not in page.get_text() for page in doc)
        footers = [next(b for *_, b, t in lines_of(out, n) if t.startswith("Page")) for n in range(3)]
        assert len(set(footers)) == 1                                        # all at the footer's height
    baseline = lines_of(out, 1)[0][2]
    assert baseline < 120                                                    # at the top of the body, not at 705


def test_a_paragraph_that_would_run_past_its_page_is_refused(tmp_path):
    d = pymupdf.open()
    page = d.new_page(width=612, height=792)
    write(page, 72, 100, "A first line near the top of the page.")
    words = (PARAGRAPH[0] + " " + PARAGRAPH[1]).split()
    justified(page, 690, words[:15], 72, 540)
    justified(page, 704, words[15:], 72, 540)
    write(page, 72, 718, "and that is all.")
    src = tmp_path / "low.pdf"
    d.save(str(src))
    with pytest.raises(pdf_edit.NoRoom, match="past the bottom of its page"):
        edited(src, "and that is all.", "and that is all, " + "with many more words added to it " * 12)


def test_a_failed_check_leaves_no_file(layout_pdf, monkeypatch):
    real = pdf_edit._apply_page

    def and_a_stray_word(page, plan, original=None, own_fonts=True):
        real(page, plan, original, own_fonts)
        write(page, 72, 400, "stray")
    monkeypatch.setattr(pdf_edit, "_apply_page", and_a_stray_word)
    with pytest.raises(pdf_edit.CantKeepLayout, match="Checking the edited page failed"):
        edited(layout_pdf, "Senior", "Junior")
    assert not layout_pdf.with_name("out.pdf").exists()


def test_an_edit_keeps_the_reading_order(layout_pdf):
    """Text readers (search, copy and paste, the Markdown copy) read the edited page in its original order."""
    from backend.rag import ingestion
    before = ingestion.load_file(layout_pdf)[0][0].page_content
    out = edited(layout_pdf, "Senior Analyst", "Lead Analyst")
    assert ingestion.load_file(out)[0][0].page_content == before.replace("Senior Analyst", "Lead Analyst")


def test_replace_all_changes_every_line(layout_pdf):
    out = layout_pdf.with_name("out.pdf")
    changes, _, _ = pdf_edit.replace(layout_pdf, out, re.compile(r"\band\b"), "plus", True)
    text = pdf_edit.document_text(out)
    assert changes == 5 and " and " not in text and text.count(" plus ") == 5 and "Kathmandu" in text


def test_missing_letters_come_from_a_complete_copy_of_the_font():
    path = Path(__file__).parents[2] / "library" / "fonts" / "cm" / "cmr10.pfb"
    assert pdf_edit.find_font_file("ABCDEF+CMR10") == path
    assert pdf_edit.font_key("Arial-BoldMT") == pdf_edit.font_key("Arial,Bold") == "arialbold"
    assert pdf_edit.font_key("TimesNewRomanPSMT") == "timesnewroman"


def test_a_real_latex_paper_is_edited_in_its_own_fonts(tmp_path):
    src = Path(__file__).parents[3] / "test_corpus" / "files" / "ml" / "1706.03762.pdf"
    if not src.exists():
        pytest.skip("test corpus not downloaded (python scripts/fetch_test_corpus.py)")
    copy = tmp_path / "paper.pdf"
    copy.write_bytes(src.read_bytes())
    out = edited(copy, "dominant sequence transduction models", "leading sequence transduction models")
    from backend.rag import ingestion
    text = ingestion.load_file(out)[0][0].page_content
    assert "leading sequence transduction models" in " ".join(text.split())       # with its spaces, for search
    (new,) = [s for s in spans(out, "leading")]
    (old,) = [s for s in spans(src, "dominant")]
    assert new["font"] == old["font"] and abs(new["size"] - old["size"]) < 0.01


# ---------------------------------------------------------------- the whole-result checks
LONGER = "and that is all, with every word set again in its own font and spacing so the paragraph needs another line."


def test_a_result_with_its_words_out_of_order_is_refused(layout_pdf, monkeypatch):
    real = pdf_edit.document_text
    monkeypatch.setattr(pdf_edit, "document_text",
                        lambda path: real(path).replace("find and change", "change and find"))
    with pytest.raises(pdf_edit.CantKeepLayout, match="no longer reads as intended"):
        edited(layout_pdf, "Senior", "Junior")
    assert not layout_pdf.with_name("out.pdf").exists()


def test_a_stray_mark_outside_the_edit_is_refused(stacked_pdf, monkeypatch):
    real = pdf_edit._apply_page

    def and_a_mark(page, plan, original=None, own_fonts=True):
        real(page, plan, original, own_fonts)
        page.draw_rect(pymupdf.Rect(300, 40, 320, 50), fill=(0, 0, 0))
    monkeypatch.setattr(pdf_edit, "_apply_page", and_a_mark)
    with pytest.raises(pdf_edit.CantKeepLayout, match="looks different outside the edit"):
        edited(stacked_pdf, "and that is all.", LONGER)


def test_what_moved_must_look_as_it_did(stacked_pdf, monkeypatch):
    real = pdf_edit._draw_region

    def drawn_a_bit_off(page, shift, original, links):
        real(page, pdf_edit._Shift(shift.region, shift.dy + 0.8), original, links)
    monkeypatch.setattr(pdf_edit, "_draw_region", drawn_a_bit_off)
    with pytest.raises(pdf_edit.CantKeepLayout, match="Checking the edited page failed"):
        edited(stacked_pdf, "and that is all.", LONGER)


# ---------------------------------------------------------------- words broken over two lines
def test_a_line_end_hyphen_is_told_apart_from_a_real_one():
    text = "We set an example here. A well-known tool."
    assert pdf_edit.line_break_hyphen("exam-", "ple", text)              # the document has "example"
    assert not pdf_edit.line_break_hyphen("well-", "known", text)        # ... and "well-known" within a line
    assert pdf_edit.line_break_hyphen("docu-", "ments", "")              # the dictionary allows docu|ments
    assert not pdf_edit.line_break_hyphen("real-", "time", "")           # a common compound
    assert not pdf_edit.line_break_hyphen("Phelan-", "McDermid", "")     # a capital after it: a name
    assert not pdf_edit.line_break_hyphen("2026-", "10", "")


def test_a_broken_word_is_rejoined_without_its_hyphen(tmp_path):
    d = pymupdf.open()
    page = d.new_page(width=612, height=792)
    justified(page, 100, "Our team builds tools that help people find and change their files".split(), 72, 400)
    justified(page, 114, "and keeps every one of the important useful project notes docu-".split(), 72, 400)
    write(page, 72, 128, "ments safe and easy to find.")
    path = tmp_path / "broken.pdf"
    d.save(str(path))
    out = edited(path, "every one of the important", "all the")
    text = " ".join(pdf_edit.document_text(out).split())
    assert "notes documents safe" in text and "docu-" not in text


def test_a_shorter_ragged_line_pulls_the_next_word_up(layout_pdf):
    """The ragged last line of the fixture's paragraph is too short to show this; a ragged paragraph is made."""
    d = pymupdf.open()
    page = d.new_page(width=612, height=792)
    for i, line in enumerate(["Our team builds tools that help people find and change",
                              "their documents quickly and safely, with every change",
                              "checked and saved."]):
        write(page, 72, 100 + 14 * i, line)
    path = layout_pdf.with_name("ragged.pdf")
    d.save(str(path))
    out = edited(path, "builds tools that help people", "helps people")
    rows = [t for *_, t in lines_of(out)]
    assert rows[0].endswith("their") or rows[0].endswith("documents")        # words came up from line two
    assert " ".join(rows) == ("Our team helps people find and change their documents quickly and safely, with every "
                              "change checked and saved.")


def test_words_are_broken_only_where_the_document_breaks_words(layout_pdf, tmp_path):
    out = edited(layout_pdf, "quickly", "very quickly and without any fuss at all")
    assert not any(t.endswith("-") for *_, t in lines_of(out))           # this document never breaks words

    d = pymupdf.open()                                                    # one that does
    page = d.new_page(width=612, height=792)
    justified(page, 100, "Our team builds tools that help people find and change their files".split(), 72, 400)
    justified(page, 114, "and keeps every one of the important useful project notes docu-".split(), 72, 400)
    justified(page, 128, "ments safe, searchable and easy to find for everyone who needs".split(), 72, 400)
    write(page, 72, 142, "them every single day.")
    path = tmp_path / "hyphenating.pdf"
    d.save(str(path))
    assert pdf_edit.PdfText(path).hyphenates()
    out = edited(path, "find and change", "find, understand, reorganise and change")
    words = " ".join(" ".join(pdf_edit.document_text(out).split()).replace("- ", "").split())
    assert "find, understand, reorganise and change" in words               # read back whole, in order


# ---------------------------------------------------------------- the edit's layout, shown with Preview and Undo
def test_an_edit_says_how_much_of_the_layout_changed(pdf_doc, signed_in):
    out = editing.edit(pdf_doc["id"], "replace", find="March", text="June")
    assert out["layout"] == "line"
    r = signed_in.post(f"/api/library/documents/{pdf_doc['id']}/undo")
    assert r.status_code == 200 and r.json()["status"] == "undone"
    assert any(d["id"] == pdf_doc["id"] for d in r.json()["documents"])
    path = config.LIBRARY_DIR / catalog.get_document(pdf_doc["id"])["file_name"]
    assert path.read_bytes() == make_pdf(LINES)
    assert signed_in.post(f"/api/library/documents/{pdf_doc['id']}/undo").status_code == 409     # nothing left to undo
    assert signed_in.post("/api/library/documents/nope/undo").status_code == 404


def test_a_rewrap_is_reported(layout_pdf):
    info = {}
    pdf_edit.replace(layout_pdf, layout_pdf.with_name("out.pdf"), re.compile("quickly"),
                     "very quickly and without any fuss at all", False, info)
    assert info["layout"] in ("rewrapped", "moved")
