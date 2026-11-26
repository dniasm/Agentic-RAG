"""The document library's settings.

Values that differ per deployment are fields on day7's Settings (library_*, signup_code), set in the one .env.
The rest are constants. Storage: the vector store and its collection are day7's (the Document service owns the store,
settings.collection_name); files, versions and logs live under settings.library_data_dir; the catalog's tables are in day7's app.db.
"""
from pathlib import Path

from backend.config.settings import PROJECT_ROOT, settings

DATA_DIR = Path(settings.library_data_dir).resolve()
LIBRARY_DIR = DATA_DIR / "library"        # the stored files, named after their current title
OWNER = "library"   # the owner of documents from before accounts existed (and of tests); users are their own ids
SIGNUP_CODE = settings.signup_code   # needed for every sign-up after the first; empty: closed

MAX_UPLOAD_MB = 20
TITLE_SOURCE_CHARS = 6000   # opening text the title writer reads
TITLE_MAX_CHARS = 120

# Agent (Groq). The 20B takes over when the 120B is rate-limited.
AGENT_MODEL = settings.library_agent_model
BACKUP_MODEL = settings.library_backup_model
AGENT_REASONING = settings.library_agent_reasoning
AGENT_MAX_STEPS = 8          # model calls per user message
OLDER_MAX_CHARS = 800        # earlier messages are cut to this many characters (same as day7's history_to_messages)
OPENING_CHARS = 1500       # document_details shows this much of a document that has no summary
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
TPM_LIMIT = settings.library_tpm_limit
TPM_WINDOW_SECONDS = 60.0
TASK_ESTIMATE_TOKENS = 3500   # assumed cost of one queued task until one has run (then the last one's cost + 15%)
BACKUP_TPM_LIMIT = settings.library_backup_tpm_limit   # the 20B's own limit
RETRY_WAIT_SECONDS = 65.0     # how long a chat reply may wait for the 120B after the 20B gave an empty answer
CALL_ESTIMATE_TOKENS = 2000   # assumed size of a model call until one has been measured (1,200-2,100 seen)
SUMMARY_REFRESH_SHARE = 0.05  # an edit or undo that changes less of the text than this keeps the old summary
EDIT_REFRESH_TOKENS = 2200   # the summary refresh after an edit or undo (measured by count, not by a run yet)
BATCH_MAX_TASKS = 50
BATCH_TASK_MAX_CHARS = 600

# Voyage's free tier allows about 3 requests a minute, shared with day7's reranker and memory: find asks day7's
# limiter (backend/config/voyage.py, priority FIND) and uses cosine scores at once when it is busy. Results of find
# are reused until the library changes (or FIND_CACHE_SECONDS pass).
FIND_CACHE_SECONDS = 600
