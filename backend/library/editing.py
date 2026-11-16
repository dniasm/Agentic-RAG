"""Editing the text of an ingested document: replace a passage or append text, then re-index it.

  * .txt / .md and .docx are edited in place. A PDF is edited in place too (backend/library/pdf_edit.py: the new text in
    the document's own font, size, colour and spacing, checked after writing) when that can be done without breaking
    its look. When it can't (text that would have to move to another line, a font with no complete copy, text added
    at the end), the edit is refused with the reason, and with the user's agreement the PDF is converted to a
    Markdown copy of its text, which is then edited. Layout and images are lost then; the PDF itself is kept in
    data/versions/ and undo puts it back.
  * Before every edit the current file is copied to data/versions/<doc_id>/, so nothing is lost.
  * Re-indexing uses day7's own loading, cleaning and splitting. Only chunks whose text changed are embedded (one
    Gemini request each); unchanged chunks keep their stored vectors. New chunks are embedded first and only then
    written over the old ones, so a failed embedding (rate limit, network) leaves the index as it was and the file
    is put back.
"""
from collections import Counter
import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from qdrant_client import models

from backend.library import catalog, config, library, pdf_edit, titles
from backend.library.logs import event
from backend.rag import ingestion, sparse, vectorstore
from backend.config.settings import settings as day7_settings

log = logging.getLogger(__name__)

EDITABLE = {".txt", ".md", ".docx"}
MAX_APPEND_CHARS = 20_000
MAX_REWRITE_CHARS = 2_000_000   # the whole text of a TXT / MD file saved from the website's editor


class EditError(Exception):
    pass


class EditConflict(EditError):
    """The document changed since the caller read it: nothing was saved."""


class NeedsConversion(EditError):
    """The PDF can't be changed in place without breaking its look (the message says why), and the caller has not
    agreed to convert it to a Markdown copy. too_long: only because the new text needs more room than the page has,
    so a shorter wording may still fit. missing: the new text's letters the PDF's font has no copy of, so a wording
    without them may still fit."""

    def __init__(self, message: str, too_long: bool = False, missing: str = "", convertible: bool = True):
        super().__init__(message)
        self.too_long = too_long
        self.missing = missing
        self.convertible = convertible   # the passage is in the text the PDF would become: converting can help


def _pattern(find: str, ignore_case: bool = False) -> re.Pattern:
    """Exact text, but any run of whitespace in `find` matches any whitespace (line breaks differ in the file)."""
    words = find.split()
    if not words:
        raise EditError("Say which text to change.")
    return re.compile(r"\s+".join(re.escape(w) for w in words), re.IGNORECASE if ignore_case else 0)


_LINE_END_HYPHEN = re.compile(r"(\w)-\s+(\w)")
_MARKS = r"([*_`]{0,3})"
_QUOTE_CLASSES = {"'": "['\u2018\u2019]", "\u2018": "['\u2018\u2019]", "\u2019": "['\u2018\u2019]",
                  '"': '["\u201c\u201d]', "\u201c": '["\u201c\u201d]', "\u201d": '["\u201c\u201d]'}


def _loose_word(word: str) -> str:
    """A word as a pattern in which any kind of quote matches any other (a phone types I'm; the PDF may have I’m)."""
    return "".join(_QUOTE_CLASSES.get(c) or re.escape(c) for c in word)


def _loose_patterns(find: str, ext: str) -> list[re.Pattern]:
    """Tried, in order, when `find` isn't in the document as written, for an edit made from the website (the chat
    agent quotes the file, so it never gets these). Text selected in the preview can differ from the file: a word the
    page broke at a line end comes as "exam- ple" (the file has "example", or "self-contained"), and a Markdown file is
    shown without its bold / italic / code marks ("**key** point" is selected as "key point"). The marks are only
    looked for in a .md file, where they are marks; elsewhere *, _ and ` are the text's own letters."""
    rejoined = _LINE_END_HYPHEN.sub(r"\1\2", find)          # "exam- ple" -> "example"
    hyphened = _LINE_END_HYPHEN.sub(r"\1-\2", find)         # "self- contained" -> "self-contained"
    variants = [t for t in dict.fromkeys([find, rejoined, hyphened]) if t.split()]
    out = [re.compile(r"\s+".join(_loose_word(w) for w in t.split()), re.IGNORECASE) for t in variants]
    if ext == ".md":
        for text in variants:   # each word's surrounding marks captured: _marks_balanced checks them
            out.append(re.compile(r"\s+".join(_MARKS + _loose_word(w) + _MARKS for w in text.split()), re.IGNORECASE))
    return out


def _marks_balanced(m: re.Match) -> bool:
    """A Markdown-tolerant match takes the marks around its words with it. Only a match that takes whole pairs keeps
    the formatting around it intact: "**key** point" may go, but "text** and" out of "**bold text** and" would leave
    "**bold" unclosed. Only the captured marks count, not a word's own letters (my_var)."""
    # counted per mark as taken (not joined: "*" + "*" around an italic word is a pair, not one "**")
    taken = Counter(g for g in m.groups() if g)
    return all(n % 2 == 0 for n in taken.values())


def _versions_dir(doc_id: str) -> Path:
    return config.DATA_DIR / "versions" / doc_id


def _backup(doc: dict) -> Path:
    folder = _versions_dir(doc["id"])
    folder.mkdir(parents=True, exist_ok=True)
    # after the highest number there, not the file count: a stray file must never make a name collide with a backup
    n = max((int(p.stem) for p in folder.iterdir() if p.stem.isdigit()), default=0) + 1
    target = folder / f"{n:03d}{doc['ext']}"
    shutil.copy2(config.LIBRARY_DIR / doc["file_name"], target)
    return target


