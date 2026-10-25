import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from backend.api.dependencies import get_current_user
from backend.config.settings import settings
from backend.models import database as db
from backend.models.schemas import DocumentOut, UploadOut
from backend.rag import vectorstore
from backend.rag.ingestion import SUPPORTED_EXTENSIONS, IngestionError, file_sha256, ingest_file

log = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["Documents"])


def _safe_name(name: str) -> str:
    name = Path(name).name  # strip any client-supplied directories
    return re.sub(r"[^A-Za-z0-9._ -]", "_", name)[:150] or "document"


def _user_dir(user_id: str) -> Path:
    path = Path(settings.upload_dir) / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.get("", response_model=list[DocumentOut])
def list_documents(user: dict = Depends(get_current_user)):
    return db.list_documents(user["id"])


@router.post("/upload", response_model=UploadOut)
def upload_document(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    filename = _safe_name(file.filename or "")
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400,
                            detail=f"Unsupported file type. Allowed: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")

    data = file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    file.file.close()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_mb} MB limit")
    if not data:
        raise HTTPException(status_code=400, detail="File is empty")

    digest = file_sha256(data)
    existing = db.find_document_by_hash(user["id"], digest)
    if existing and existing["status"] == "indexed":
        return UploadOut(status="duplicate", filename=existing["filename"],
                         chunks_indexed=existing["chunks"], document=existing)
    if existing:  # a previous attempt failed; retry from scratch
        vectorstore.delete_document_vectors(user["id"], existing["id"])
        db.delete_document(user["id"], existing["id"])

    doc = db.add_document(user["id"], filename, digest, len(data))
    path = _user_dir(user["id"]) / f"{doc['id']}{ext}"
    path.write_bytes(data)

    try:
        result = ingest_file(path, user_id=user["id"], doc_id=doc["id"], filename=filename)
    except IngestionError as e:
        db.update_document(doc["id"], status="failed", error=str(e))
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        log.exception("Ingestion failed")
        vectorstore.delete_document_vectors(user["id"], doc["id"])
        db.update_document(doc["id"], status="failed", error=str(e)[:300])
        raise HTTPException(status_code=500, detail=f"Indexing failed: {str(e)[:200]}")

    db.update_document(doc["id"], status="indexed", pages=result["pages"], chunks=result["chunks"])
    doc = db.get_document(user["id"], doc["id"])
    return UploadOut(status="success", filename=filename, chunks_indexed=result["chunks"], document=doc)


@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: str, user: dict = Depends(get_current_user)):
    doc = db.get_document(user["id"], doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    vectorstore.delete_document_vectors(user["id"], doc_id)
    for f in _user_dir(user["id"]).glob(f"{doc_id}.*"):
        f.unlink(missing_ok=True)
    db.delete_document(user["id"], doc_id)
