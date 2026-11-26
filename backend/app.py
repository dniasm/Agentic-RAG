"""FastAPI entrypoint. Run either way:

    (project root)  uvicorn backend.app:app --reload --port 8000
    (backend/)      uvicorn app:app --reload --port 8000
"""
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Make the `backend` package importable when launched from inside backend/.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import grpc
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import admin, auth, chat, documents, health, ws
from backend.realtime.hub import hub
from backend.config import redact
from backend.config.settings import settings
from backend.library import api as library_api
from backend.library.auth import UserServiceDown
from backend.models.database import init_db
from backend import version
from backend.compression import Compression
from backend.request_log import RequestLogMiddleware
from backend.security import SecurityMiddleware
from services.common.log import setup_logging
from backend.web import mount_frontend

setup_logging("orchestrator")   # logs/orchestrator/<date>.log, logs/audit/, the console (services/common/log.py)
redact.guard_handlers()          # the configured API keys, exactly, on top of the pattern masks
logging.getLogger("backend.library").setLevel(logging.DEBUG)   # the library's tool calls and waits reach the files
for _noisy in ("httpcore", "google_genai.models", "google_genai._api_client", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.INFO)  # one line per HTTP call drowns out the useful logs


@asynccontextmanager
async def lifespan(_: FastAPI):
    missing = [k for k in ("groq_api_key", "gemini_api_key") if not getattr(settings, k)]
    if missing:
        raise RuntimeError(f"Missing required settings in .env: {', '.join(k.upper() for k in missing)}")
    init_db()
    library_api.startup()  # the document library: logs, unfinished files, the batch queue worker
    version.log_startup()  # the commit it runs, so an old backend left running can be told apart
    try:
        yield
    finally:
        from backend.api.routes.chat import flush_pending_memory
        hub.close_all()  # open WebSockets end with a normal close
        flush_pending_memory()  # facts still waiting for a batched extraction
        library_api.shutdown()


app = FastAPI(
    title = "Agentic RAG API",
    description = "Backend API for the Agentic RAG Application",
    version = "1.0.0",
    lifespan = lifespan,
    # the API's own description only in development (API_DOCS=true): backend/security.py
    docs_url = "/docs" if settings.api_docs else None,
    redoc_url = "/redoc" if settings.api_docs else None,
    openapi_url = "/openapi.json" if settings.api_docs else None,
)

#CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials = True,
    allow_methods = ["*"],
    allow_headers = ["*"],
)

# Gzip for scripts, styles, pages and JSON (backend/compression.py), inside the security checks: added first, it
# runs after them, so a refused request is never compressed and the security headers are on every response.
app.add_middleware(Compression)

# Host and cross-site checks, security headers (backend/security.py). Added last, so it runs first.
app.add_middleware(SecurityMiddleware)

# The request id and the access line. Added last, so outermost: even a refused request gets both.
app.add_middleware(RequestLogMiddleware)

@app.exception_handler(UserServiceDown)
@app.exception_handler(grpc.RpcError)
def _service_unavailable(request, exc):
    """A service the request needs didn't answer: refused, never let through (the sign-in check fails closed)."""
    logging.getLogger(__name__).error("Service unavailable on %s: %s", request.url.path, exc)
    return JSONResponse({"detail": "A service this needs is unavailable. Try again shortly."}, status_code=503)


#ROUTES
for module in (health, auth, chat, documents, admin, ws):
    app.include_router(module.router, prefix = "/api")
app.include_router(library_api.router, prefix = "/api")  # /api/library/*

# Production: the built front end at / (backend/web.py); without a build, / only says the API is up.
if not mount_frontend(app):
    @app.get("/")
    def root():
        return{
            "message" : "Agentic RAG API is running"
        }
