import logging
import re
from concurrent.futures import ThreadPoolExecutor
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

# Indexing runs here, after the upload has returned: embedding a long file is paced to Gemini's rate limit and can
# take minutes, which used to hold the upload request open the whole time. One file at a time: the embedded Qdrant
# store is not safe for concurrent writers, and the files share Gemini's per-minute limit anyway.
_ingest_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")


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
    if existing and existing["status"] in ("indexed", "processing"):
        status = "duplicate" if existing["status"] == "indexed" else "processing"
        return UploadOut(status=status, filename=existing["filename"], chunks_indexed=existing["chunks"],
                         document=existing)
    if existing:  # a previous attempt failed; retry from scratch
        vectorstore.delete_document_vectors(user["id"], existing["id"])
        db.delete_document(user["id"], existing["id"])

    doc = db.add_document(user["id"], filename, digest, len(data))  # status "processing"
    path = _user_dir(user["id"]) / f"{doc['id']}{ext}"
    path.write_bytes(data)
    _ingest_worker.submit(_index, user["id"], doc["id"], path, filename)
    return UploadOut(status="processing", filename=filename, chunks_indexed=0, document=doc)


def stop_indexing() -> None:
    """On shutdown: files still queued stay "processing" and are marked interrupted at the next start."""
    _ingest_worker.shutdown(wait=False, cancel_futures=True)


def _index(user_id: str, doc_id: str, path: Path, filename: str) -> None:
    try:
        result = ingest_file(path, user_id=user_id, doc_id=doc_id, filename=filename)
    except IngestionError as e:  # e.g. a scanned PDF with no text: the user can act on the message
        db.update_document(doc_id, status="failed", error=str(e))
        return
    except Exception as e:
        log.exception("Ingestion failed")
        vectorstore.delete_document_vectors(user_id, doc_id)
        db.update_document(doc_id, status="failed", error=f"Indexing failed: {str(e)[:250]}")
        return
    if db.get_document(user_id, doc_id) is None:  # removed while it was being indexed
        vectorstore.delete_document_vectors(user_id, doc_id)
        return
    db.update_document(doc_id, status="indexed", pages=result["pages"], chunks=result["chunks"])


@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: str, user: dict = Depends(get_current_user)):
    doc = db.get_document(user["id"], doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    vectorstore.delete_document_vectors(user["id"], doc_id)
    for f in _user_dir(user["id"]).glob(f"{doc_id}.*"):
        f.unlink(missing_ok=True)
    db.delete_document(user["id"], doc_id)
