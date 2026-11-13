"""HTTP API and the chat page.  Run:  python -m renamer serve   (http://127.0.0.1:8200)

Every /api/ endpoint except health and the sign-in ones needs a login (a cookie, see renamer/auth.py), and works only
in that user's library: another user's document, chat or batch answers as if it did not exist.
"""
import logging
import mimetypes
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from renamer import agent, auth, batch, catalog, config, export, library, logs
from renamer.logs import event

# The page loads its scripts as ES modules, which browsers run only when served as JavaScript; on Windows,
# Python's mimetypes can read .js as text/plain from the registry.
mimetypes.add_type("text/javascript", ".js")
logs.setup()   # data/logs/events.log (major events) and data/logs/all.log (everything)


@asynccontextmanager
async def lifespan(_app):
    event("server_started", documents=len(catalog.list_documents()), users=catalog.count_users())
    batch.start()      # the queue worker
    yield
    batch.stop()
    event("server_stopped")


app = FastAPI(title="Document Renaming Agent", lifespan=lifespan)


@app.middleware("http")
async def always_fresh_page(request, call_next):
    """The page's own files (HTML, CSS, JS) are checked with the server on every load, so a browser never pairs a
    new page with an old cached stylesheet. Unchanged files still come back as a quick 304."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# One file at a time: Gemini's free tier allows 100 embedding requests a minute.
_ingest_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")
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
        _ingest_pool.submit(library.process, doc["id"])


_resume_unfinished()


# ---------------------------------------------------------------- who is asking
def current_user(request: Request) -> dict:
    user = auth.user_for(request.cookies.get(auth.COOKIE))
    if not user:
        raise HTTPException(401, "Please log in.")
    return user


def _own_doc(doc_id: str, user: dict) -> dict:
    doc = catalog.get_document(doc_id)
    if not doc or doc["owner"] != user["id"]:
        raise HTTPException(404, "Document not found")
    return doc


def _signed_in(request: Request, user: dict, token: str) -> JSONResponse:
    response = JSONResponse({"user": auth.public(user)})
    response.set_cookie(auth.COOKIE, token, max_age=auth.LOGIN_DAYS * 86400, path="/", httponly=True,
                        samesite="lax", secure=request.url.scheme == "https")   # https through the tunnel
    return response


class SignUpIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)
    invite: str = Field(default="", max_length=128)


class LogInIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class ChatIn(BaseModel):
    session_id: str = Field(min_length=6, max_length=64)
    message: str = Field(min_length=1, max_length=4000)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/auth/state")
def auth_state(request: Request):
    """Signed in or not, and whether a new account can be made ("first", "invite" or "closed")."""
    user = auth.user_for(request.cookies.get(auth.COOKIE))
    return {"user": auth.public(user) if user else None, "signups": auth.signups_open()}


@app.post("/api/auth/signup")
def sign_up(body: SignUpIn, request: Request):
    try:
        user, token = auth.sign_up(body.username, body.password, body.invite)
    except auth.AuthError as e:
        raise HTTPException(e.status, str(e)) from e
    return _signed_in(request, user, token)


@app.post("/api/auth/login")
def log_in(body: LogInIn, request: Request):
    try:
        user, token = auth.log_in(body.username, body.password)
    except auth.AuthError as e:
        raise HTTPException(e.status, str(e)) from e
    return _signed_in(request, user, token)


@app.post("/api/auth/logout")
def log_out(request: Request):
    auth.log_out(request.cookies.get(auth.COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE, path="/")
    return response


# ---------------------------------------------------------------- the user's library
@app.get("/api/documents")
def list_documents(user: dict = Depends(current_user)):
    docs = catalog.list_documents(owner=user["id"])
    histories = catalog.rename_histories()
    return [{**_public(d), "history": histories.get(d["id"], [])} for d in docs]


@app.post("/api/documents/upload")
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
            _ingest_pool.submit(library.process, doc["id"])
        # a duplicate of the file as it was uploaded, though the document has been edited since
        edited_since = duplicate and doc["sha256"] != library.ingestion.file_sha256(data)
        results.append({"file": f.filename, "status": "duplicate" if duplicate else "queued", "document": _public(doc),
                        "edited_since": edited_since})
    return results


@app.delete("/api/documents/{doc_id}", status_code=204)
def delete_document(doc_id: str, user: dict = Depends(current_user)):
    doc = _own_doc(doc_id, user)
    if doc["status"] == "processing":
        raise HTTPException(409, "The document is still being indexed")
    library.delete(doc_id)


@app.get("/api/documents/{doc_id}/file")
def download(doc_id: str, user: dict = Depends(current_user)):
    doc = _own_doc(doc_id, user)
    path = config.LIBRARY_DIR / doc["file_name"]
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=Path(doc["file_name"]).name)


@app.get("/api/documents/{doc_id}/pdf")
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


@app.post("/api/chat")
def chat(body: ChatIn, user: dict = Depends(current_user)):
    with _locks_guard:
        lock = _session_locks.setdefault(body.session_id, threading.Lock())
    with lock:
        try:
            result = agent.chat(body.session_id, body.message.strip(), owner=user["id"])
        except PermissionError:
            raise HTTPException(404, "Chat not found") from None
    return {**result, "documents": list_documents(user)}


@app.get("/api/sessions/{session_id}")
def session(session_id: str, user: dict = Depends(current_user)):
    s = catalog.load_session(session_id)
    if s["owner"] not in (None, user["id"]):
        raise HTTPException(404, "Chat not found")
    return {"messages": s["display"]}


@app.get("/api/limits")
def limits_now(user: dict = Depends(current_user)):
    """Groq tokens used in the last minute (one allowance, shared by every user) and this user's queue: polled by the
    page every second."""
    return batch.status_light(user["id"])


@app.get("/api/queue")
def queue(user: dict = Depends(current_user)):
    return batch.status(user["id"])


@app.delete("/api/batches/{batch_id}")
def cancel_batch(batch_id: str, user: dict = Depends(current_user)):
    found = catalog.get_batch(batch_id)
    if not found or found["owner"] != user["id"]:
        raise HTTPException(404, "Batch not found")
    n = catalog.cancel_batch(batch_id)
    event("batch_cancelled", batch=batch_id, tasks=n, user=user["id"])
    return {"cancelled": n}


app.mount("/", StaticFiles(directory=config.PROJECT_ROOT / "web", html=True), name="web")
