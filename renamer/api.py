"""HTTP API and the chat page.  Run:  python -m renamer serve   (http://127.0.0.1:8200)"""
import threading
from concurrent.futures import ThreadPoolExecutor

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from renamer import agent, batch, catalog, config, library, logs
from renamer.logs import event

logs.setup()   # data/logs/events.log (major events) and data/logs/all.log (everything)


@asynccontextmanager
async def lifespan(_app):
    event("server_started", documents=len(catalog.list_documents()))
    batch.start()      # the queue worker
    yield
    batch.stop()
    event("server_stopped")


app = FastAPI(title="Document Renaming Agent", lifespan=lifespan)
# One file at a time: Gemini's free tier allows 100 embedding requests a minute.
_ingest_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")
# One chat message at a time per session, so two quick messages can't race on the same session.
_session_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _public(doc: dict) -> dict:
    return {k: doc[k] for k in ("id", "title", "original_name", "file_name", "ext", "status", "error", "pages",
                                "chunks", "summary", "keywords", "size_bytes", "created_at", "updated_at")}


def _resume_unfinished() -> None:
    """Files left 'processing' when the server stopped are indexed again."""
    for doc in catalog.list_documents(status="processing"):
        _ingest_pool.submit(library.process, doc["id"])


_resume_unfinished()


class ChatIn(BaseModel):
    session_id: str = Field(min_length=6, max_length=64)
    message: str = Field(min_length=1, max_length=4000)


@app.get("/api/health")
def health():
    return {"ok": True, "day7": str(config.DAY7_DIR), "documents": len(catalog.list_documents())}


@app.get("/api/documents")
def list_documents():
    docs = catalog.list_documents()
    return [{**_public(d), "history": catalog.rename_history(d["id"])} for d in docs]


@app.post("/api/documents/upload")
def upload(files: list[UploadFile] = File(...)):
    results = []
    for f in files:
        data = f.file.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
        f.file.close()
        try:
            doc, duplicate = library.add_file(f.filename or "", data)
        except library.LibraryError as e:
            event("upload_rejected", file=f.filename, why=str(e))
            results.append({"file": f.filename, "status": "rejected", "error": str(e)})
            continue
        if not duplicate:
            _ingest_pool.submit(library.process, doc["id"])
        results.append({"file": f.filename, "status": "duplicate" if duplicate else "queued", "document": _public(doc)})
    return results


@app.delete("/api/documents/{doc_id}", status_code=204)
def delete_document(doc_id: str):
    doc = catalog.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc["status"] == "processing":
        raise HTTPException(409, "The document is still being indexed")
    library.delete(doc_id)


@app.get("/api/documents/{doc_id}/file")
def download(doc_id: str):
    doc = catalog.get_document(doc_id)
    path = config.LIBRARY_DIR / doc["file_name"] if doc else None
    if not path or not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=doc["file_name"])


@app.post("/api/chat")
def chat(body: ChatIn):
    with _locks_guard:
        lock = _session_locks.setdefault(body.session_id, threading.Lock())
    with lock:
        result = agent.chat(body.session_id, body.message.strip())
    return {**result, "documents": list_documents()}


@app.get("/api/limits")
def limits_now():
    """Groq tokens used in the last minute, and what the queue is doing: polled by the page every second."""
    return batch.status_light()


@app.get("/api/queue")
def queue():
    return batch.status()


@app.delete("/api/batches/{batch_id}")
def cancel_batch(batch_id: str):
    if not catalog.get_batch(batch_id):
        raise HTTPException(404, "Batch not found")
    n = catalog.cancel_batch(batch_id)
    event("batch_cancelled", batch=batch_id, tasks=n)
    return {"cancelled": n}


@app.get("/api/sessions/{session_id}")
def session(session_id: str):
    return {"messages": catalog.load_session(session_id)["display"]}


app.mount("/", StaticFiles(directory=config.PROJECT_ROOT / "web", html=True), name="web")
