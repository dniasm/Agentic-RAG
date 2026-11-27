"""The Document service, RAG half: reading, indexing and finding passages over gRPC (protos/agentic_rag_proto/document.proto).

    python -m services.document        # listens on DOCUMENT_SERVICE_ADDR (default localhost:50064)

Needs GEMINI_API_KEY (environment or .env) to embed passages and questions, and VOYAGE_API_KEY to rerank; without
Voyage a search still works and says reranked=false. The passage collection is created (or its vector size checked)
on first use, in this service's own folder; document.db holds the OCR and question-embedding caches.
"""
import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

import grpc
from qdrant_client import models

from agentic_rag_proto import document_pb2 as pb
from agentic_rag_proto import document_pb2_grpc as pb_grpc
from backend.config import gemini, voyage
from services.common.server import Service, serve
from services.document import db, ingestion, ocr, rerank, search, sparse, vectorstore
from services.document.catalog_server import CatalogMixin

log = logging.getLogger(__name__)
NAME = "document"
GRPC_NAME = "agentic_rag.v1.DocumentService"
_DONE = object()   # ends an event queue


def _opt(msg, name: str):
    return getattr(msg, name) if msg.HasField(name) else None


def _passage(h: dict) -> pb.Passage:
    p = pb.Passage(id=h["id"], content=h["content"], ocr=bool(h.get("ocr")), matched_query=h.get("matched_query") or "",
                   score=h.get("score") or 0.0, rrf=h.get("rrf") or 0.0, selected=h.get("selected") or 0)
    for name in ("source", "page", "doc_id", "chunk_index", "dense_rank", "bm25_rank", "bm25", "relevance", "scorer"):
        if h.get(name) is not None:
            setattr(p, name, h[name])
    return p


def failure_of(e: Exception) -> pb.Failure:
    """The Failure event for an error while reading or indexing one file."""
    if isinstance(e, ingestion.BudgetExceeded):
        return pb.Failure(kind="budget", message=str(e), needed=e.needed)
    if isinstance(e, gemini.AllKeysLimited):
        return pb.Failure(kind="rate_limited", message=str(e)[:400], for_day=e.for_day)
    if isinstance(e, ingestion.IngestionError):
        text = str(e)
        kind = "no_text" if text.startswith("No extractable") else "unsupported" if text.startswith("Unsupported") else "error"
        return pb.Failure(kind=kind, message=text[:400])
    if isinstance(e, gemini.GeminiError):
        log.warning("Embedding failed: %s", str(e)[:300])
        return pb.Failure(kind="error", message=str(e)[:400])
    log.exception("Reading or indexing a file failed")
    return pb.Failure(kind="error", message=f"{type(e).__name__}: {e}"[:400])


