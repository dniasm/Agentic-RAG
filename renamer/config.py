"""This project's settings.

API keys stay in day7's .env. A .env in this folder is optional; anything set there wins (see day7.py).
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("RENAMER_DATA_DIR", PROJECT_ROOT / "data")).resolve()
DAY7_DIR = Path(os.environ.get("RENAMER_DAY7_DIR", PROJECT_ROOT.parent / "day7")).resolve()

LIBRARY_DIR = DATA_DIR / "library"        # the stored files, named after their current title
CATALOG_PATH = DATA_DIR / "catalog.db"    # documents, rename history, chat sessions
QDRANT_PATH = DATA_DIR / "qdrant"         # this project's own embedded vector store
COLLECTION_NAME = "renamer_docs"
OWNER = "library"                         # every chunk is stored under this one day7 "user"

MAX_UPLOAD_MB = 20
TITLE_SOURCE_CHARS = 6000   # opening text the title writer reads
TITLE_MAX_CHARS = 120

# Agent (Groq). The 20B takes over when the 120B is rate-limited.
AGENT_MODEL = os.environ.get("RENAMER_AGENT_MODEL", "openai/gpt-oss-120b")
BACKUP_MODEL = os.environ.get("RENAMER_BACKUP_MODEL", "openai/gpt-oss-20b")
AGENT_REASONING = os.environ.get("RENAMER_AGENT_REASONING", "low")
AGENT_MAX_STEPS = 8          # model calls per user message
OLDER_MAX_CHARS = 800        # earlier messages are cut to this many characters (same as day7's history_to_messages)
LIST_MAX_DOCUMENTS = 150   # list_documents shows at most this many titles (about 3,000 tokens)
HISTORY_TURNS = 8            # earlier user messages sent back to the model (only the last with its tool calls)

# Finding documents. Scores are on Voyage rerank-2.5's 0-1 scale (day7's calibrated thresholds).
MATCH_HIGH = 0.55     # at or above: a confident match
MATCH_LOW = 0.45      # below: not a match
MATCH_MARGIN = 0.10   # documents within this of the best one are too close to tell apart
NAME_EXACT = 0.90     # name similarity that counts as naming the document outright
# Without the reranker, Gemini cosine similarity is used instead (day7: answerable 0.58+, unrelated 0.47-0.59).
COSINE_HIGH = 0.62
COSINE_LOW = 0.55
COSINE_MARGIN = 0.03

# Groq's free tier for the 120B (tokens per minute) and the batch queue that works within it.
TPM_LIMIT = int(os.environ.get("RENAMER_TPM_LIMIT", 8000))
TPM_WINDOW_SECONDS = 60.0
TASK_ESTIMATE_TOKENS = 3500   # assumed cost of one queued task until one has run (then the last one's cost + 15%)
BACKUP_TPM_LIMIT = int(os.environ.get("RENAMER_BACKUP_TPM_LIMIT", 8000))   # the 20B's own limit
RETRY_WAIT_SECONDS = 65.0     # how long a chat reply may wait for the 120B after the 20B gave an empty answer
CALL_ESTIMATE_TOKENS = 2000   # assumed size of a model call until one has been measured (1,200-2,100 seen)
SUMMARY_REFRESH_SHARE = 0.05  # an edit or undo that changes less of the text than this keeps the old summary
EDIT_REFRESH_TOKENS = 2200   # the summary refresh after an edit or undo (measured by count, not by a run yet)
BATCH_MAX_TASKS = 50
BATCH_TASK_MAX_CHARS = 600

# Voyage's free tier allows about 3 rerank calls a minute. Past that, find uses cosine scores at once instead of
# making a call that fails. Results of find are reused until the library changes (or FIND_CACHE_SECONDS pass).
VOYAGE_CALLS_PER_MINUTE = int(os.environ.get("RENAMER_VOYAGE_CALLS_PER_MINUTE", 3))
FIND_CACHE_SECONDS = 600
