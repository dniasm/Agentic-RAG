"""The orchestrator's side of the Document service's RAG half: send a file to be indexed, find passages, delete them.

Everything here is a thin wrapper that turns gRPC messages into the dicts and exceptions the library and the agent
already use. The orchestrator opens no vector store and embeds nothing itself; it also keeps the embedding allowance:
`ingest()` checks it BEFORE calling the service (the service refuses a file that needs more than what is left, before
embedding anything) and charges the passages the service says it really embedded, AFTER.
"""
import hashlib
import json
import logging
from pathlib import Path

from agentic_rag_proto import document_pb2 as pb
from backend.clients import get_clients
from backend.config import gemini
from backend.config.settings import settings

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
CHUNK_BYTES = 1024 * 1024   # file bytes per message (gRPC's limit is 4 MB)


class IngestionError(Exception):
    """A file that could not be read or indexed (no text, password-protected, ...); the message says why."""


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def available() -> bool:
    """A reranker can be tried for an answer (the service decides; this is the same switch and key it reads)."""
    return settings.rerank_enabled and bool(settings.voyage_api_key)


def _call(rpc_name: str, request):
    clients = get_clients()
    return clients.call(getattr(clients.document, rpc_name), request)


def _file_messages(message_type, start, path: Path):
    yield message_type(start=start)
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_BYTES):
            yield message_type(chunk=chunk)


def _raise(failure: pb.Failure, user_id: str | None = None, safety: bool = False):
    """The exception for a Failure event."""
    if failure.kind == "budget" and user_id is not None:
        from backend.library import embed_budget
        embed_budget.check(user_id, failure.needed, safety=safety)   # raises with the allowance's own message
        raise embed_budget.EmbedBudgetExceeded(failure.message)
    if failure.kind == "rate_limited":
        raise gemini.AllKeysLimited(failure.message, for_day=failure.for_day, wait=0.0)
    raise IngestionError(failure.message)


# ---------------------------------------------------------------- reading and indexing
def ingest(path: Path, *, user_id: str, doc_id: str, filename: str, file_name: str | None = None,
           safety: bool = False, on_progress=None) -> dict:
    """Index the file at `path` for a user. Returns {"pages", "chunks", "embedded"}.

    on_progress(stage, done, total) is called for each progress event the service streams (reading, ocr, chunked,
    embedding, stored). Raises embed_budget.EmbedBudgetExceeded when the allowance can't take the file (nothing was
    embedded), IngestionError for a file with no text, and gemini.AllKeysLimited when every Gemini key is limited."""
    from backend.library import embed_budget
    start = pb.IngestStart(user_id=user_id, doc_id=doc_id, filename=filename, ext=path.suffix.lower())
    if file_name:
        start.file_name = file_name
    left = embed_budget.allowance(user_id, safety=safety)
    if left is not None:
        start.max_passages = left
    clients = get_clients()
    stream = clients.call_stream(clients.document.Ingest, _file_messages(pb.IngestRequest, start, path),
                                 timeout=settings.document_stream_timeout_seconds)
    for event in stream:
        kind = event.WhichOneof("event")
        if kind == "progress":
            if on_progress:
                on_progress(event.progress.stage, event.progress.done, event.progress.total)
        elif kind == "failure":
            _raise(event.failure, user_id, safety)
        elif kind == "done":
            embed_budget.charge(user_id, event.done.embedded)
            return {"pages": event.done.pages, "chunks": event.done.chunks, "embedded": event.done.embedded}
    raise IngestionError("The Document service ended the stream without a result.")


def read_pages(path: Path, scanned_cached_only: bool = False, on_progress=None) -> tuple[list[dict], int]:
    """The text of a file as indexing reads it: ([{"page", "text", "ocr"}, ...], page count). A PDF's scanned pages are
    read by OCR (on_progress(stage, done, total) follows it). With scanned_cached_only, only the scanned pages whose OCR
    text is already cached, and OCR never starts."""
    start = pb.ReadStart(ext=path.suffix.lower(), mode=pb.READ_SCANNED_CACHED if scanned_cached_only else pb.READ_ALL)
    clients = get_clients()
    stream = clients.call_stream(clients.document.ReadPages, _file_messages(pb.ReadRequest, start, path),
                                 timeout=settings.document_stream_timeout_seconds)
    for event in stream:
        kind = event.WhichOneof("event")
        if kind == "progress":
            if on_progress:
                on_progress(event.progress.stage, event.progress.done, event.progress.total)
        elif kind == "failure":
            _raise(event.failure)
        elif kind == "result":
            pages = [{"page": p.page if p.HasField("page") else None, "text": p.text, "ocr": p.ocr}
                     for p in event.result.pages]
            return pages, event.result.page_count
    raise IngestionError("The Document service ended the stream without a result.")


