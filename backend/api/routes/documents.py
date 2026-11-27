"""day7's document routes, on the document library (backend/library): one catalog, one file store, one indexing
queue. An upload is stored, titled and indexed in the background; GET /documents shows when it is indexed."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from backend.api.dependencies import get_current_user
from backend.config.settings import settings
from backend.library import catalog, library
from backend.models import database as db
from backend.models.schemas import DocumentOut, UploadOut

router = APIRouter(prefix="/documents", tags=["Documents"])


def _out(doc: dict) -> DocumentOut:
    return DocumentOut(id=doc["id"], title=doc["title"], filename=doc["original_name"], ext=doc["ext"] or "",
                       size_bytes=doc["size_bytes"],
                       pages=doc["pages"] or 0, chunks=doc["chunks"] or 0, status=doc["status"], error=doc["error"],
                       created_at=doc["created_at"],
                       edit_id=(catalog.last_edit(doc["id"]) or {}).get("id"),
                       redo_id=(catalog.last_undone(doc["id"]) or {}).get("id"))


@router.get("", response_model=list[DocumentOut])
def list_documents(user: dict = Depends(get_current_user)):
    return [_out(d) for d in catalog.list_documents(owner=user["id"])]


@router.post("/upload", response_model=UploadOut)
def upload_document(file: UploadFile = File(...), conversation_id: str | None = Form(None),
                    user: dict = Depends(get_current_user)):
    """`conversation_id` (optional): the chat the file is attached to, so "the document I just uploaded" finds it."""
    conversation_id = (conversation_id or "").strip() or None
    if conversation_id and not db.get_conversation(user["id"], conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    data = file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    file.file.close()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_mb} MB limit")
    try:
        doc, duplicate = library.add_file(file.filename or "", data, owner=user["id"], conversation_id=conversation_id)
    except library.LibraryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if duplicate:
        if conversation_id and doc.get("conversation_id") != conversation_id:   # uploaded again here: it belongs to this chat now
            catalog.update_document(doc["id"], conversation_id=conversation_id)
            doc = catalog.get_document(doc["id"]) or doc
        status = "duplicate" if doc["status"] == "indexed" else "processing"
        return UploadOut(status=status, filename=doc["original_name"], chunks_indexed=doc["chunks"] or 0,
                         document=_out(doc))
    library.queue(doc["id"])
    return UploadOut(status="processing", filename=doc["original_name"], chunks_indexed=0, document=_out(doc))


@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: str, user: dict = Depends(get_current_user)):
    doc = catalog.get_document(doc_id)
    if not doc or doc["owner"] != user["id"]:
        raise HTTPException(status_code=404, detail="Document not found")
    library.delete(doc_id)
