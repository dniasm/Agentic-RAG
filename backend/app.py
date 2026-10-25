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
from backend.models.database import init_db
from backend.rag import rerank
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
    rerank.warm_up_in_background()  # loading torch can take a minute; the app works meanwhile

    try:
        yield
    finally:
        close_client()


app = FastAPI(
    title = "Agentic RAG API",
    description = "Backend API for the Agentic RAG Application",
    version = "1.0.0",
    lifespan = lifespan,
)

#CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_origin_regex = r"http://(localhost|127\.0\.0\.1)(:\d+)?",  # any local dev port
    allow_credentials = True,
    allow_methods = ["*"],
    allow_headers = ["*"],
)

#ROUTES
for module in (health, auth, chat, documents):
    app.include_router(module.router, prefix = "/api")

@app.get("/")
def root():
    return{
        "message" : "Agentic RAG API is running"
    }