def _context(text: str, m: re.Match, width: int = 70) -> str:
    return "…" + " ".join(text[max(0, m.start() - width): m.end() + width].split()) + "…"


PREVIEW_CHARS = 20      # characters of the document shown on each side of a change
MAX_PREVIEWS = 3
_SPACES = re.compile(r"\s+")


def _preview(source: str, start: int, end: int, new: str) -> dict:
    """The change with PREVIEW_CHARS of the document around it: what the user is shown."""
    return {"lead": _SPACES.sub(" ", source[max(0, start - PREVIEW_CHARS): start]),
            "old": _SPACES.sub(" ", source[start:end])[:150], "new": new[:150],
            "trail": _SPACES.sub(" ", source[end: end + PREVIEW_CHARS])}


def document_text(doc: dict) -> str:
    """The document's text as an edit sees it (a PDF: the text its Markdown copy would have)."""
    path = config.LIBRARY_DIR / doc["file_name"]
    if doc["ext"] == ".docx":
        import docx
        return "\n".join(p.text for p in _docx_paragraphs(docx.Document(str(path))))
    if doc["ext"] in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="ignore")
    pages, _ = ingestion.load_file(path)
    return "\n\n".join(p.page_content for p in pages)


OUTLINE_MAX = 40   # headings sent to the agent
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_NUMBERED = re.compile(r"^(\d+(\.\d+)*\.?|[IVX]+\.)\s+[A-Z]")


def _looks_like_heading(line: str) -> bool:
    """A plain-text line that reads as a heading: short, no closing punctuation, and in capitals ("AT A GLANCE"),
    numbered ("2.1 Results") or bold on its own ("**Results**"). PDFs keep no heading styles, so this is a guess."""
    line = line.strip().strip("*").strip()
    letters = [c for c in line if c.isalpha()]
    if not 3 <= len(line) <= 80 or len(letters) < 3 or line[-1] in ".,;:)":
        return False
    return line.isupper() or bool(_NUMBERED.match(line))


def outline(doc: dict) -> list[str]:
    """The document's headings, in order: Word heading styles, Markdown "#" lines, or (a PDF, plain text, or a file
    without either) lines that look like headings. At most OUTLINE_MAX."""
    path = config.LIBRARY_DIR / doc["file_name"]
    found: list[str] = []
    if doc["ext"] == ".docx":
        import docx
        paragraphs = [p for p in docx.Document(str(path)).paragraphs if p.text.strip()]
        found = [p.text.strip() for p in paragraphs
                 if (p.style.name or "").lower().startswith(("heading", "title"))]
        lines = [p.text for p in paragraphs]
    else:
        lines = document_text(doc).splitlines()
        if doc["ext"] == ".md":
            found = [m.group(1) for m in map(_MD_HEADING.match, lines) if m]
    if not found:
        found = [line.strip().strip("*").strip() for line in lines if _looks_like_heading(line)]
    out = []
    for h in found:
        if not out or out[-1] != h:
            out.append(h)
    return out[:OUTLINE_MAX]


PASSAGE_BEFORE, PASSAGE_AFTER = 100, 900


def passage(doc: dict, words: str) -> str | None:
    """The text around the first place `words` appear (case and line breaks ignored), so the agent can quote a
    passage exactly before replacing it. None if they don't appear."""
    if not (words or "").split():
        return None
    text = document_text(doc)
    m = _pattern(words, ignore_case=True).search(text)
    if not m:
        return None
    return text[max(0, m.start() - PASSAGE_BEFORE): m.end() + PASSAGE_AFTER]


# ---------------------------------------------------------------- applying an edit to the file
def _docx_paragraphs(d):
    yield from d.paragraphs
    for table in d.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs


def _replace_in_paragraph(p, pattern: re.Pattern, new: str, remaining: int | None, previews: list) -> int:
    """Replace in one paragraph, keeping run formatting when each match sits inside one run. Returns the count."""
    total = len(pattern.findall(p.text))
    if not total:
        return 0
    count = total if remaining is None else min(total, remaining)
    for m in list(pattern.finditer(p.text))[:count]:
        if len(previews) < MAX_PREVIEWS:
            previews.append(_preview(p.text, m.start(), m.end(), new))
    per_run = sum(len(pattern.findall(r.text)) for r in p.runs)
    if per_run == total:
        left = count
        for r in p.runs:
            if left and pattern.search(r.text):
                r.text, n = pattern.subn(lambda _m: new, r.text, count=left)
                left -= n
    elif p.runs:  # the match spans runs: rewrite the paragraph in its first run
        p.runs[0].text = pattern.sub(lambda _m: new, p.text, count=count)
        for r in p.runs[1:]:
            r.text = ""
    return count


def _match_texts(doc: dict) -> list[str]:
    """The texts an edit's pattern is matched in: a Word file's paragraphs, a PDF's text, a text file whole."""
    path = config.LIBRARY_DIR / doc["file_name"]
    if doc["ext"] == ".docx":
        import docx
        return [p.text for p in _docx_paragraphs(docx.Document(str(path)))]
    if doc["ext"] == ".pdf":
        return [_pdf_text(path)]
    return [path.read_text(encoding="utf-8", errors="ignore")]


def _matches(doc: dict, pattern: re.Pattern) -> list[re.Match]:
    return [m for t in _match_texts(doc) for m in pattern.finditer(t)]