class DocumentServicer(CatalogMixin, pb_grpc.DocumentServiceServicer):
    # ---------------------------------------------------------------- helpers
    async def _run(self, context, fn, *args, **kwargs):
        """Run a blocking call off the event loop; bad input becomes INVALID_ARGUMENT."""
        try:
            return await asyncio.to_thread(lambda: fn(*args, **kwargs))
        except ValueError as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e)[:200])

    async def _receive(self, request_iterator, context) -> tuple[object, Path]:
        """The first message (the file's description) and the rest written to a temporary file; returns (start, path)."""
        start, handle, path = None, None, None
        try:
            async for msg in request_iterator:
                kind = msg.WhichOneof("part")
                if kind == "start" and start is None:
                    start = msg.start
                    handle, name = tempfile.mkstemp(suffix=start.ext or "", prefix="document-")
                    path = Path(name)
                    handle = os.fdopen(handle, "wb")
                elif kind == "chunk" and start is not None:
                    handle.write(msg.chunk)
                else:
                    raise ValueError("the first message must describe the file, the rest must be its bytes")
            if start is None:
                raise ValueError("no file was sent")
        except ValueError as e:
            if path:
                path.unlink(missing_ok=True)
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        finally:
            if handle is not None:
                handle.close()
        return start, path

    async def _events(self, work, make_progress, make_failure):
        """Yield the events `work` (a blocking function taking a progress callback) reports, then its result."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def emit(event):
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def progress(stage: str, done: int, total: int) -> None:
            emit(make_progress(pb.IngestProgress(stage=stage, done=done, total=total)))

        def runner():
            try:
                emit(work(progress))
            except Exception as e:
                emit(make_failure(failure_of(e)))
            finally:
                emit(_DONE)

        task = asyncio.create_task(asyncio.to_thread(runner))
        try:
            while (event := await queue.get()) is not _DONE:
                yield event
        finally:
            await task

    # ---------------------------------------------------------------- reading and indexing
    async def Ingest(self, request_iterator, context):
        start, path = await self._receive(request_iterator, context)
        if not start.user_id.strip() or not start.doc_id.strip():
            path.unlink(missing_ok=True)
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id and doc_id must not be empty")

        def work(progress):
            result = ingestion.ingest_file(
                path, user_id=start.user_id, doc_id=start.doc_id, filename=start.filename or path.name,
                file_name=start.file_name or None, max_passages=_opt(start, "max_passages"), progress=progress)
            from backend.config import api_meter
            api_meter.flush()   # the embedding requests this file used count against the day right away
            return pb.IngestEvent(done=pb.IngestDone(**result))

        try:
            async for event in self._events(work, lambda p: pb.IngestEvent(progress=p),
                                            lambda f: pb.IngestEvent(failure=f)):
                yield event
        finally:
            path.unlink(missing_ok=True)

    async def ReadPages(self, request_iterator, context):
        start, path = await self._receive(request_iterator, context)

        def work(progress):
            if start.mode == pb.READ_SCANNED_CACHED:
                import pymupdf
                with pymupdf.open(str(path)) as pdf:
                    scanned = [n for n in range(1, len(pdf) + 1) if ocr.needs_ocr(pdf[n - 1])]
                    count = len(pdf)
                texts = ocr.read_pages(path, scanned, cached_only=True) if scanned else {}
                return pb.ReadEvent(result=pb.ReadResult(
                    pages=[pb.Page(page=n, text=t, ocr=True) for n, t in sorted(texts.items())], page_count=count))
            pages, count = ingestion.load_file(path, progress=lambda d, t: progress("ocr", d, t))
            return pb.ReadEvent(result=pb.ReadResult(pages=[
                pb.Page(text=d.page_content, ocr=bool(d.metadata.get("ocr")),
                        **({"page": d.metadata["page"]} if d.metadata.get("page") is not None else {}))
                for d in pages], page_count=count))

        try:
            async for event in self._events(work, lambda p: pb.ReadEvent(progress=p), lambda f: pb.ReadEvent(failure=f)):
                yield event
        finally:
            path.unlink(missing_ok=True)

    # ---------------------------------------------------------------- finding
    async def Search(self, request, context):
        found = await self._run(
            context, search.search, list(request.queries), request.user_id, k=_opt(request, "k"),
            min_score=_opt(request, "min_score"), doc_id=_opt(request, "doc_id"), mode=_opt(request, "mode"),
            rerank_query=_opt(request, "rerank_query"), mmr=request.mmr)
        reply = pb.SearchReply(passages=[_passage(h) for h in found["passages"]], reranked=found["reranked"],
                               zone=found["zone"])
        if found["best_relevance"] is not None:
            reply.best_relevance = found["best_relevance"]
        return reply

    async def Rerank(self, request, context):
        priority = voyage.FIND if request.priority == "find" else voyage.RERANK
        items = [{"content": i.content, "i": n} for n, i in enumerate(request.items)]
        scored = await self._run(context, rerank.score, request.query, items, priority)
        if scored is None:
            return pb.RerankReply(reranked=False)
        relevance = [0.0] * len(items)
        for item in scored:
            relevance[item["i"]] = item["relevance"]
        return pb.RerankReply(reranked=True, relevance=relevance, scorer=scored[0]["scorer"] if scored else "voyage")

    async def GetDenseVectors(self, request, context):
        vectors = await self._run(context, vectorstore.get_dense_vectors, list(request.ids))
        return pb.GetDenseVectorsReply(vectors=[pb.Vector(values=v or []) for v in vectors])

    # ---------------------------------------------------------------- the index
    async def DeleteDocumentVectors(self, request, context):
        if not request.user_id.strip() or not request.doc_id.strip():
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id and doc_id must not be empty")
        await self._run(context, vectorstore.delete_document_vectors, request.user_id, request.doc_id)
        return pb.DeleteReply()

    async def SetChunkFields(self, request, context):
        if not request.user_id.strip() or not request.doc_id.strip():
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id and doc_id must not be empty")
        await self._run(context, vectorstore.set_chunk_fields, request.user_id, request.doc_id, json.loads(request.fields_json or "{}"))
        return pb.DeleteReply()

    async def CountPassages(self, request, context):
        def count():
            with db.shared().tx() as c:
                pages = c.execute("SELECT COUNT(*) FROM page_text").fetchone()[0]
                queries = c.execute("SELECT COUNT(*) FROM query_embeddings").fetchone()[0]
            return vectorstore.count(_opt(request, "user_id"), _opt(request, "doc_id")), pages, queries
        passages, pages, queries = await self._run(context, count)
        return pb.CountPassagesReply(passages=passages, page_texts=pages, query_embeddings=queries)

    async def KeyStates(self, request, context):
        cooldowns, shared, rejected = gemini.pool.cooldowns(), gemini.pool.shared(), gemini.pool.rejected()
        keys = []
        for k in range(gemini.key_count()):
            state = pb.KeyState(index=k, out_for_day=gemini.pool.out_for_day(k), shares_quota=k in shared,
                                rejected=k in rejected)
            if cooldowns.get(k) is not None:
                state.cooldown_seconds = cooldowns[k]
            keys.append(state)
        return pb.KeyStatesReply(keys=keys)

    # ---------------------------------------------------------------- migration
    async def ImportPassages(self, request, context):
        def points():
            for p in request.passages:
                if len(p.dense) != vectorstore.settings.embedding_dim:
                    raise ValueError(f"passage {p.id}: needs a dense vector of {vectorstore.settings.embedding_dim} numbers")
                yield models.PointStruct(
                    id=p.id, payload=json.loads(p.payload_json),
                    vector={vectorstore.DENSE: list(p.dense),
                            vectorstore.SPARSE: models.SparseVector(indices=list(p.sparse.indices),
                                                                    values=list(p.sparse.values))})
        created = await self._run(context, lambda: vectorstore.import_points(list(points())))
        return pb.DocumentImportReply(created=created)

    async def ImportCaches(self, request, context):
        def copy():
            made = 0
            with db.shared().tx() as c:
                for table, column, rows in (("page_text", "text", request.page_texts),
                                            ("query_embeddings", "vector", request.query_embeddings)):
                    for r in rows:
                        made += c.execute(f"INSERT OR IGNORE INTO {table} (key, {column}) VALUES (?, ?)",
                                          (r.key, r.value)).rowcount
            return made
        return pb.DocumentImportReply(created=await self._run(context, copy))


def build_service() -> Service:
    """The service ready to bind and start; the passage collection is created (or its size checked) here, at start."""
    vectorstore.get_client()
    service = Service(NAME)
    service.register(pb_grpc.add_DocumentServiceServicer_to_server, DocumentServicer(), GRPC_NAME)
    service.add_closer(vectorstore.close_client)
    service.add_closer(db.close_shared)
    return service


def main() -> None:
    voyage.use_document_share()   # this process may use only the document part of the account's Voyage budget
    serve(NAME, build_service)