def page_text(path: Path, on_progress=None) -> str:
    """All of a file's text, pages joined by blank lines (what indexing sees)."""
    pages, _ = read_pages(path, on_progress=on_progress)
    return "\n\n".join(p["text"] for p in pages)


# ---------------------------------------------------------------- finding
def _hit(p: pb.Passage) -> dict:
    def opt(name):
        return getattr(p, name) if p.HasField(name) else None
    hit = {"id": p.id, "content": p.content, "source": opt("source"), "page": opt("page"), "ocr": p.ocr,
           "doc_id": opt("doc_id"), "chunk_index": opt("chunk_index"), "matched_query": p.matched_query,
           "score": p.score, "rrf": p.rrf, "dense_rank": opt("dense_rank"), "bm25_rank": opt("bm25_rank"),
           "bm25": opt("bm25")}
    if p.HasField("relevance"):
        hit["relevance"], hit["scorer"] = p.relevance, opt("scorer")
    if p.selected:
        hit["selected"] = p.selected
    return hit


def search(queries: list[str], user_id: str, *, k: int | None = None, min_score: float | None = None,
           doc_id: str | None = None, rerank_query: str | None = None, mmr: bool = False) -> dict:
    """{"passages": [hit dicts], "reranked": bool, "zone": "high" | "mid" | "low" | "", "best_relevance": float | None}.
    With rerank_query the passages come back best first by the reranker, those MMR chose marked "selected" (1 = the first MMR pick);
    reranked=False means the caller must judge the passages' relevance itself."""
    request = pb.SearchRequest(queries=queries, user_id=user_id, mmr=mmr)
    if k is not None:
        request.k = k
    if min_score is not None:
        request.min_score = min_score
    if doc_id:
        request.doc_id = doc_id
    if rerank_query:
        request.rerank_query = rerank_query
    reply = _call("Search", request)
    return {"passages": [_hit(p) for p in reply.passages], "reranked": reply.reranked, "zone": reply.zone,
            "best_relevance": reply.best_relevance if reply.HasField("best_relevance") else None}


def rerank_texts(query: str, texts: list[str], priority: str = "rerank") -> list[float] | None:
    """The reranker's relevance of each text to the query, in order; None when it could not score."""
    reply = _call("Rerank", pb.RerankRequest(query=query, items=[pb.RerankItem(content=t) for t in texts],
                                             priority=priority))
    return list(reply.relevance) if reply.reranked else None


def dense_vectors(ids: list[str]) -> list[list[float] | None]:
    reply = _call("GetDenseVectors", pb.GetDenseVectorsRequest(ids=ids))
    return [list(v.values) or None for v in reply.vectors]


# ---------------------------------------------------------------- the index
def delete_document_vectors(user_id: str, doc_id: str) -> None:
    _call("DeleteDocumentVectors", pb.DocumentRef(user_id=user_id, doc_id=doc_id))


def set_chunk_fields(owner: str, doc_id: str, fields: dict) -> None:
    """Set metadata fields (title, file_name) on every passage of a document, without re-embedding."""
    _call("SetChunkFields", pb.SetChunkFieldsRequest(user_id=owner, doc_id=doc_id, fields_json=json.dumps(fields)))


def count_passages(user_id: str | None = None, doc_id: str | None = None) -> int:
    request = pb.CountPassagesRequest()
    if user_id:
        request.user_id = user_id
    if doc_id:
        request.doc_id = doc_id
    return _call("CountPassages", request).passages


def key_states() -> list[dict]:
    """The Gemini key pool as the service sees it (admin page)."""
    reply = _call("KeyStates", pb.KeyStatesRequest())
    return [{"index": k.index, "out_for_day": k.out_for_day, "rejected": k.rejected, "shares_quota": k.shares_quota,
             "cooldown_seconds": k.cooldown_seconds if k.HasField("cooldown_seconds") else None} for k in reply.keys]