def _count_matches(doc: dict, pattern: re.Pattern) -> tuple[int, list[str]]:
    texts = _match_texts(doc)
    count, contexts = 0, []
    for t in texts:
        for m in pattern.finditer(t):
            count += 1
            if len(contexts) < 4:
                contexts.append(_context(t, m))
    return count, contexts


def _pdf_text(path: Path) -> str:
    """A PDF's text as an in-place edit matches it. A PDF whose layout can't be read the way an edit needs it
    (encrypted, damaged) can only be converted."""
    try:
        return pdf_edit.document_text(path)
    except pdf_edit.CantKeepLayout:
        raise
    except Exception as e:
        raise pdf_edit.CantKeepLayout("The PDF's text layout can't be read for an in-place edit.") from e


# Typographic letters a PDF's embedded font often lacks (a model writes non-breaking hyphens and curly quotes),
# and the plain ones typed on a keyboard: used only when the PDF can't take the text as given.
_PLAIN_LETTERS = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
                                "\u2212": "-", "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201c": '"',
                                "\u201d": '"', "\u201e": '"', "\u2026": "...", "\u00a0": " ", "\u202f": " ",
                                "\u2009": " "})


# The keyboard's quotes, for a font that has only the typographic ones (TeX's Computer Modern draws ' as ’): an opening
# one at a word start (after the start, a space, an opening bracket or a dash), a closing one elsewhere.
_CURLY = {"'": ("\u2018", "\u2019"), '"': ("\u201c", "\u201d")}
_WORD_START = "\\s([{\u2013\u2014-"


def curly_quotes(text: str, letters: str) -> str:
    """`text` with each of `letters` (' and/or ") as the typographic quote its place calls for."""
    for plain in letters:
        opening, closing = _CURLY[plain]
        text = re.sub(f"(^|(?<=[{_WORD_START}])){re.escape(plain)}", opening, text)
        text = text.replace(plain, closing)
    return text


# Hard-to-see letters, named in messages ("the apostrophe (')") instead of quoted bare.
_LETTER_NAMES = {"'": "the apostrophe (')", '"': 'the straight quote (")', "\u2019": "the curly apostrophe (\u2019)",
                 "\u2018": "the opening quote (\u2018)", "\u201c": "the opening quote (\u201c)",
                 "\u201d": "the closing quote (\u201d)", "-": "the hyphen (-)", "\u2013": "the dash (\u2013)",
                 "\u2014": "the long dash (\u2014)", ".": "the full stop (.)", ",": "the comma (,)"}


def letter_names(letters: str) -> str:
    """ "j'" -> '"j" and the apostrophe (\')' for a message."""
    plain = "".join(c for c in letters if c not in _LETTER_NAMES)
    parts = ([f'"{plain}"'] if plain else []) + [_LETTER_NAMES[c] for c in letters if c in _LETTER_NAMES]
    return " and ".join(parts)


