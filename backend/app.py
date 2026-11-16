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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import auth, chat, documents, health
from backend.config.settings import settings
from backend.library import api as library_api
from backend.memory.store import ensure_memory_collection
from backend.models.database import init_db
from backend.compression import Compression
from backend.security import SecurityMiddleware
from backend.web import mount_frontend
from backend.rag.vectorstore import get_client, close_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.INFO)  # one line per HTTP call drowns out the useful logs


@asynccontextmanager
async def lifespan(_: FastAPI):
    missing = [k for k in ("groq_api_key", "gemini_api_key") if not getattr(settings, k)]
    if missing:
        raise RuntimeError(f"Missing required settings in .env: {', '.join(k.upper() for k in missing)}")
    init_db()
    get_client()  # open / create the Qdrant collection up front
    library_api.startup()  # the document library: logs, unfinished files, the batch queue worker
    try:  # once here, not on every message; a problem only disables recall of past conversations
        ensure_memory_collection()
    except Exception as e:
        logging.getLogger(__name__).warning("Conversation memory unavailable: %s", e)

    try:
        yield
    finally:
        from backend.api.routes.chat import flush_pending_memory
        flush_pending_memory()  # facts still waiting for a batched extraction
        library_api.shutdown()
        close_client()


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

#ROUTES
for module in (health, auth, chat, documents):
    app.include_router(module.router, prefix = "/api")
app.include_router(library_api.router, prefix = "/api")  # /api/library/*

# Production: the built front end at / (backend/web.py); without a build, / only says the API is up.
if not mount_frontend(app):
    @app.get("/")
    def root():
        return{
            "message" : "Agentic RAG API is running"
        }
