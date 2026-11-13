"""Bridge to day7's code: its ingestion pipeline, hybrid search, reranker and model settings are imported, not copied.

day7's settings read its own .env (API keys) and then the environment, which wins. Before anything from day7
is imported, the environment points its storage here, so this project never touches day7's vector store
or database:

    vector store     data/qdrant, collection "renamer_docs"
    query cache      data/embedding_cache.db (day7 puts it next to DATABASE_PATH)

Import day7 modules from this module (`from renamer.day7 import ingestion`), never directly, so the
environment is always set first.
"""
import atexit
import os
import sys

from dotenv import dotenv_values

from renamer import config

if not (config.DAY7_DIR / "backend" / "rag" / "ingestion.py").exists():
    raise RuntimeError(f"day7 not found at {config.DAY7_DIR}. Set RENAMER_DAY7_DIR to its folder.")

config.DATA_DIR.mkdir(parents=True, exist_ok=True)

# Keys in this project's .env override day7's.
for key, value in dotenv_values(config.PROJECT_ROOT / ".env").items():
    if value:
        os.environ[key] = value

os.environ["QDRANT_URL"] = os.environ.get("RENAMER_QDRANT_URL", "")
os.environ["QDRANT_PATH"] = str(config.QDRANT_PATH)
os.environ["COLLECTION_NAME"] = config.COLLECTION_NAME
os.environ["DATABASE_PATH"] = str(config.DATA_DIR / "day7_unused.db")
os.environ["UPLOAD_DIR"] = str(config.LIBRARY_DIR)

if str(config.DAY7_DIR) not in sys.path:
    sys.path.insert(0, str(config.DAY7_DIR))

from backend.config.settings import settings  # noqa: E402
from backend.rag import ingestion, rerank, retrieval, sparse, vectorstore  # noqa: E402

# Close the vector store before the interpreter starts tearing down; left to the garbage collector, Qdrant's
# local client tries to close during shutdown and prints "ImportError: sys.meta_path is None".
atexit.register(vectorstore.close_client)

__all__ = ["settings", "ingestion", "rerank", "retrieval", "sparse", "vectorstore"]