_MD_HEADING_MARK = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.M)
_MD_EMPHASIS = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1|`([^`\n]+)`")


def without_markdown(text: str) -> tuple[str, bool]:
    """A PDF's text can't carry Markdown: "**Profile**" would print its asterisks. Bold and code marks and heading
    hashes are taken out (and line breaks become spaces: the PDF's layout sets the lines). Returns (text, whether any
    Markdown was taken out)."""
    plain = _MD_EMPHASIS.sub(lambda m: m[2] if m[2] is not None else m[3], _MD_HEADING_MARK.sub("", text))
    removed = plain != text
    return re.sub(r"\s*\n\s*", " ", plain).strip() if "\n" in plain else plain, removed


def _pdf_replace(path: Path, tmp: Path, pattern: re.Pattern, text: str, replace_all: bool, info: dict | None):
    """pdf_edit.replace. Where the PDF's font has no copy of a typographic dash, quote or space in the text, only
    those letters become their keyboard ones (listed in info["plain_letters"]); where it has no keyboard quote (TeX's
    fonts draw ' as ’), those become typographic ones (info["curly_letters"]). Each letter is swapped once: a font
    lacking both kinds gets the refusal; any other refusal stands."""
    swapped: set[str] = set()     # letters swapped for a plain one
    curled: set[str] = set()      # keyboard quotes swapped for typographic ones
    produced: set[str] = set()    # letters the swaps wrote: missing as well, nothing is left to try
    text, removed = without_markdown(text)
    if removed and info is not None:
        info["markdown_removed"] = True
    for _ in range(4):   # each try can find another font (bold, italic) lacking another letter
        try:
            out = pdf_edit.replace(path, tmp, pattern, text, replace_all, info)
        except pdf_edit.MissingLetters as e:
            lacking = {c for c in e.missing if c in text}
            if not lacking or lacking & produced:
                raise
            to_curl = {c for c in lacking if c in _CURLY and c not in curled}
            to_plain = {c for c in lacking - to_curl if ord(c) in _PLAIN_LETTERS and c not in swapped}
            if lacking - to_curl - to_plain:
                raise
            if to_plain:
                swapped |= to_plain
                produced |= {_PLAIN_LETTERS[ord(c)] for c in to_plain}
                text = text.translate({ord(c): _PLAIN_LETTERS[ord(c)] for c in to_plain})
            if to_curl:
                curled |= to_curl
                produced |= {q for c in to_curl for q in _CURLY[c]}
                text = curly_quotes(text, "".join(sorted(to_curl)))
            log.info("Edit of %s: its font lacks %r; swapped for the kind it has", path.name, "".join(sorted(lacking)))
            continue
        if info is not None and swapped:
            info["plain_letters"] = "".join(sorted(swapped))
        if info is not None and curled:
            info["curly_letters"] = "".join(sorted(curled))
        if info is not None:
            info["written_text"] = text   # as saved: the history line shows what is in the document
        return (*out, text)
    raise pdf_edit.CantKeepLayout("The PDF's fonts lack too many of the new text's letters.")


def _apply(doc: dict, op: str, pattern: re.Pattern | None, text: str, replace_all: bool,
           info: dict | None = None) -> tuple[int, list]:
    """Write the edited file. Returns (how many places changed, previews of the first few). For a PDF, `info` gets
    how much of its layout changed (pdf_edit.replace)."""
    path = config.LIBRARY_DIR / doc["file_name"]
    tmp = path.with_name(path.name + ".tmp")
    changed, previews = 1, []
    if doc["ext"] == ".pdf":
        if op == "append":
            raise pdf_edit.CantKeepLayout("Text added at the end of a PDF can't yet be laid out to match the rest of it.")
        changed, spans, content, text = _pdf_replace(path, tmp, pattern, text, replace_all, info)
        previews = [_preview(content, a, b, text) for a, b in spans[:MAX_PREVIEWS]]
    elif doc["ext"] == ".docx":
        import docx
        d = docx.Document(str(path))
        if op == "append":
            before = " ".join(p.text for p in d.paragraphs)
            previews.append(_preview(before, len(before), len(before), text))
            d.add_paragraph(text)
        else:
            remaining, changed = (None if replace_all else 1), 0
            for p in _docx_paragraphs(d):
                changed += _replace_in_paragraph(p, pattern, text, None if remaining is None else remaining - changed, previews)
                if remaining is not None and changed >= remaining:
                    break
        d.save(str(tmp))
    elif op == "rewrite":   # the whole text, from the website's editor, with the file's own line endings
        before = path.read_bytes()   # not read_text: it turns \r\n into \n, hiding which line endings the file has
        content = text.replace("\r\n", "\n")
        if b"\r\n" in before:
            content = content.replace("\n", "\r\n")
        previews.append(_preview(content, 0, min(len(content), 200), content[:200]))
        tmp.write_bytes((b"\xef\xbb\xbf" if before.startswith(b"\xef\xbb\xbf") else b"") + content.encode("utf-8"))
    else:
        content = path.read_text(encoding="utf-8", errors="ignore")
        if op == "append":
            content = content.rstrip("\n")
            previews.append(_preview(content, len(content), len(content), text))
            content = content + "\n\n" + text + "\n"
        else:
            matches = list(pattern.finditer(content))
            for m in (matches if replace_all else matches[:1])[:MAX_PREVIEWS]:
                previews.append(_preview(content, m.start(), m.end(), text))
            content, changed = pattern.subn(lambda _m: text, content, count=0 if replace_all else 1)
        tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return changed, previews


# ---------------------------------------------------------------- re-index
def _stored_vectors(doc_id: str, owner: str) -> dict[str, list[float]]:
    """The dense vector of each chunk the document has in the index now, by the chunk's text."""
    client = vectorstore.get_client()
    vectors, offset = {}, None
    with vectorstore._lock:
        while True:
            points, offset = client.scroll(day7_settings.collection_name,
                                           scroll_filter=vectorstore.user_filter(owner, doc_id),
                                           limit=256, offset=offset, with_payload=["page_content"],
                                           with_vectors=[vectorstore.DENSE])
            for p in points:
                vector = p.vector.get(vectorstore.DENSE) if isinstance(p.vector, dict) else p.vector
                if vector is not None:
                    vectors[p.payload["page_content"]] = vector
            if offset is None:
                return vectors


def _reindex(doc: dict) -> dict:
    """day7's ingest_file, but a chunk whose text the index already holds keeps its vector: an edit changes a few
    chunks, and every embedded chunk is one Gemini request of the 1,000 a day. Loading, cleaning and splitting are
    day7's own functions and the points are built the same way (ids doc_id:index, the same metadata), so the index
    is what a full ingest would give. New chunks are embedded before anything is written, so a failed embedding
    leaves the index untouched."""
    path = config.LIBRARY_DIR / doc["file_name"]
    pages, page_count = ingestion.load_file(path)
    if not pages:
        raise ingestion.IngestionError("No extractable text found.")
    chunks = ingestion.split_documents(pages)
    vectors = _stored_vectors(doc["id"], doc["owner"])
    new_texts = list(dict.fromkeys(c.page_content for c in chunks if c.page_content not in vectors))
    if new_texts:
        vectors.update(zip(new_texts, ingestion.embed_paced(new_texts, doc["title"])))

    points = [models.PointStruct(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc['id']}:{idx}")),
        vector={vectorstore.DENSE: vectors[c.page_content], vectorstore.SPARSE: sparse.document_vector(c.page_content)},
        payload={"page_content": c.page_content, "metadata": {
            "user_id": doc["owner"], "doc_id": doc["id"], "source": doc["title"], "page": c.metadata.get("page"),
            "chunk_index": idx, "start_index": c.metadata.get("start_index")}},
    ) for idx, c in enumerate(chunks)]
    vectorstore.upsert(points)
    client = vectorstore.get_client()
    with vectorstore._lock:  # drop old chunks beyond the new end (chunk ids are doc_id:index, so the rest were overwritten)
        stale = vectorstore.user_filter(doc["owner"], doc["id"])
        stale.must.append(models.FieldCondition(key="metadata.chunk_index", range=models.Range(gte=len(points))))
        client.delete(day7_settings.collection_name, points_selector=models.FilterSelector(filter=stale), wait=True)
    library._set_chunk_fields(doc["id"], doc["owner"], title=doc["title"], file_name=doc["file_name"])
    return {"pages": page_count, "chunks": len(points), "embedded": len(new_texts)}


def _plain_text(path: Path) -> str:
    """The text day7 reads from a file (what the summary is written from)."""
    pages, _ = ingestion.load_file(path)
    return "\n\n".join(p.page_content for p in pages)


def _without_line_ends(text: str) -> str:
    return text.replace("\r\n", "\n")


def file_text(doc_id: str) -> dict:
    """A TXT / MD document's whole text for the website's editor, with the SHA-256 of the file it came from (sent
    back with the save, so a save over a change made meanwhile is refused)."""
    doc = catalog.get_document(doc_id)
    if not doc:
        raise EditError("Document not found.")
    if doc["ext"] not in (".txt", ".md"):
        raise EditError("Only a text or Markdown file can be edited as a whole.")
    data = (config.LIBRARY_DIR / doc["file_name"]).read_bytes()
    return {"text": _without_line_ends(_strict_text(data)), "sha256": ingestion.file_sha256(data)}


def _strict_text(data: bytes) -> str:
    """A text file's bytes as UTF-8, or a refusal: opening a Latin-1 file in the editor would show replacement
    characters, and saving would write them into the file."""
    try:
        return data.decode("utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8")
    except UnicodeDecodeError:
        raise EditError("This file isn't UTF-8 text, so it can't be edited here without damaging it. Download it to "
                        "edit it, or change it from the chat.") from None


def rewrite(doc_id: str, text: str, sha256: str) -> dict:
    """Save the whole text of a TXT / MD document from the website's editor: one edit (Undo puts the old text back),
    refused when the file changed since `sha256` was read (another edit, a chat, a queued task)."""
    return _rewrite_locked(doc_id, text, sha256)


@library._exclusive
def _rewrite_locked(doc_id: str, text: str, sha256: str) -> dict:
    doc = catalog.get_document(doc_id)
    if doc and doc["ext"] in (".txt", ".md"):
        now = ingestion.file_sha256((config.LIBRARY_DIR / doc["file_name"]).read_bytes())
        if now != sha256:
            raise EditConflict("The document was changed since it was opened. Reopen it to see the change, then edit again.")
    return _edit_locked(doc_id, "rewrite", text=text)   # the same lock (re-entrant): the check and the save are one step


def changed_share(old: str, new: str) -> float:
    """0-1: how much of the text changed, measured as everything between the common start and the common end.
    Exact for a single change and on the safe side (larger) for several."""
    longest = max(len(old), len(new))
    if not longest:
        return 0.0
    head = len(os.path.commonprefix([old, new]))
    tail = len(os.path.commonprefix([old[head:][::-1], new[head:][::-1]]))
    return (longest - head - tail) / longest


def refresh_expected(doc: dict, op: str, find: str, text: str, replace_all: bool) -> bool:
    """Would this edit change enough of the text for _finish to rewrite the summary (a model call)? Worked out on the
    text alone, before anything is touched, so the queue reserves tokens only for the edits that need them."""
    if doc["ext"] not in EDITABLE | {".pdf"}:
        return False                   # other types can't be edited
    old = _plain_text(config.LIBRARY_DIR / doc["file_name"])
    if op == "append":
        new = old.rstrip("\n") + "\n\n" + text
    else:
        if not find.split():
            return False
        pattern = _pattern(find)
        if not pattern.search(old):
            pattern = _pattern(find, ignore_case=True)
        new = pattern.sub(lambda _m: text, old, count=0 if replace_all else 1)
    return changed_share(old, new) >= config.SUMMARY_REFRESH_SHARE


def undo_refresh_expected(doc: dict) -> bool:
    """Would undoing the document's latest edit change enough text to rewrite the summary? (See refresh_expected.)"""
    last = catalog.last_edit(doc["id"])
    backup = _versions_dir(doc["id"]) / last["backup"] if last else None
    if not backup or not backup.exists():
        return False                   # nothing to undo: the task will say so without a model call
    if backup.suffix != doc["ext"]:
        return True                    # a converted PDF comes back: a different file
    return changed_share(_plain_text(config.LIBRARY_DIR / doc["file_name"]), _plain_text(backup)) \
        >= config.SUMMARY_REFRESH_SHARE


def _finish(doc: dict, reloaded: dict, before: str | None = None) -> bool:
    """After the file and index have changed: update the catalog row. The summary and keywords (which drive search
    by content) are written again by the model, about 2,200 Groq tokens, unless the change is small: under
    SUMMARY_REFRESH_SHARE of the text, the old summary still holds. Returns whether it was refreshed."""
    path = config.LIBRARY_DIR / doc["file_name"]
    data = path.read_bytes()
    from backend.library import export   # imported here: export imports this module
    fields = dict(sha256=ingestion.file_sha256(data), size_bytes=len(data),
                  pages=export.page_count(catalog.get_document(doc["id"]) or doc, reloaded["pages"]),
                  chunks=reloaded["chunks"])
    refreshed = False
    try:
        text = _plain_text(path)
        if before is None or changed_share(before, text) >= config.SUMMARY_REFRESH_SHARE:
            others = [d["title"] for d in catalog.list_documents(owner=doc["owner"])
                      if d["id"] != doc["id"] and d["status"] != "failed"]
            profile, refreshed = titles.profile_document(doc["original_name"], text[: config.TITLE_SOURCE_CHARS], others)
            if refreshed:
                fields.update(summary=profile.summary, keywords=profile.keywords)
    except Exception:
        log.warning("Couldn't refresh the summary of %s", doc["title"], exc_info=True)
    catalog.update_document(doc["id"], **fields)
    return refreshed


@library._exclusive
def undo(doc_id: str, edit_id: int | None = None) -> dict:
    """Put back the document as it was before its most recent edit (one step at a time). With edit_id, only if that
    is still the most recent edit (checked under the same lock as the undo, so two clicks can't both pass)."""
    doc = catalog.get_document(doc_id)
    if not doc:
        raise EditError("Document not found.")
    if doc["status"] != "indexed":
        raise EditError(f"The document is {doc['status']}, not ready to change.")
    last = catalog.last_edit(doc_id)
    if not last:
        raise EditError("There is nothing to undo: this document hasn't been edited.")
    if edit_id is not None and last["id"] != edit_id:
        raise EditError("That edit was already undone, or a later edit came after it.")
    backup = _versions_dir(doc_id) / last["backup"]
    path = config.LIBRARY_DIR / doc["file_name"]
    if not backup.exists() or not path.exists():
        raise EditError("The saved earlier version is missing, so the edit can't be undone.")
    if backup.suffix != doc["ext"]:   # the edit converted the file (PDF to Markdown): the original comes back
        new_name = library.stored_name(doc["owner"], doc["title"], backup.suffix, exclude_doc=doc)
        doc = {**doc, "ext": backup.suffix, "file_name": new_name}
    target = config.LIBRARY_DIR / doc["file_name"]
    before = _plain_text(path)
    current = backup.with_name("current" + path.suffix)
    shutil.copy2(path, current)
    redo = backup.with_name(f"redo-{last['id']}{path.suffix}")     # the edited file, for a redo
    shutil.copy2(path, redo)
    try:
        shutil.copy2(backup, target)
        reloaded = _reindex(doc)
    except Exception as e:
        if target != path:
            target.unlink(missing_ok=True)
        else:
            shutil.copy2(current, path)
        redo.unlink(missing_ok=True)
        log.exception("Undo on %s failed", doc["title"])
        raise EditError(f"The undo failed and the document is unchanged: {str(e)[:200]}") from e
    finally:
        current.unlink(missing_ok=True)
    if target != path:
        path.unlink(missing_ok=True)
    catalog.update_document(doc_id, ext=doc["ext"], file_name=doc["file_name"])
    refreshed = _finish(doc, reloaded, before)
    catalog.move_edit_to_undone({**last, "doc_id": doc_id}, redo.name, path.suffix)   # the backup stays for a redo
    event("undo", doc=doc_id, title=doc["title"], undid=last["detail"], chunks=reloaded["chunks"],
          embedded=reloaded["embedded"],
          summary_refreshed=refreshed)
    return {"status": "undone", "undid": last["detail"], "title": doc["title"], "redo_id": last["id"],
            "chunks_reindexed": reloaded["chunks"], "edits_left": len(catalog.edit_history(doc_id))}


@library._exclusive
def redo(doc_id: str, edit_id: int | None = None) -> dict:
    """Bring back the edit undone most recently (Forward after Back). With edit_id, only if that is the one. A new
    edit since the undo ends the redo history, so there is then nothing to bring back."""
    doc = catalog.get_document(doc_id)
    if not doc:
        raise EditError("Document not found.")
    if doc["status"] != "indexed":
        raise EditError(f"The document is {doc['status']}, not ready to change.")
    undone = catalog.last_undone(doc_id)
    if not undone:
        raise EditError("There is nothing to redo: no undone edit of this document is waiting.")
    if edit_id is not None and undone["id"] != edit_id:
        raise EditError("That edit can't be redone now: another edit was undone after it.")
    saved = _versions_dir(doc_id) / undone["redo"]
    path = config.LIBRARY_DIR / doc["file_name"]
    if not saved.exists() or not path.exists():
        raise EditError("The saved edited version is missing, so the edit can't be redone.")
    if undone["ext"] != doc["ext"]:   # the edit had converted the file (PDF to Markdown): that copy comes back
        new_name = library.stored_name(doc["owner"], doc["title"], undone["ext"], exclude_doc=doc)
        doc = {**doc, "ext": undone["ext"], "file_name": new_name}
    target = config.LIBRARY_DIR / doc["file_name"]
    before = _plain_text(path)
    current = saved.with_name("current" + path.suffix)
    shutil.copy2(path, current)
    try:
        shutil.copy2(saved, target)
        reloaded = _reindex(doc)
    except Exception as e:
        if target != path:
            target.unlink(missing_ok=True)
        else:
            shutil.copy2(current, path)
        log.exception("Redo on %s failed", doc["title"])
        raise EditError(f"The redo failed and the document is unchanged: {str(e)[:200]}") from e
    finally:
        current.unlink(missing_ok=True)
    if target != path:
        path.unlink(missing_ok=True)
    catalog.update_document(doc_id, ext=doc["ext"], file_name=doc["file_name"])
    refreshed = _finish(doc, reloaded, before)
    catalog.restore_undone(undone["id"])
    saved.unlink(missing_ok=True)
    event("redo", doc=doc_id, title=doc["title"], redid=undone["detail"], chunks=reloaded["chunks"],
          embedded=reloaded["embedded"], summary_refreshed=refreshed)
    return {"status": "redone", "redid": undone["detail"], "title": doc["title"], "edit_id": undone["id"],
            "chunks_reindexed": reloaded["chunks"]}


def _drop_redo_history(doc_id: str) -> None:
    """A new edit: edits undone before it can't come back (their saved files go)."""
    folder = _versions_dir(doc_id)
    for u in catalog.clear_undone(doc_id):
        (folder / u["redo"]).unlink(missing_ok=True)
        (folder / u["backup"]).unlink(missing_ok=True)


def _pdf_to_markdown(doc: dict) -> dict:
    """Write the PDF's text as a Markdown file beside it. Returns the document record pointing at that file."""
    text = _conversion_text(doc)
    name = library.stored_name(doc["owner"], doc["title"], ".md", exclude_doc=doc)
    (config.LIBRARY_DIR / name).write_text(text, encoding="utf-8")
    return {**doc, "ext": ".md", "file_name": name}


def _damage(text: str) -> int:
    """Letters a reading couldn't map: replacement characters, and private-use ones (a font without a proper letter map
    often gives those instead)."""
    return sum(1 for c in text if c == "\ufffd" or "\ue000" <= c <= "\uf8ff")


def _conversion_text(doc: dict) -> str:
    """The text a PDF becomes when converted: the PDF editor's reading (its ToUnicode map: real dashes, words whole,
    the text the preview selects), unless indexing's reading (pypdf) has fewer letters it couldn't map; on a tie the
    editor's, since that is what a selection in the preview matches. Indexing's for a PDF the editor can't read."""
    path = config.LIBRARY_DIR / doc["file_name"]
    try:
        editor = pdf_edit.document_text(path)
    except Exception:
        editor = ""
    try:
        pages, _ = ingestion.load_file(path)
        indexed = "\n\n".join(p.page_content for p in pages)
    except Exception:
        indexed = ""
    if editor.strip() and (not indexed.strip() or _damage(editor) <= _damage(indexed)):
        text = editor
    else:
        text = indexed
    if not text.strip():
        raise EditError("This PDF has no extractable text (it is probably scanned images), so it can't be converted.")
    return text


def _convertible(doc: dict, find: str, loose: bool) -> bool:
    """Would converting the PDF let this edit go through, i.e. is `find` in the text it becomes? Matched as the edit
    would match it in a PDF (no Markdown marks: in a PDF's text * and _ are letters)."""
    try:
        text = _conversion_text(doc)
    except EditError:
        return False
    patterns = [_pattern(find), _pattern(find, ignore_case=True)] + (_loose_patterns(find, ".pdf") if loose else [])
    return any(p.search(text) for p in patterns)


def edit(doc_id: str, op: str, *, find: str = "", text: str = "", replace_all: bool = False,
         convert_pdf: bool = False, loose: bool = False) -> dict:
    """Fonts a PDF may need are downloaded first, outside the library lock (a slow server holds up nothing else);
    then the edit itself (_edit_locked)."""
    doc = catalog.get_document(doc_id)
    if doc and doc.get("ext") == ".pdf":
        pdf_edit.prefetch_fonts(config.LIBRARY_DIR / doc["file_name"])
    return _edit_locked(doc_id, op, find=find, text=text, replace_all=replace_all, convert_pdf=convert_pdf,
                        loose=loose)


@library._exclusive
def _edit_locked(doc_id: str, op: str, *, find: str = "", text: str = "", replace_all: bool = False,
                 convert_pdf: bool = False, loose: bool = False) -> dict:
    """op = "replace" (find -> text) or "append" (text added at the end). Returns what happened.

    Raises EditError when nothing was changed. A replace that matches several places and replace_all is False
    changes nothing and returns {"status": "ambiguous", ...} for the caller to put to the user. A PDF is changed in
    place when its look can be kept; otherwise it raises NeedsConversion (saying why) unless convert_pdf is True,
    and is then converted to a Markdown copy and edited.
    """
    doc = catalog.get_document(doc_id)
    if not doc:
        raise EditError("Document not found.")
    if doc["status"] != "indexed":
        raise EditError(f"The document is {doc['status']}, not ready to edit.")
    if doc["ext"] not in EDITABLE | {".pdf"}:
        raise EditError(f"{doc['ext'].upper().lstrip('.')} files can't be edited here.")
    if op not in ("replace", "append", "rewrite"):
        raise EditError("Unknown edit. Use replace or append.")
    if doc["ext"] != ".pdf":
        return _edit(doc, doc, op, find, text, replace_all, loose)
    try:
        return _edit(doc, doc, op, find, text, replace_all, loose)
    except pdf_edit.CantKeepLayout as e:
        reason, too_long = str(e), isinstance(e, pdf_edit.NoRoom)
        missing = "".join(c for c in getattr(e, "missing", "") if c in text and not c.isspace())
        event("pdf_edit_refused", doc=doc_id, title=doc["title"], reason=reason)
    if not convert_pdf:
        # offered only if the passage is in the text the PDF would become: else converting ends in "not found"
        convertible = op != "replace" or too_long or _convertible(doc, find, loose)
        raise NeedsConversion(reason, too_long, missing, convertible)
    markdown = _pdf_to_markdown(doc)
    try:
        result = _edit(doc, markdown, op, find, text, replace_all, loose)
    except Exception:
        (config.LIBRARY_DIR / markdown["file_name"]).unlink(missing_ok=True)
        raise
    if result["status"] != "edited":    # nothing changed: the PDF stays as it is
        (config.LIBRARY_DIR / markdown["file_name"]).unlink(missing_ok=True)
    else:
        result["conversion_reason"] = reason
    return result


def _edit(source: dict, doc: dict, op: str, find: str, text: str, replace_all: bool, loose: bool = False) -> dict:
    """The edit itself. `source` is the document as stored; `doc` is the file to edit (the same, or a Markdown
    copy of a PDF)."""
    doc_id, converted = doc["id"], doc is not source
    pattern = None
    if op == "rewrite":
        if doc["ext"] not in (".txt", ".md"):
            raise EditError("Only a text or Markdown file can be edited as a whole.")
        if len(text) > MAX_REWRITE_CHARS:
            raise EditError(f"That text is too long to save here (limit {MAX_REWRITE_CHARS:,} characters).")
        # decoded as strictly as file_text opened it: a file can't pass one check and fail the other
        if _without_line_ends(text) == _without_line_ends(_strict_text((config.LIBRARY_DIR / doc["file_name"]).read_bytes())):
            raise EditError("Nothing was changed.")
    elif op == "append":
        if not text.strip():
            raise EditError("Give the text to add.")
        if len(text) > MAX_APPEND_CHARS:
            raise EditError(f"That is too much text to add in one go (limit {MAX_APPEND_CHARS} characters).")
    else:
        if find.split() == text.split():
            raise EditError("The new text is the same as the old text, so nothing would change.")
        pattern = _pattern(find)
        found, contexts = _count_matches(doc, pattern)
        if not found:  # the user may not have the capitalisation right
            pattern = _pattern(find, ignore_case=True)
            found, contexts = _count_matches(doc, pattern)
        unbalanced = False
        for candidate in (_loose_patterns(find, source["ext"]) if loose and not found else []):
            matches = _matches(doc, candidate)
            if matches and not all(_marks_balanced(m) for m in matches):
                unbalanced = True   # it would take half of a bold / italic / code pair with it
                continue
            if matches:
                pattern, found, contexts = candidate, *_count_matches(doc, candidate)
                break
        if not found and unbalanced:
            raise EditError("That selection starts or ends inside bold, italic or code formatting. Select the whole "
                            "formatted part, or less of it.")
        if not found:
            if doc["ext"] == ".docx" and _pattern(find, ignore_case=True).search(document_text(doc).replace("\n", " ")):
                raise EditError("That text runs across more than one paragraph of this Word document, and a change "
                                "has to stay inside one. Replace it one paragraph at a time.")
            if doc["ext"] == ".pdf" and _pattern(find, ignore_case=True).search(document_text(doc)):
                raise pdf_edit.CantKeepLayout("The text couldn't be located in the PDF's page layout.")
            raise EditError("That text isn't in the document. Check the exact wording.")
        if found > 1 and not replace_all:
            return {"status": "ambiguous", "matches": found, "contexts": contexts,
                    "message": "The text appears in several places and nothing was changed. Ask the user whether to "
                               "change all of them, or which one (quote more of the surrounding text)."}

    path = config.LIBRARY_DIR / doc["file_name"]
    if not path.exists():
        raise EditError("The stored file is missing.")
    if doc["ext"] == ".pdf" and op == "append":
        raise pdf_edit.CantKeepLayout("Text added at the end of a PDF can't yet be laid out to match the rest of it.")
    before = _plain_text(path)
    backup = _backup(source)
    try:
        info: dict = {}
        changed, previews = _apply(doc, op, pattern, text, replace_all, info)
        reloaded = _reindex(doc)
    except pdf_edit.CantKeepLayout:
        backup.unlink(missing_ok=True)          # refused before the file was replaced: nothing to put back
        raise
    except Exception as e:
        if converted:
            backup.unlink(missing_ok=True)      # the PDF was never touched; the caller removes the Markdown copy
        else:
            shutil.copy2(backup, path)          # the index was not touched if embedding failed; the file goes back
            backup.unlink(missing_ok=True)
        log.exception("Editing %s failed", doc["title"])
        raise EditError(f"The edit failed and the document is unchanged: {str(e)[:200]}") from e

    if converted:   # from now on the document is the Markdown copy; the PDF lives on in versions/
        catalog.update_document(doc_id, ext=doc["ext"], file_name=doc["file_name"])
        (config.LIBRARY_DIR / source["file_name"]).unlink(missing_ok=True)
        reloaded = {**reloaded, "pages": source["pages"] or reloaded["pages"]}
    refreshed = _finish(doc, reloaded, before)
    written = info.get("written_text", text)
    detail = (f'replaced "{find}" with "{written}"' if op == "replace" else
              "edited the text in the editor" if op == "rewrite" else f'appended "{text[:80]}"')[:300]
    if converted:
        detail = ("converted the PDF to Markdown; " + detail)[:300]
    catalog.record_edit(doc_id, op, detail, backup.name)
    _drop_redo_history(doc_id)
    event("edit", doc=doc_id, title=doc["title"], op=op, changes=changed, chunks=reloaded["chunks"],
          embedded=reloaded["embedded"], detail=detail,
          converted=converted, summary_refreshed=refreshed)
    return {"status": "edited", "operation": op, "changes": changed, "chunks_reindexed": reloaded["chunks"],
            "chunks_embedded": reloaded["embedded"], "title": doc["title"], "previews": previews, "converted": converted,
            "layout": info.get("layout"),
            **({"plain_letters": info["plain_letters"]} if info.get("plain_letters") else {}),
            **({"curly_letters": info["curly_letters"]} if info.get("curly_letters") else {}),
            **({"stand_in": info["stand_in"]} if info.get("stand_in") else {}),
            **{k: info[k] for k in ("added_lines", "new_pages", "page_numbers_kept", "markdown_removed")
               if info.get(k)}}
