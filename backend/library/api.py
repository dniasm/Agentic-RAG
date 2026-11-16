"""The document library's HTTP API, mounted by backend/app.py at /api/library: the Documents page's actions
(rename, undo, redo, preview, export, queue). Chat goes through /api/chat (backend/api/routes/chat.py).

Every endpoint needs a login (the app's cookie: backend/api/routes/auth.py), and works only
in that user's library: another user's document, chat or batch answers as if it did not exist.
"""
import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from backend.api.dependencies import get_current_user
from backend.library import batch, catalog, config, export, library, logs, naming
from backend.library.logs import event

router = APIRouter(prefix="/library", tags=["Library"])


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


class RenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=400)
    confirmed: bool = False   # use the name though another document already has it


@router.post("/documents/{doc_id}/rename")
def rename_document(doc_id: str, body: RenameIn, user: dict = Depends(current_user)):
    """The Library page's Rename. A name another document already has is refused (409, with `conflicts`) until
    it is sent again with confirmed=true."""
    doc = _own_doc(doc_id, user)
    if doc["status"] != "indexed":
        raise HTTPException(409, "The document is still being indexed" if doc["status"] == "processing"
                            else "The document failed to index, so it can't be renamed")
    try:
        title = naming.clean_title(body.title)
    except naming.TitleError as e:
        raise HTTPException(400, str(e)) from e
    if title == doc["title"]:
        return {"old_title": title, "new_title": title, "file_name": Path(doc["file_name"]).name, "unchanged": True}
    conflicts = [c["title"] for c in library.name_conflicts(title, exclude=doc_id)]
    if conflicts and not body.confirmed:
        raise HTTPException(409, {"message": f"Another document is already called “{title}”.",
                                  "conflicts": conflicts, "title": title})
    result = library.rename(doc_id, title)
    event("rename_button", doc=doc_id, old=result["old_title"], new=result["new_title"])
    return {**result, "file_name": Path(result["file_name"]).name}


class EditIn(BaseModel):
    find: str = Field(min_length=1, max_length=20_000)    # the passage selected in the preview
    text: str = Field(max_length=20_000)                  # what it becomes ("" deletes it)
    replace_all: bool = False                             # every place it appears, after being told how many
    convert_pdf: bool = False                             # agreed to a Markdown copy when the PDF's look can't be kept


def _edit_result(result: dict, doc_id: str, user: dict) -> dict:
    return {**result, "edit_id": (catalog.last_edit(doc_id) or {}).get("id"), "documents": list_documents(user)}


@router.post("/documents/{doc_id}/edit")
def edit_document(doc_id: str, body: EditIn, user: dict = Depends(current_user)):
    """The preview's "Edit selection": replace the selected passage, through the same edit as the chat's (a PDF in
    place, checked; undo and redo). 409 with `ambiguous` (it appears in several places: select more, or
    replace_all) or `needs_conversion` (the PDF's look can't be kept: agree with convert_pdf); 400 for anything
    else that stops it (the message says why)."""
    doc = _own_doc(doc_id, user)
    from backend.library import editing
    try:
        result = editing.edit(doc["id"], "replace", find=body.find, text=body.text, replace_all=body.replace_all,
                              convert_pdf=body.convert_pdf)
    except editing.NeedsConversion as e:
        raise HTTPException(409, {"message": str(e), "needs_conversion": True, "too_long": e.too_long}) from e
    except editing.EditError as e:
        raise HTTPException(400, str(e)) from e
    if result["status"] == "ambiguous":
        raise HTTPException(409, {"message": f"That passage appears {result['matches']} times.",
                                  "ambiguous": True, "matches": result["matches"], "contexts": result["contexts"]})
    event("edit_button", doc=doc_id, title=doc["title"], converted=result.get("converted", False))
    return _edit_result(result, doc_id, user)


class TextIn(BaseModel):
    text: str = Field(max_length=2_000_000)
    sha256: str = Field(min_length=64, max_length=64)   # of the text as it was opened (GET /text)


@router.get("/documents/{doc_id}/text")
def document_text(doc_id: str, user: dict = Depends(current_user)):
    """A TXT / MD document's whole text for the website's editor, and the SHA-256 to send back with the save."""
    doc = _own_doc(doc_id, user)
    from backend.library import editing
    try:
        return editing.file_text(doc["id"])
    except editing.EditError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/documents/{doc_id}/text")
def save_document_text(doc_id: str, body: TextIn, user: dict = Depends(current_user)):
    """Save the editor's text as one edit (Undo puts the old text back). 409 when the document changed since it
    was opened: nothing is overwritten."""
    doc = _own_doc(doc_id, user)
    from backend.library import editing
    try:
        result = editing.rewrite(doc["id"], body.text, body.sha256)
    except editing.EditConflict as e:
        raise HTTPException(409, str(e)) from e
    except editing.EditError as e:
        raise HTTPException(400, str(e)) from e
    event("text_saved", doc=doc_id, title=doc["title"])
    return _edit_result(result, doc_id, user)


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


@router.post("/documents/{doc_id}/redo")
def redo_edit(doc_id: str, body: UndoIn | None = None, user: dict = Depends(current_user)):
    """Bring back the edit undone most recently (the Redo button under an undone edit in the chat)."""
    doc = _own_doc(doc_id, user)
    from backend.library import editing
    try:
        result = editing.redo(doc["id"], body.edit_id if body else None)
    except editing.EditError as e:
        raise HTTPException(409, str(e)) from e
    event("redo_button", doc=doc_id, title=doc["title"])
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

