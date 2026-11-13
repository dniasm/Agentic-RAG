"""The document library: ingest through day7's pipeline, title, find by name or content, rename, delete.

Renaming a document changes it everywhere at once: its title in the catalog, the stored file's name in
data/library, and the "source"/"title" fields on its chunks in the vector store.
"""
import functools
import logging
import os
import copy
import shutil
import threading
import time
from pathlib import Path

from qdrant_client import models

from renamer import catalog, config, limits, naming, titles
from renamer.logs import event
from renamer.day7 import ingestion, rerank, retrieval, settings as day7_settings, vectorstore

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = ingestion.SUPPORTED_EXTENSIONS


class LibraryError(Exception):
    pass


# One change to stored files at a time: the chat and the queue worker run in different threads, and a rename that
# moves a file while an edit writes it (or two edits of one document) would leave the catalog and disk disagreeing.
WRITE_LOCK = threading.RLock()


def _exclusive(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with WRITE_LOCK:
            return fn(*args, **kwargs)
    return wrapper


def _path(doc: dict) -> Path:
    return config.LIBRARY_DIR / doc["file_name"]


def _set_chunk_fields(doc_id: str, **fields) -> None:
    """Update metadata fields on every chunk of a document (no re-embedding)."""
    client = vectorstore.get_client()
    with vectorstore._lock:
        client.set_payload(
            day7_settings.collection_name, payload=fields, key="metadata", wait=True,
            points=models.FilterSelector(filter=vectorstore.user_filter(config.OWNER, doc_id)),
        )


def taken_file_names(exclude_doc: dict | None = None) -> set[str]:
    """File names a new or renamed file must not use (lower case): every document's in the catalog, and every file
    actually in the library folder (a leftover or half-finished file is never overwritten), except the excluded
    document's own. Call it under WRITE_LOCK, with the move that follows, so two files can't pick the same name."""
    own = (exclude_doc or {}).get("file_name", "").lower()
    on_disk = {p.name.lower() for p in config.LIBRARY_DIR.iterdir()} if config.LIBRARY_DIR.exists() else set()
    return (catalog.used_file_names(exclude_doc=(exclude_doc or {}).get("id")) | on_disk) - {own}


def _move_file(doc: dict, title: str) -> str:
    """Rename the stored file after `title`; returns the new file name. Call under WRITE_LOCK."""
    new_name = naming.file_name_for(title, doc["ext"], taken_file_names(exclude_doc=doc))
    if new_name != doc["file_name"]:
        old = _path(doc)
        if old.exists():
            os.replace(old, config.LIBRARY_DIR / new_name)
    return new_name


def _unique_title(title: str, exclude: str | None = None) -> str:
    """Ingest only: never give a new document a title another document already has."""
    taken = {naming.normalize(d["title"]) for d in catalog.list_documents() if d["id"] != exclude}
    candidate, n = title, 2
    while naming.normalize(candidate) in taken:
        candidate, n = f"{title} ({n})", n + 1
    return candidate


# ---------------------------------------------------------------- ingest
def add_file(original_name: str, data: bytes) -> tuple[dict, bool]:
    """Store an uploaded file. Returns (document, is_duplicate). Call process() next to index it."""
    original_name = Path(original_name or "").name or "document"
    ext = Path(original_name).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise LibraryError(f"Unsupported file type. Allowed: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    if not data:
        raise LibraryError("The file is empty.")
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise LibraryError(f"The file is larger than {config.MAX_UPLOAD_MB} MB.")

    digest = ingestion.file_sha256(data)
    existing = catalog.find_by_hash(digest)
    if existing and existing["status"] != "failed":   # the same file, or the one it was before it was edited
        event("upload_duplicate", file=original_name, same_as=existing["title"], doc=existing["id"],
              edited_since=existing["sha256"] != digest)
        return existing, True
    if existing:  # an earlier attempt failed: start again
        delete(existing["id"])

    config.LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    provisional = titles.fallback_profile(original_name, "").title
    with WRITE_LOCK:
        file_name = naming.file_name_for(provisional, ext, taken_file_names())
        (config.LIBRARY_DIR / file_name).write_bytes(data)
        doc = catalog.add_document(title=provisional, original_name=original_name, file_name=file_name, ext=ext,
                                   sha256=digest, size_bytes=len(data))
    event("upload", doc=doc["id"], file=original_name, bytes=len(data))
    return doc, False


def process(doc_id: str, ask_title=None) -> dict:
    """Title the document, then index it with day7's ingestion pipeline (load, clean, chunk, embed, store)."""
    doc = catalog.get_document(doc_id)
    try:
        pages, _ = ingestion.load_file(_path(doc))
        if not pages:
            raise ingestion.IngestionError("No extractable text found (the file may be scanned images).")
        opening = "\n\n".join(p.page_content for p in pages)[: config.TITLE_SOURCE_CHARS]
        others = [d["title"] for d in catalog.list_documents() if d["id"] != doc_id and d["status"] != "failed"]
        profile, _ = titles.profile_document(doc["original_name"], opening, others, ask=ask_title)
        with WRITE_LOCK:   # choosing the title and the file name, and taking them, is one step for renames too
            title = _unique_title(profile.title, exclude=doc_id)
            file_name = _move_file(catalog.get_document(doc_id), title)
            catalog.update_document(doc_id, title=title, file_name=file_name, summary=profile.summary,
                                    keywords=profile.keywords)
            catalog.record_rename(doc_id, doc["original_name"], title, "titled")

        result = ingestion.ingest_file(config.LIBRARY_DIR / file_name, user_id=config.OWNER, doc_id=doc_id,
                                       filename=title)
        _set_chunk_fields(doc_id, title=title, file_name=file_name)
        catalog.update_document(doc_id, status="indexed", pages=result["pages"], chunks=result["chunks"], error=None)
        event("indexed", doc=doc_id, title=title, chunks=result["chunks"], pages=result["pages"])
    except Exception as e:
        log.exception("Ingesting %s failed", doc["original_name"])
        try:
            vectorstore.delete_document_vectors(config.OWNER, doc_id)
        except Exception:
            pass
        catalog.update_document(doc_id, status="failed", error=str(e)[:300])
        event("index_failed", doc=doc_id, file=doc["original_name"], error=str(e)[:200])
    return catalog.get_document(doc_id)


def ingest_path(path: Path, ask_title=None) -> tuple[dict, bool]:
    doc, duplicate = add_file(path.name, path.read_bytes())
    return (doc, True) if duplicate else (process(doc["id"], ask_title=ask_title), False)


@_exclusive
def delete(doc_id: str) -> None:
    doc = catalog.get_document(doc_id)
    if not doc:
        raise LibraryError("Document not found.")
    vectorstore.delete_document_vectors(config.OWNER, doc_id)
    _path(doc).unlink(missing_ok=True)
    shutil.rmtree(config.DATA_DIR / "versions" / doc_id, ignore_errors=True)
    catalog.delete_document(doc_id)
    event("deleted", doc=doc_id, title=doc["title"])


# ---------------------------------------------------------------- rename
def name_conflicts(new_title: str, exclude: str) -> list[dict]:
    return [d for d in catalog.list_documents() if d["id"] != exclude and naming.same_name(d["title"], new_title)]


@_exclusive
def rename(doc_id: str, new_title: str) -> dict:
    """Rename now (the agent's guard decides beforehand whether it may). Returns {old_title, new_title, file_name}."""
    doc = catalog.get_document(doc_id)
    if not doc:
        raise LibraryError("Document not found.")
    title = naming.clean_title(new_title)
    file_name = _move_file(doc, title)
    try:
        if doc["status"] == "indexed":
            _set_chunk_fields(doc_id, source=title, title=title, file_name=file_name)
    except Exception:
        if file_name != doc["file_name"]:  # put the file back so the catalog and disk still agree
            os.replace(config.LIBRARY_DIR / file_name, _path(doc))
        raise
    catalog.update_document(doc_id, title=title, file_name=file_name)
    catalog.record_rename(doc_id, doc["title"], title, "user")
    event("rename", doc=doc_id, old=doc["title"], new=title, file=file_name)
    return {"old_title": doc["title"], "new_title": title, "file_name": file_name}


# ---------------------------------------------------------------- find
def profile_text(doc: dict) -> str:
    parts = [f"Title: {doc['title']}", f"File: {doc['original_name']}"]
    if doc["summary"]:
        parts.append(f"Summary: {doc['summary']}")
    if doc["keywords"]:
        parts.append("Keywords: " + ", ".join(doc["keywords"]))
    return "\n".join(parts)


def _name_score(description: str, doc: dict, history: list[dict]) -> float:
    names = [(doc["title"], 1.0), (doc["original_name"], 1.0)]
    names += [(h["old_title"], 0.97) for h in history]  # names it used to have
    return max(naming.name_similarity(description, n) * w for n, w in names)


def _content_scores(description: str, docs: list[dict]) -> tuple[dict[str, float], dict[str, str], str]:
    """Best relevance per document from its chunks and its profile. Returns (scores, evidence, scorer)."""
    try:
        hits = retrieval.retrieve([description], config.OWNER, k=12, min_score=0.0)
    except Exception as e:
        log.warning("Content search failed: %s", str(e)[:200])
        hits = []
    known = {d["id"] for d in docs}
    hits = [h for h in hits if h.get("doc_id") in known]
    items = hits + [{"content": profile_text(d), "doc_id": d["id"], "profile": True} for d in docs]

    scored = None
    if _VOYAGE.used() < config.VOYAGE_CALLS_PER_MINUTE:
        _VOYAGE.record(1)
        scored = rerank.score(description, items)
    else:
        log.info("Voyage's minute is used up (%d calls): cosine scores for %r", _VOYAGE.used(), description[:80])
    scores: dict[str, float] = {}
    evidence: dict[str, str] = {}
    if scored is not None:
        scorer = scored[0]["scorer"] if scored else "voyage"
        for item in scored:  # best first
            doc_id = item["doc_id"]
            if doc_id not in scores:
                scores[doc_id] = item["relevance"]
            if doc_id not in evidence and not item.get("profile"):
                evidence[doc_id] = item["content"]
        return scores, evidence, scorer

    for h in sorted(hits, key=lambda h: h["score"], reverse=True):  # no reranker: cosine similarity of chunks
        scores.setdefault(h["doc_id"], h["score"])
        evidence.setdefault(h["doc_id"], h["content"])
    return scores, evidence, "cosine"


def _thresholds(scorer: str) -> tuple[float, float, float]:
    if scorer == "cosine":
        return config.COSINE_HIGH, config.COSINE_LOW, config.COSINE_MARGIN
    if scorer == "voyage":
        return config.MATCH_HIGH, config.MATCH_LOW, config.MATCH_MARGIN
    high, low = rerank.thresholds(scorer)
    return high, low, config.MATCH_MARGIN


def snippet(text: str, limit: int = 180) -> str:
    """One line of at most `limit` characters, cut at a word."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


_VOYAGE = limits.TokenWindow(config.VOYAGE_CALLS_PER_MINUTE, 60.0)   # rerank calls in the last minute (1 per call)
_FIND_CACHE: dict = {}   # (description, catalog version) -> (time, result)


def find(description: str) -> dict:
    """find_documents, reused while nothing in the library has changed: the agent often looks the same document up
    again in a follow-up message, and each search costs a Gemini embedding and a Voyage rerank call."""
    key = (" ".join(description.lower().split()), catalog.version())
    hit = _FIND_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < config.FIND_CACHE_SECONDS:
        return copy.deepcopy(hit[1])
    result = _find(description)
    if len(_FIND_CACHE) > 200:
        _FIND_CACHE.clear()
    _FIND_CACHE[key] = (time.monotonic(), result)
    return copy.deepcopy(result)


def _find(description: str) -> dict:
    """Which document does `description` mean? It can be a name, part of one, or what the document is about.

    decision:
        unique     one document clearly matches
        ambiguous  several match about equally (same or similar names, or similar content)
        uncertain  only a weak match: confirm with the user first
        none       nothing matches
    """
    docs = catalog.list_documents(status="indexed")
    if not docs:
        return {"decision": "none", "reason": "empty_library", "candidates": []}

    histories = catalog.rename_histories()
    names = {d["id"]: _name_score(description, d, histories.get(d["id"], [])) for d in docs}
    best_name = max(names.values())
    named = [d for d in docs if names[d["id"]] >= config.NAME_EXACT and names[d["id"]] >= best_name - 0.05]

    content, evidence, scorer = ({}, {}, "name") if len(named) == 1 else _content_scores(description, docs)
    high, low, margin = _thresholds(scorer if scorer != "name" else "voyage")

    def combined(doc_id: str) -> float:
        name = names[doc_id]
        from_name = low + (name - 0.6) * (high - low) / 0.3 if name >= 0.6 else 0.0  # 0.6 -> low, 0.9 -> high
        return round(max(content.get(doc_id, 0.0), from_name), 4)

    if len(named) == 1:
        decision, reason, ranked = "unique", "name", [(named[0], 1.0)]
    elif len(named) > 1:
        decision, reason = "ambiguous", "same_name"
        ranked = [(d, combined(d["id"])) for d in named]
    else:
        ranked = sorted(((d, combined(d["id"])) for d in docs), key=lambda x: x[1], reverse=True)
        top = ranked[0][1]
        close = [r for r in ranked if r[1] >= low and top - r[1] <= margin]
        if top < low:
            decision, reason = "none", "no_match"
        elif len(close) > 1:
            decision, reason, ranked = "ambiguous", "similar_content", close + [r for r in ranked if r not in close]
        elif top < high:
            decision, reason = "uncertain", "weak_match"
        else:
            decision, reason = "unique", "content"

    if decision == "unique":
        keep = ranked[:1]
    elif decision == "none":
        keep = ranked[:3]  # the nearest documents, so the agent can offer them
    else:
        keep = [r for r in ranked if r[1] >= low] or ranked[:3]
    return {
        "decision": decision,
        "reason": reason,
        "scorer": scorer,
        "candidates": [{
            "doc_id": d["id"], "title": d["title"], "original_name": d["original_name"],
            "summary": snippet(d["summary"], 220), "score": score, "name_match": names[d["id"]],
            **({"evidence": snippet(evidence[d["id"]])} if d["id"] in evidence else {}),
        } for d, score in keep[:5]],
    }
