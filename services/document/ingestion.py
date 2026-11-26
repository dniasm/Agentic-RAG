"""Ingestion pipeline: load -> clean -> chunk -> embed -> upsert into Qdrant."""
import hashlib
import logging
import re
import time
import uuid
from pathlib import Path

from langchain_core.documents import Document
from qdrant_client import models

from backend.config import gemini
from backend.config.settings import settings
from services.document import sparse, vectorstore

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


class IngestionError(Exception):
    pass


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"-\n(\w)", r"\1", text)        # re-join hyphenated line breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_file(path: Path, progress=None) -> tuple[list[Document], int]:
    """Return one Document per page (PDF) or per file (others), plus the page count. Only a PDF's passages carry a
    page: a Word or text file has no fixed pages, and "page 1" on every citation from it was wrong.
    progress(done, total) is called before each scanned page is read by OCR."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as e:
                raise IngestionError("PDF is password-protected") from e
        docs, empty = [], []
        for i, page in enumerate(reader.pages, start=1):
            text = _clean(page.extract_text() or "")
            if text:
                docs.append(Document(page_content=text, metadata={"page": i}))
            else:
                empty.append(i)
        if empty:   # scanned pages: their text read by OCR, for the index only (the file isn't changed)
            from services.document import ocr
            for i, text in ocr.read_pages(path, empty, progress=progress).items():
                if text := _clean(text):
                    docs.append(Document(page_content=text, metadata={"page": i, "ocr": True}))
            docs.sort(key=lambda d: d.metadata["page"])
        return docs, len(reader.pages)

    if ext == ".docx":
        import docx

        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for table in d.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        text = _clean("\n".join(parts))
        return ([Document(page_content=text, metadata={})] if text else []), 1

    if ext in {".txt", ".md"}:
        text = _clean(path.read_text(encoding="utf-8", errors="ignore"))
        return ([Document(page_content=text, metadata={})] if text else []), 1

    raise IngestionError(f"Unsupported file type: {ext}")


def split_documents(docs: list[Document]) -> list[Document]:
    # Imported here, not at module level: the langchain_text_splitters package eagerly imports
    # sentence-transformers / transformers / torch when they're installed (no longer required, but an older
    # environment may still have them), which added 12-32 s to every backend startup.
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
        add_start_index=True,
    )
    return [c for c in splitter.split_documents(docs) if len(c.page_content.strip()) > 20]


# Gemini's free tier counts EVERY text in a batch as one request, limited to 100 per minute. Sending a
# whole document at once therefore fails for any document over ~100 chunks, however long you wait.
EMBED_BATCH = 90
MAX_WAIT = 90.0   # longest wait for a key to come back; edits embed under the library lock, so never an hour


def embed_paced(texts: list[str], title: str) -> list[list[float]]:
    """Embed in batches under the per-minute limit. A refused batch goes whole to the next Gemini key
    (config/gemini.py); when every key is limited, wait until the first is back (up to 6 tries), unless every key
    is out for the day or the first back is more than MAX_WAIT away."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start:start + EMBED_BATCH]

        def send(emb, batch=batch):
            return emb.embed_documents(batch, batch_size=EMBED_BATCH, task_type="RETRIEVAL_DOCUMENT",
                                       titles=[title] * len(batch))

        for attempt in range(6):
            try:
                vectors += gemini.with_keys(send, "documents", calls=len(batch))   # each text is one request
                break
            except gemini.AllKeysLimited as e:
                if e.for_day or e.wait > MAX_WAIT or attempt == 5:
                    raise
                wait = e.wait + 2
                log.info("Embedding rate limit hit on every key (chunk %d of %d); waiting %.0fs",
                         start, len(texts), wait)
                time.sleep(wait)
    return vectors


class BudgetExceeded(IngestionError):
    """More passages need embedding than the caller allowed (max_passages); nothing was embedded or written."""

    def __init__(self, needed: int, allowed: int):
        super().__init__(f"{needed} passages need embedding but {allowed} are allowed")
        self.needed, self.allowed = needed, allowed


def _noop(*_args, **_kwargs) -> None:
    return None


def ingest_file(path: Path, *, user_id: str, doc_id: str, filename: str, file_name: str | None = None,
                max_passages: int | None = None, progress=None) -> dict:
    """Index a file for a user: load, clean, chunk, embed, store. Returns {"pages", "chunks", "embedded"}.

    A chunk whose text the document already has in the index keeps its stored vector (an edit changes a few chunks,
    and every embedded chunk is one Gemini request), so re-indexing a document embeds only what is new. The new
    chunks are embedded before anything is written, so a failed embedding leaves the index as it was. The caller's
    allowance arrives as max_passages (None: no limit): more new chunks than that is BudgetExceeded, before anything is
    embedded. progress(stage, done, total) reports reading / ocr / chunked / embedding / stored.
    """
    progress = progress or _noop
    progress("reading", 0, 0)
    pages, page_count = load_file(path, progress=lambda done, total: progress("ocr", done, total))
    if not pages:
        raise IngestionError("No extractable text found (the file may be scanned images).")

    chunks = split_documents(pages)
    progress("chunked", len(chunks), len(chunks))
    vectors = vectorstore.stored_vectors(user_id, doc_id)
    new_texts = list(dict.fromkeys(c.page_content for c in chunks if c.page_content not in vectors))
    if max_passages is not None and len(new_texts) > max_passages:   # refused whole, before anything is embedded
        raise BudgetExceeded(len(new_texts), max_passages)
    for start in range(0, len(new_texts), EMBED_BATCH):
        batch = new_texts[start:start + EMBED_BATCH]
        vectors.update(zip(batch, embed_paced(batch, filename)))
        progress("embedding", start + len(batch), len(new_texts))

    points = []
    for idx, chunk in enumerate(chunks):
        metadata = {
            "user_id": user_id,
            "doc_id": doc_id,
            "source": filename,
            **({"title": filename, "file_name": file_name} if file_name else {}),
            "page": chunk.metadata.get("page"),
            **({"ocr": True} if chunk.metadata.get("ocr") else {}),
            "chunk_index": idx,
            "start_index": chunk.metadata.get("start_index"),
        }
        points.append(models.PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:{idx}")),
            vector={vectorstore.DENSE: vectors[chunk.page_content],
                    vectorstore.SPARSE: sparse.document_vector(chunk.page_content)},
            payload={"page_content": chunk.page_content, "metadata": metadata},
        ))

    vectorstore.upsert(points)
    vectorstore.delete_chunks_from(user_id, doc_id, len(points))   # chunks of an older, longer version
    progress("stored", len(points), len(points))
    return {"pages": page_count, "chunks": len(points), "embedded": len(new_texts)}
