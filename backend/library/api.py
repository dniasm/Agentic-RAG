"""The document library's HTTP API, mounted by backend/app.py at /api/library (until stages 4-6 unify the routes).

Every endpoint needs a login (the app's cookie: backend/api/routes/auth.py), and works only
in that user's library: another user's document, chat or batch answers as if it did not exist.
"""
import logging
import threading
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from backend.api.dependencies import get_current_user
from backend.library import agent, batch, catalog, config, export, library, logs
from backend.library.logs import event

router = APIRouter(prefix="/library", tags=["Library"])


# One chat message at a time per session, so two quick messages can't race on the same session.
_session_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _public(doc: dict) -> dict:
    out = {k: doc[k] for k in ("id", "title", "original_name", "ext", "status", "error", "pages",
                               "chunks", "summary", "keywords", "size_bytes", "created_at", "updated_at")}
    out["file_name"] = Path(doc["file_name"]).name   # the stored name, without the user's folder
    return out


def _resume_unfinished() -> None:
    """Files left 'processing' when the server stopped are indexed again."""
    for doc in catalog.list_documents(status="processing"):
        library.queue(doc["id"])


def startup() -> None:
    """Called from day7's lifespan: logs, unfinished files, the queue worker."""
    logs.setup()   # data/logs/events.log (major events) and data/logs/all.log (everything)
    _resume_unfinished()
    batch.start()
    event("server_started", documents=len(catalog.list_documents()), users=catalog.count_users())


def shutdown() -> None:
    library.stop_queue()
    batch.stop()
    event("server_stopped")


# ---------------------------------------------------------------- who is asking
current_user = get_current_user   # the one login (cookie), from backend/api/dependencies.py


def _own_doc(doc_id: str, user: dict) -> dict:
    doc = catalog.get_document(doc_id)
    if not doc or doc["owner"] != user["id"]:
        raise HTTPException(404, "Document not found")
    return doc


class ChatIn(BaseModel):
    session_id: str = Field(min_length=6, max_length=64)
    message: str = Field(min_length=1, max_length=4000)


# ---------------------------------------------------------------- the user's library
@router.get("/documents")
def list_documents(user: dict = Depends(current_user)):
    docs = catalog.list_documents(owner=user["id"])
    histories = catalog.rename_histories()
    return [{**_public(d), "history": histories.get(d["id"], [])} for d in docs]


@router.post("/documents/upload")
def upload(files: list[UploadFile] = File(...), user: dict = Depends(current_user)):
    results = []
    for f in files:
        data = f.file.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
        f.file.close()
        try:
            doc, duplicate = library.add_file(f.filename or "", data, owner=user["id"])
        except library.LibraryError as e:
            event("upload_rejected", file=f.filename, why=str(e), user=user["id"])
            results.append({"file": f.filename, "status": "rejected", "error": str(e)})
            continue
        if not duplicate:
            library.queue(doc["id"])
        # a duplicate of the file as it was uploaded, though the document has been edited since
        edited_since = duplicate and doc["sha256"] != library.ingestion.file_sha256(data)
        results.append({"file": f.filename, "status": "duplicate" if duplicate else "queued", "document": _public(doc),
                        "edited_since": edited_since})
    return results


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: str, user: dict = Depends(current_user)):
    doc = _own_doc(doc_id, user)
    if doc["status"] == "processing":
        raise HTTPException(409, "The document is still being indexed")
    library.delete(doc_id)


class UndoIn(BaseModel):
    edit_id: int | None = None   # the edit the button is under: refused unless it is still the latest


@router.post("/documents/{doc_id}/undo")
def undo_edit(doc_id: str, body: UndoIn | None = None, user: dict = Depends(current_user)):
    """Undo the document's most recent edit (the Undo button under an edit in the chat)."""
    doc = _own_doc(doc_id, user)
    from backend.library import editing
    try:
        result = editing.undo(doc["id"], body.edit_id if body else None)
    except editing.EditError as e:
        raise HTTPException(409, str(e)) from e
    event("undo_button", doc=doc_id, title=doc["title"])
    return {**result, "documents": list_documents(user)}


@router.get("/documents/{doc_id}/file")
def download(doc_id: str, inline: bool = False, user: dict = Depends(current_user)):
    """The stored file; inline=true shows it in the browser (the PDF preview) instead of downloading it."""
    doc = _own_doc(doc_id, user)
    path = config.LIBRARY_DIR / doc["file_name"]
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=Path(doc["file_name"]).name,
                        content_disposition_type="inline" if inline else "attachment")


PREVIEW_MAX_CHARS = 200_000


@router.get("/documents/{doc_id}/preview")
def preview(doc_id: str, user: dict = Depends(current_user)):
    """The document's text for the preview window (a PDF is shown from /file?inline=true instead)."""
    doc = _own_doc(doc_id, user)
    if not (config.LIBRARY_DIR / doc["file_name"]).exists():
        raise HTTPException(404, "File not found")
    from backend.library import editing
    try:
        text = editing.document_text(doc)
    except Exception as e:
        raise HTTPException(500, f"Couldn't read this document: {str(e)[:200]}") from e
    return {"title": doc["title"], "ext": doc["ext"], "text": text[:PREVIEW_MAX_CHARS],
            "truncated": len(text) > PREVIEW_MAX_CHARS}


@router.get("/documents/{doc_id}/pdf")
def download_pdf(doc_id: str, user: dict = Depends(current_user)):
    """The document as a PDF: a PDF as it is stored, any other document laid out from its text."""
    doc = _own_doc(doc_id, user)
    if not (config.LIBRARY_DIR / doc["file_name"]).exists():
        raise HTTPException(404, "File not found")
    if path := export.stored_pdf(doc):
        return FileResponse(path, filename=Path(doc["file_name"]).name, media_type="application/pdf")
    try:
        data = export.to_pdf(doc)
    except Exception as e:
        logging.getLogger(__name__).exception("PDF export of %s failed", doc["title"])
        raise HTTPException(500, f"Couldn't make a PDF of this document: {str(e)[:200]}") from e
    event("pdf_export", doc=doc_id, title=doc["title"], bytes=len(data))
    name = export.pdf_file_name(doc)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.post("/chat")
def chat(body: ChatIn, user: dict = Depends(current_user)):
    with _locks_guard:
        lock = _session_locks.setdefault(body.session_id, threading.Lock())
    with lock:
        try:
            result = agent.chat(body.session_id, body.message.strip(), owner=user["id"])
        except PermissionError:
            raise HTTPException(404, "Chat not found") from None
    return {**result, "documents": list_documents(user)}


@router.get("/sessions/{session_id}")
def session(session_id: str, user: dict = Depends(current_user)):
    s = catalog.load_session(session_id)
    if s["owner"] not in (None, user["id"]):
        raise HTTPException(404, "Chat not found")
    return {"messages": s["display"]}


@router.get("/limits")
def limits_now(user: dict = Depends(current_user)):
    """Groq tokens used in the last minute (one allowance, shared by every user) and this user's queue: polled by the
    page every second."""
    return batch.status_light(user["id"])


@router.get("/queue")
def queue(user: dict = Depends(current_user)):
    return batch.status(user["id"])


@router.delete("/batches/{batch_id}")
def cancel_batch(batch_id: str, user: dict = Depends(current_user)):
    found = catalog.get_batch(batch_id)
    if not found or found["owner"] != user["id"]:
        raise HTTPException(404, "Batch not found")
    n = catalog.cancel_batch(batch_id)
    event("batch_cancelled", batch=batch_id, tasks=n, user=user["id"])
    return {"cancelled": n}

