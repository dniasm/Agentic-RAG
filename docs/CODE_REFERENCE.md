# Agentic RAG — Code Reference

A file-by-file, function-by-function reference for the whole codebase.

- For **what the system does and how to run it**, see [`README.md`](../README.md).
- For **architecture, API and configuration**, see [`DOCUMENTATION.md`](DOCUMENTATION.md).
- This document explains **the code itself**: every file, every function, what it takes, what it returns,
  how it works, and who calls it. Line numbers (`L42`) refer to the current source.

**Conventions used in the code**

| Pattern | Meaning |
|---|---|
| `_name` | Private helper, only used inside its own file. |
| `@lru_cache` | The function builds its result once and returns the same object on every later call (used for clients and settings). |
| `@router.get/post/delete(...)` | FastAPI endpoint: runs when that URL + method is requested. |
| `Depends(fn)` | FastAPI dependency: `fn` runs first and its return value is injected as the parameter. |
| `with tx() as c:` | A serialized SQLite transaction (commit on success, rollback on error). |
| Node function `x_node(state) -> dict` | A LangGraph step: reads the agent state, returns only the keys it changes. |

---

## Contents

**Backend**
1. [`backend/app.py`](#1-backendapppy)
2. [`backend/config/settings.py`](#2-backendconfigsettingspy)
3. [`backend/config/llm.py`](#3-backendconfigllmpy)
4. [`backend/models/database.py`](#4-backendmodelsdatabasepy)
5. [`backend/models/schemas.py`](#5-backendmodelsschemaspy)
6. [`backend/api/security.py`](#6-backendapisecuritypy)
7. [`backend/api/dependencies.py`](#7-backendapidependenciespy)
8. [`backend/api/routes/health.py`](#8-backendapirouteshealthpy)
9. [`backend/api/routes/auth.py`](#9-backendapiroutesauthpy)
10. [`backend/api/routes/documents.py`](#10-backendapiroutesdocumentspy)
11. [`backend/api/routes/chat.py`](#11-backendapirouteschatpy)
12. [`backend/rag/vectorstore.py`](#12-backendragvectorstorepy)
13. [`backend/rag/ingestion.py`](#13-backendragingestionpy)
14. [`backend/rag/retrieval.py`](#14-backendragretrievalpy)
15. [`backend/tools/__init__.py`](#15-backendtoolsinitpy)
16. [`backend/tools/greeting.py`](#16-backendtoolsgreetingpy)
17. [`backend/tools/direct_answer.py`](#17-backendtoolsdirect_answerpy)
18. [`backend/tools/rag.py`](#18-backendtoolsragpy)
19. [`backend/tools/web_search.py`](#19-backendtoolsweb_searchpy)
20. [`backend/evaluation/evaluator.py`](#20-backendevaluationevaluatorpy)
21. [`backend/graph/state.py`](#21-backendgraphstatepy)
22. [`backend/graph/nodes.py`](#22-backendgraphnodespy)
23. [`backend/graph/workflow.py`](#23-backendgraphworkflowpy)
24. [Package markers & data folders](#24-package-markers--data-folders)

**Frontend**

25. [Build & config files](#25-frontend-build--config-files)
26. [`src/main.jsx` & `src/index.css`](#26-srcmainjsx--srcindexcss)
27. [`src/services/api.jsx`](#27-srcservicesapijsx)
28. [`src/App.jsx`](#28-srcappjsx)
29. [`src/pages/Chat.jsx`](#29-srcpageschatjsx)
30. [`src/pages/Documents.jsx`](#30-srcpagesdocumentsjsx)
31. [`src/pages/Dashboard.jsx`](#31-srcpagesdashboardjsx)
32. [`src/components/Sidebar.jsx`](#32-srccomponentssidebarjsx)
33. [`src/components/ChatWindow.jsx`](#33-srccomponentschatwindowjsx)
34. [`src/components/InputBox.jsx`](#34-srccomponentsinputboxjsx)
35. [`src/components/Message.jsx`](#35-srccomponentsmessagejsx)
36. [`src/components/SourceCard.jsx`](#36-srccomponentssourcecardjsx)
37. [`src/components/ToolBadge.jsx`](#37-srccomponentstoolbadgejsx)

**Project root**

38. [Root files](#38-root-files) (`.env`, `.gitignore`, `requirements.txt`, `test_rag_pipeline.py`, …)

[Appendix A — Call graph of a chat request](#appendix-a--call-graph-of-a-chat-request)
[Appendix B — Call graph of a document upload](#appendix-b--call-graph-of-a-document-upload)

---

# Backend

## 1. `backend/app.py`

**Purpose:** entry point of the API. Creates the FastAPI application, runs startup checks, configures CORS
and mounts all routers under `/api`.

**Start command:** `uvicorn backend.app:app --reload` (project root) or `uvicorn app:app --reload` (inside `backend/`).

### Module-level code

| Line | Code | Explanation |
|---|---|---|
| L12–14 | `_PROJECT_ROOT = …; sys.path.insert(0, _PROJECT_ROOT)` | Adds the project root to Python's import path, so `from backend.… import …` works even when Uvicorn is started from inside `backend/`. |
| L16–20 | imports | Routers, settings, `init_db`, `get_client`. |
| L22 | `logging.basicConfig(level=INFO, …)` | Enables `INFO` logs with timestamps for the whole app. |
| L38–43 | `app = FastAPI(…, lifespan=lifespan)` | The application object Uvicorn serves. |
| L46–53 | `app.add_middleware(CORSMiddleware, …)` | Allows browser requests from `CORS_ORIGINS` and from any `localhost`/`127.0.0.1` port (`allow_origin_regex`). |
| L56–57 | `for module in (health, auth, chat, documents): app.include_router(module.router, prefix="/api")` | Mounts every route module under `/api`. |

### `async def lifespan(_: FastAPI)` — L28
Startup/shutdown hook (`@asynccontextmanager`). Code before `yield` runs at startup; after `yield` at shutdown.

1. Checks that `groq_api_key` and `gemini_api_key` are set; raises `RuntimeError` listing the missing ones otherwise
   (the server refuses to start rather than failing later on the first request).
2. `init_db()` — creates SQLite tables if missing.
3. `get_client()` — opens Qdrant and creates the collection if missing.

### `def root()` — L59
`GET /` → `{"message": "Agentic RAG API is running"}`. A simple "is it up?" response outside `/api`.

---

## 2. `backend/config/settings.py`

**Purpose:** single source of truth for configuration. Values come from defaults in the class, overridden by
the project-root `.env` file or environment variables.

### Constants
- **`BACKEND_DIR`** (L7) — absolute path of `backend/` (`Path(__file__).resolve().parent.parent`).
- **`PROJECT_ROOT`** (L8) — parent of `backend/`; where `.env` lives.

### `class Settings(BaseSettings)` — L11
A pydantic-settings model. Each attribute is a setting with a type and default; pydantic reads a matching
environment variable (case-insensitive) and converts it to the declared type.

`model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")`
— read `.env`, ignore unknown keys.

| Group | Fields |
|---|---|
| Keys | `groq_api_key`, `gemini_api_key`, `tavily_api_key` |
| LLMs | `llm_model`, `fast_llm_model`, `llm_temperature` |
| Embeddings | `embedding_model`, `embedding_dim` |
| Qdrant | `qdrant_url`, `qdrant_api_key`, `qdrant_path`, `collection_name` |
| Ingestion | `upload_dir`, `chunk_size`, `chunk_overlap`, `max_upload_mb` |
| Agent loop | `retrieval_k`, `min_relevance_score`, `max_retrieval_attempts`, `max_web_attempts`, `web_results`, `history_turns` |
| Auth / storage | `database_path`, `jwt_secret`, `jwt_expire_minutes`, `cors_origins` |

(Defaults and meanings: see `DOCUMENTATION.md` §5.)

### `def get_settings() -> Settings` — L60
`@lru_cache`d factory: builds `Settings()` once. **Returns** the shared instance.

### `settings` (module variable)
`settings = get_settings()` — the object every other module imports (`from backend.config.settings import settings`).

---

## 3. `backend/config/llm.py`

**Purpose:** creates the Groq chat-model clients and a helper for schema-constrained ("structured") output.

### `def get_llm() -> ChatGroq` — L10
`@lru_cache`. **Returns** a `ChatGroq` client for `settings.llm_model` (default `openai/gpt-oss-120b`) with
`temperature=settings.llm_temperature` and `max_retries=2`.
**Used by:** `tools/direct_answer.answer`, `tools/rag.generate`, `tools/web_search.generate` — anything that writes
a user-facing answer.

### `def get_fast_llm() -> ChatGroq` — L21
`@lru_cache`. **Returns** a `ChatGroq` client for `settings.fast_llm_model` (default `openai/gpt-oss-20b`),
`temperature=0` (deterministic).
**Used by:** `tools/greeting.respond` and, through `structured()`, all decision steps.

### `def structured(model_cls, fast: bool = True)` — L31
| Param | Meaning |
|---|---|
| `model_cls` | A pydantic class describing the required output (`Route`, `Rewrite`, `DocGrades`, `AnswerEval`). |
| `fast` | `True` → fast model, `False` → main model. |

**Returns** a LangChain runnable: `llm.with_structured_output(model_cls, method="json_schema").with_retry(stop_after_attempt=2)`.
Calling `.invoke(prompt)` on it returns **an instance of `model_cls`** (e.g. `r.intent`), not raw text.

- `method="json_schema"` uses Groq's native JSON-schema mode, which constrains decoding to the schema. It replaced
  tool-calling mode, which intermittently failed on gpt-oss models (`tool_use_failed`).
- `.with_retry(2)` retries once on any exception.

**Used by:** `graph/nodes.router_node`, `graph/nodes.rewrite_node`, `evaluation/evaluator.grade_documents`,
`evaluation/evaluator.evaluate_answer`.

---

## 4. `backend/models/database.py`

**Purpose:** all persistence in SQLite (`backend/data/app.db`): users, documents, conversations, messages, and
dashboard statistics. Every other module talks to the database only through these functions.

### Module-level state
| Name | Line | Meaning |
|---|---|---|
| `_lock` | L12 | `threading.RLock()` — serializes DB access across FastAPI's worker threads. Re-entrant, so the same thread can acquire it again without deadlocking. |
| `_conn` | L13 | The single shared `sqlite3.Connection`, opened lazily. |
| `SCHEMA` | L15 | SQL creating 4 tables and 2 indexes (`IF NOT EXISTS`, so it's safe to run every startup). |

**Tables in `SCHEMA`:**
- `users(id PK, username UNIQUE COLLATE NOCASE, password_hash, created_at)`
- `documents(id PK, user_id FK→users ON DELETE CASCADE, filename, file_hash, size_bytes, pages, chunks, status, error, created_at, UNIQUE(user_id, file_hash))`
- `conversations(id PK, user_id FK→users CASCADE, title, created_at, updated_at)`
- `messages(id PK, conversation_id FK→conversations CASCADE, role, content, tool, meta JSON, created_at)`
- Indexes: `messages(conversation_id, created_at)`, `conversations(user_id, updated_at)`.

### Infrastructure functions

#### `_now() -> str` — L56
**Returns** the current UTC time in ISO-8601 (`2026-09-28T07:53:47.123+00:00`). ISO strings sort chronologically as text.

#### `_get_conn() -> sqlite3.Connection` — L60
Opens the connection on first call and returns the same one afterwards.
1. Creates the parent folder of `database_path`.
2. `sqlite3.connect(..., check_same_thread=False)` — allow use from multiple threads (safety comes from `_lock`).
3. `row_factory = sqlite3.Row` — rows accessible by column name.
4. `PRAGMA foreign_keys = ON` — SQLite does **not** enforce foreign keys/cascades unless enabled.

#### `tx()` — L71 (`@contextmanager`)
Usage: `with tx() as c: c.execute(...)`.
Acquires `_lock`, yields the connection, then `commit()`s; on any exception it `rollback()`s and re-raises.
Guarantees all-or-nothing writes and that only one thread touches the DB at a time.

#### `init_db() -> None` — L83
Runs `SCHEMA` via `executescript`. **Called by:** `app.lifespan` at startup.

#### `_one(cur) -> dict | None` — L88
Fetches one row from a cursor and converts it to a plain `dict`; `None` if there is no row.

#### `_all(cur) -> list[dict]` — L93
Fetches all rows as a list of `dict`s.

### Users

#### `create_user(username, password_hash) -> dict` — L98
Inserts a user with a random `uuid4().hex` id and timestamp. Uses named placeholders (`:id`, …).
**Returns** the user dict (including `password_hash`). Raises `sqlite3.IntegrityError` if the username exists.
**Called by:** `routes/auth.register`.

#### `get_user_by_username(username) -> dict | None` — L109
Case-insensitive lookup (thanks to `COLLATE NOCASE`). **Called by:** `routes/auth.register`, `routes/auth.login`.

#### `get_user(user_id) -> dict | None` — L114
Lookup by id. **Called by:** `api/dependencies.get_current_user` on every authenticated request.

### Documents

#### `add_document(user_id, filename, file_hash, size_bytes) -> dict` — L120
Inserts a document row with `status="processing"`, `pages=0`, `chunks=0`. **Returns** the row dict (with its new `id`).
**Called by:** `routes/documents.upload_document` (before ingestion starts).

#### `_DOC_FIELDS` — L134
Allow-list `{"pages", "chunks", "status", "error"}` of columns `update_document` may change.

#### `update_document(doc_id, **fields) -> None` — L137
Updates any subset of the allowed columns, e.g. `update_document(id, status="indexed", pages=3, chunks=4)`.
1. Drops keys not in `_DOC_FIELDS` (column names are interpolated into SQL, so they must be allow-listed; values use placeholders).
2. Builds `"status = :status, pages = :pages, …"` and executes the `UPDATE`.

#### `find_document_by_hash(user_id, file_hash) -> dict | None` — L146
Returns the user's document with that SHA-256, if any. **Used for** duplicate detection on upload.

#### `list_documents(user_id, status=None) -> list[dict]` — L151
All of the user's documents, newest first; optionally only those with a given `status`.
**Called by:** `routes/documents.list_documents` (all) and `routes/chat._prepare` (`status="indexed"` → document names for the router).

#### `get_document(user_id, doc_id) -> dict | None` — L160
One document, only if it belongs to `user_id`.

#### `delete_document(user_id, doc_id) -> None` — L165
Deletes the row (scoped to the user).

### Conversations & messages

#### `create_conversation(user_id, title) -> dict` — L171
Creates a conversation. Title = `title.strip()[:60]`, or `"New chat"` if empty. **Called by:** `routes/chat._prepare` with the user's first message.

#### `get_conversation(user_id, conv_id) -> dict | None` — L184
Ownership-checked lookup.

#### `list_conversations(user_id) -> list[dict]` — L189
`id, title, created_at, updated_at`, ordered by `updated_at DESC` (most recently active first).

#### `rename_conversation(user_id, conv_id, title) -> None` — L197
Updates the title (max 60 chars). *Not currently used by any route* — ready for a future "rename" feature.

#### `delete_conversation(user_id, conv_id) -> None` — L202
Deletes the conversation; its messages are removed by `ON DELETE CASCADE`.

#### `add_message(conv_id, role, content, tool=None, meta=None) -> dict` — L207
| Param | Meaning |
|---|---|
| `role` | `"user"` or `"assistant"` |
| `tool` | Which tool produced an assistant answer (`rag`, `web_search`, …) |
| `meta` | Dict of extras (sources, trace, evaluation, latency…) stored as JSON text |

In one transaction: inserts the message and sets the conversation's `updated_at` to the message time (moves it
to the top of the sidebar). **Returns** the message dict with `meta` as a dict.

#### `list_messages(conv_id, limit=None) -> list[dict]` — L220
- Without `limit`: all messages oldest→newest.
- With `limit`: the **latest** `limit` messages, still in oldest→newest order (inner query sorts DESC + LIMIT,
  outer query re-sorts ASC).

Parses each `meta` from JSON back to a dict.
**Called by:** `routes/chat.conversation_messages` (full history for the UI) and `routes/chat._prepare` (last
`history_turns` messages as agent memory).

### Statistics

#### `user_stats(user_id) -> dict` — L236
Three queries: indexed document count + chunk sum (`COALESCE(SUM(chunks),0)`), conversation count, and all
assistant messages (JOINed to conversations to scope by user). Then, in Python, it tallies per-tool counts and reads
each `meta` to count rewritten queries, evaluated / sufficient answers, and latencies.

**Returns:**
```python
{"documents", "chunks", "conversations", "queries", "by_tool": {tool: n},
 "queries_rewritten", "answers_evaluated", "answers_sufficient", "avg_latency_ms"}
```
**Called by:** `routes/chat.stats`.

---

## 5. `backend/models/schemas.py`

**Purpose:** pydantic models that define and validate the JSON the API accepts and returns. FastAPI validates
request bodies against them (→ `422` on violation) and serializes responses through them (`response_model=`).

| Class | Line | Fields | Used by |
|---|---|---|---|
| `Credentials` | L8 | `username` (3–32, `^[A-Za-z0-9_.-]+$`), `password` (6–128) | `POST /auth/register`, `POST /auth/login` body |
| `UserOut` | L13 | `id`, `username` | `/auth/me`, inside `TokenOut` |
| `TokenOut` | L18 | `access_token`, `token_type="bearer"`, `user: UserOut` | register/login response |
| `DocumentOut` | L25 | `id`, `filename`, `size_bytes`, `pages`, `chunks`, `status`, `error`, `created_at` | document list, upload response |
| `UploadOut` | L36 | `status` (`success`/`duplicate`), `filename`, `chunks_indexed`, `document` | upload response |
| `ChatRequest` | L44 | `message` (1–4000 chars), `conversation_id` (optional) | `/chat`, `/chat/stream` body |
| `ChatResponse` | L49 | `conversation_id`, `message_id`, `answer`, `tool`, `intent`, `sources`, `rewritten_queries`, `evaluation`, `trace`, `latency_ms` | `/chat` response, SSE `final` payload |
| `ConversationOut` | L62 | `id`, `title`, `created_at`, `updated_at` | `/conversations` |
| `MessageOut` | L69 | `id`, `role`, `content`, `tool`, `meta`, `created_at` | `/conversations/{id}/messages` |

Response models also act as a filter: fields not declared (e.g. `user_id` on documents) are not sent to the client.

---

## 6. `backend/api/security.py`

**Purpose:** password hashing (bcrypt) and access tokens (HS256 JWT implemented with the standard library).

### Passwords

#### `_prehash(password: str) -> bytes` — L16
`base64(sha256(password.encode("utf-8")))` → always 44 bytes.
bcrypt only uses the first 72 bytes of input (and bcrypt 5 raises on longer input); pre-hashing keeps every character
of long passphrases significant and removes NUL bytes.

#### `hash_password(password: str) -> str` — L21
`bcrypt.hashpw(_prehash(password), bcrypt.gensalt())` → e.g. `$2b$12$<salt><hash>`.
Random per-user salt (defeats precomputed tables) and a deliberately slow cost factor (defeats brute force).
The salt and cost are embedded in the output string. **Called by:** `routes/auth.register`.

#### `verify_password(password: str, password_hash: str) -> bool` — L25
`bcrypt.checkpw(_prehash(password), stored_hash)` — re-hashes using the salt stored inside `password_hash` and compares.
Returns `False` (instead of raising) if the stored hash is malformed (`ValueError`). **Called by:** `routes/auth.login`.

### Tokens (JWT)

A token is `base64url(header).base64url(payload).base64url(signature)`, where
`signature = HMAC-SHA256(secret, "header.payload")`. Header and payload are readable by anyone; the signature makes them
tamper-proof.

#### `_load_secret() -> bytes` — L33
Returns the signing key:
1. `settings.jwt_secret` if set in `.env`;
2. otherwise reads `backend/data/.jwt_secret`, creating it first with `secrets.token_urlsafe(64)` (cryptographically
   secure randomness). Persisting it keeps tokens valid across restarts/reloads.

#### `_SECRET` — L44
`_SECRET = _load_secret()` — loaded once at import time.

#### `_b64(data: bytes) -> str` — L47
URL-safe base64 encoding without `=` padding (JWT format).

#### `_unb64(data: str) -> bytes` — L51
Inverse of `_b64`. Re-adds padding: `"=" * (-len(data) % 4)` computes how many `=` are needed to reach a multiple of 4.

#### `create_access_token(user_id: str, username: str) -> str` — L55
1. `header = {"alg": "HS256", "typ": "JWT"}`
2. `payload = {"sub": user_id, "name": username, "iat": now, "exp": now + jwt_expire_minutes*60}` (Unix seconds).
3. `signing_input = b64(header_json) + "." + b64(payload_json)`
4. `sig = hmac.new(_SECRET, signing_input, sha256).digest()`
5. **Returns** `signing_input + "." + b64(sig)`.

**Called by:** `routes/auth._token_for` (after register/login).

#### `decode_access_token(token: str) -> dict | None` — L64
Validates a token and returns its payload, or `None` if anything is wrong:
1. Split into exactly 3 parts (else `ValueError` → `None`).
2. Recompute the HMAC over `header.payload`; compare with the given signature using `hmac.compare_digest`
   (constant time — prevents timing attacks). Mismatch → `None`. The signature is verified **before** trusting any content.
3. Header must say `alg == "HS256"` (blocks `alg: none`-style tricks).
4. `exp` must be in the future (missing `exp` counts as expired).
5. Any decoding/JSON error → `None` (never crashes the request).

**Called by:** `api/dependencies.get_current_user`.

---

## 7. `backend/api/dependencies.py`

**Purpose:** the authentication guard used by every protected endpoint.

#### `_bearer` — L8
`HTTPBearer(auto_error=False)` — extracts `Authorization: Bearer <token>` into an `HTTPAuthorizationCredentials`
(`.scheme`, `.credentials`). With `auto_error=False` it returns `None` when the header is missing, so our code can
answer with a consistent `401` (instead of FastAPI's default `403`).

#### `get_current_user(creds = Depends(_bearer)) -> dict` — L11
Used as `user: dict = Depends(get_current_user)` in route signatures.

1. Prepares one `HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})`.
2. No header or scheme ≠ `bearer` → raise 401.
3. `decode_access_token(creds.credentials)` → `None` → raise 401 (forged / edited / expired / garbage token).
4. `db.get_user(payload["sub"])` → not found → raise 401 (user deleted after token was issued).
5. **Returns** `{"id", "username"}` — deliberately excluding `password_hash`.

Every failure yields the same message so attackers learn nothing about *why* it failed.
**Used by:** all routes except `health`, `auth/register`, `auth/login`.

---

## 8. `backend/api/routes/health.py`

`router = APIRouter(prefix="/health", tags=["Health"])`

#### `health_check()` — L11
`GET /api/health` → `{"status": "healthy", "llm": settings.llm_model, "embeddings": settings.embedding_model, "web_search": "tavily" | "duckduckgo"}`.
No auth. **Used by:** the frontend's "System Online" indicator (`App.jsx`).

---

## 9. `backend/api/routes/auth.py`

`router = APIRouter(prefix="/auth", tags=["Auth"])`

#### `_token_for(user: dict) -> TokenOut` — L13
Builds the login response: a fresh token from `create_access_token(user["id"], user["username"])` plus `UserOut`.

#### `register(body: Credentials)` — L21
`POST /api/auth/register` → `201 TokenOut`.
1. `get_user_by_username` → exists → `409 Username already taken`.
2. `create_user(username, hash_password(password))`; also catches `sqlite3.IntegrityError` for the race where two
   requests register the same name simultaneously.
3. Returns `_token_for(user)` — the user is logged in immediately.

#### `login(body: Credentials)` — L32
`POST /api/auth/login` → `200 TokenOut`.
Looks up the user and verifies the password. Unknown user **or** wrong password → the same `401 Invalid username or password`
(prevents username enumeration).

#### `me(user = Depends(get_current_user))` — L40
`GET /api/auth/me` → `UserOut`. Used by the frontend at page load to restore a saved session.

---

## 10. `backend/api/routes/documents.py`

`router = APIRouter(prefix="/documents", tags=["Documents"])`

#### `_safe_name(name: str) -> str` — L19
Sanitizes a client-supplied filename: `Path(name).name` strips any directory parts (blocks `../../x`), replaces
characters outside `[A-Za-z0-9._ -]` with `_`, truncates to 150 chars, falls back to `"document"`.

#### `_user_dir(user_id: str) -> Path` — L24
Returns (and creates) `UPLOAD_DIR/<user_id>/`.

#### `list_documents(user = Depends(get_current_user))` — L31
`GET /api/documents` → `list[DocumentOut]` via `db.list_documents(user["id"])`.

#### `upload_document(file: UploadFile, user = Depends(get_current_user))` — L36
`POST /api/documents/upload` (multipart) → `UploadOut`. A plain `def`, so FastAPI runs it in a worker thread and slow
embedding does not block the event loop.

1. **Validate type:** extension must be in `SUPPORTED_EXTENSIONS` → else `400`.
2. **Read with a cap:** reads at most `max_upload_mb MB + 1` bytes; more than the limit → `413`; empty → `400`.
3. **Deduplicate:** `file_sha256(data)` → `find_document_by_hash`. Already `indexed` → return `status="duplicate"`.
   A previous non-indexed attempt → its vectors and row are deleted and ingestion restarts.
4. **Register:** `db.add_document(...)` (`processing`), write bytes to `_user_dir/<doc_id><ext>`.
5. **Ingest:** `ingest_file(path, user_id, doc_id, filename)`.
   - `IngestionError` (e.g. no text) → status `failed` + error, `422`.
   - Any other exception → logged, partial vectors deleted, status `failed`, `500`.
6. **Finish:** `update_document(status="indexed", pages, chunks)`; return `status="success"` with the fresh row.

#### `delete_document(doc_id, user = Depends(get_current_user))` — L80
`DELETE /api/documents/{doc_id}` → `204`. `404` if not the user's. Removes vectors
(`delete_document_vectors`), the stored file(s) `<doc_id>.*`, and the DB row.

---

## 11. `backend/api/routes/chat.py`

`router = APIRouter(tags=["Chat"])` — no prefix; paths are declared in full.

#### `_prepare(req: ChatRequest, user: dict) -> (conv, history, doc_names)` — L20
Shared setup for both chat endpoints:
1. If `req.conversation_id` is given → `get_conversation` (404 if not the user's); otherwise `create_conversation(user, req.message)`.
2. `history` = last `settings.history_turns` messages as `[{"role", "content"}]` (loaded **before** saving the new message,
   so the current question isn't duplicated in memory).
3. `doc_names` = filenames of the user's `indexed` documents (fed to the router).
4. Saves the user's message with `add_message(..., "user", ...)`.

#### `_persist(conv: dict, state: dict, started: float) -> dict` — L37
Computes `latency_ms` from `time.perf_counter()`, builds `meta = {intent, sources, rewritten_queries, evaluation, trace, latency_ms}`,
saves the assistant message (`tool=state["tool"]`) and **returns** the `ChatResponse`-shaped dict.

#### `_error_state(e: Exception) -> dict` — L57
Logs the exception with stack trace and returns a fallback state: an apology answer, `tool="direct"`, `intent="error"`, and a
single `error` trace step containing the message. Ensures the user always gets a reply and the conversation stays consistent.

#### `chat(req, user = Depends(get_current_user))` — L69
`POST /api/chat` → `ChatResponse`. `_prepare` → `run_agent(...)` (or `_error_state` on exception) → `_persist`.
Non-streaming; used by `test_rag_pipeline.py` and API clients.

#### `chat_stream(req, user = Depends(get_current_user))` — L80
`POST /api/chat/stream` → `StreamingResponse` with `media_type="text/event-stream"` (Server-Sent Events).
Headers `Cache-Control: no-cache` and `X-Accel-Buffering: no` prevent proxies from buffering the stream.

- **`sse(event, data) -> str`** (inner, L85) — formats one SSE message: `"event: <name>\ndata: <json>\n\n"`.
- **`events()`** (inner generator, L88):
  1. yields `meta` with `conversation_id` and `title` (lets the UI register a new conversation immediately);
  2. iterates `stream_agent(...)`: yields a `step` event for every trace step; remembers the final state;
  3. on exception: builds `_error_state` and yields its step;
  4. yields `final` with `_persist(...)` output.

  `_prepare` runs *before* streaming starts, so auth/404 errors are still normal HTTP errors.

#### `conversations(user)` — L108
`GET /api/conversations` → `list[ConversationOut]`.

#### `conversation_messages(conv_id, user)` — L113
`GET /api/conversations/{conv_id}/messages` → `list[MessageOut]`; `404` if not the user's.

#### `delete_conversation(conv_id, user)` — L120
`DELETE /api/conversations/{conv_id}` → `204`; `404` if not the user's.

#### `stats(user)` — L127
`GET /api/stats` → `db.user_stats(user["id"])`.

---

## 12. `backend/rag/vectorstore.py`

**Purpose:** everything that touches Qdrant or the embedding model.

#### `_lock` — L16
`threading.RLock()` — the embedded (on-disk) Qdrant client isn't safe for concurrent writers; all operations take this lock.

#### `get_embeddings() -> GoogleGenerativeAIEmbeddings` — L20
`@lru_cache`. Gemini embeddings client: `model=settings.embedding_model`, `output_dimensionality=settings.embedding_dim` (768).
**Used by:** `ingestion.ingest_file`, `retrieval.retrieve`.

#### `get_client() -> QdrantClient` — L29
`@lru_cache`. `QdrantClient(url=…)` if `QDRANT_URL` is set, else `QdrantClient(path=settings.qdrant_path)` (embedded).
Calls `_ensure_collection` before returning. **Called by:** `app.lifespan` and every function below.

#### `_ensure_collection(client) -> None` — L38
- Collection missing → create with `VectorParams(size=embedding_dim, distance=COSINE)`.
- Collection exists with a **different vector size** → `RuntimeError` with a clear fix (new `COLLECTION_NAME` or delete the store),
  instead of confusing search errors later.
- On a Qdrant server only: creates keyword payload indexes on `metadata.user_id` and `metadata.doc_id` for fast filtering.

#### `user_filter(user_id, doc_id=None) -> models.Filter` — L58
Qdrant filter `metadata.user_id == user_id` (and `metadata.doc_id == doc_id` if given). **This is the per-user data isolation
for vectors.**

#### `upsert(points: list[PointStruct]) -> None` — L65
Writes points in batches of 128 with `wait=True` (returns only after the write is durable).

#### `search(vector, user_id, limit) -> list[ScoredPoint]` — L72
`client.query_points(collection, query=vector, query_filter=user_filter(user_id), limit=limit, with_payload=True).points`.
Each result has `.id`, `.score` (cosine similarity) and `.payload`.

#### `delete_document_vectors(user_id, doc_id) -> None` — L84
Deletes all points matching `user_filter(user_id, doc_id)`.

#### `count_user_vectors(user_id) -> int` — L94
Exact count of a user's points. *Currently unused* (handy for diagnostics).

---

## 13. `backend/rag/ingestion.py`

**Purpose:** the ingestion pipeline — load → clean → chunk → embed → upsert.

#### `SUPPORTED_EXTENSIONS` — L14
`{".pdf", ".docx", ".txt", ".md"}`.

#### `class IngestionError(Exception)` — L17
Raised for user-facing file problems (unsupported, encrypted, no text). The upload route maps it to `422`.

#### `file_sha256(data: bytes) -> str` — L21
Hex SHA-256 of the file bytes (duplicate detection).

#### `_clean(text: str) -> str` — L25
Normalizes extracted text: removes NUL characters, re-joins words hyphenated across line breaks (`hyph-\nen` → `hyphen`),
collapses runs of spaces/tabs, collapses 3+ newlines to 2, strips.

#### `load_file(path: Path) -> tuple[list[Document], int]` — L33
Returns `(documents, page_count)`:
- **PDF** — `pypdf.PdfReader`; tries `decrypt("")` for encrypted files (raises `IngestionError` if it's truly password-protected);
  one LangChain `Document` per non-empty page with `metadata={"page": i}` (1-based).
- **DOCX** — `python-docx`: all non-empty paragraphs plus table rows (`cell | cell | cell`); one document, `page=1`.
- **TXT/MD** — read as UTF-8 (invalid bytes ignored); one document, `page=1`.
- Other → `IngestionError`.

#### `split_documents(docs) -> list[Document]` — L70
`RecursiveCharacterTextSplitter(chunk_size, chunk_overlap, separators=["\n\n", "\n", ". ", " ", ""], add_start_index=True)`.
Tries to split at paragraphs, then lines, sentences, words. Overlap keeps context that straddles a boundary. Page metadata is
inherited by each chunk; `start_index` records the chunk's character offset. Chunks of ≤20 chars are discarded.

#### `ingest_file(path, *, user_id, doc_id, filename) -> dict` — L80
The pipeline (keyword-only arguments after `*`):
1. `load_file` → empty → `IngestionError("No extractable text found …")`.
2. `split_documents`.
3. `get_embeddings().embed_documents(texts, task_type="RETRIEVAL_DOCUMENT", titles=[filename]*n)` — batch embedding; the task
   type and title improve retrieval quality with Gemini.
4. Builds `PointStruct`s:
   - `id = uuid5(NAMESPACE_URL, f"{doc_id}:{idx}")` — deterministic, so re-ingesting overwrites instead of duplicating;
   - `payload = {"page_content": text, "metadata": {user_id, doc_id, source, page, chunk_index, start_index}}`.
5. `vectorstore.upsert(points)`.
6. **Returns** `{"pages": page_count, "chunks": len(points)}`.

**Called by:** `routes/documents.upload_document`.

---

## 14. `backend/rag/retrieval.py`

#### `retrieve(queries, user_id, k=None, min_score=None) -> list[dict]` — L8
Multi-query dense retrieval.

| Param | Default | Meaning |
|---|---|---|
| `queries` | — | One or more query strings (original + rewrites/expansions). |
| `user_id` | — | Scope; only this user's chunks are searched. |
| `k` | `settings.retrieval_k` (6) | Neighbours per query. |
| `min_score` | `settings.min_relevance_score` (0.45) | Cosine floor. |

Steps:
1. Strip and deduplicate queries preserving order (`dict.fromkeys`). None left → `[]`.
2. Embed all queries **in parallel** (`ThreadPoolExecutor`, up to 4 workers) with `task_type="RETRIEVAL_QUERY"`.
3. For each vector: `vectorstore.search(vector, user_id, limit=k)`; drop hits below `min_score`.
4. Merge by point id, keeping the highest score and recording `matched_query`.
5. Sort by score, return the top `k + 2` (extra candidates for the LLM grader to discard).

**Each hit:** `{"id", "content", "source", "page", "doc_id", "chunk_index", "score", "matched_query"}`.
**Called by:** `graph/nodes.retrieve_node`.

---

## 15. `backend/tools/__init__.py`

**Purpose:** helpers shared by all tools.

#### `history_to_messages(history) -> list[BaseMessage]` — L7
Converts `[{"role": "user"|"assistant", "content"}]` into LangChain `HumanMessage` / `AIMessage` objects so the chat model sees
the prior conversation as real turns.

#### `history_to_text(history, max_chars=600) -> str` — L17
Renders history as plain text (`USER: …` / `ASSISTANT: …`), each message truncated to `max_chars`; `"(no prior conversation)"`
if empty. Used inside router/rewrite prompts.

#### `today() -> str` — L23
Today's date, e.g. `"September 28, 2026"`. Injected into prompts so the model can judge recency.

#### `_CITE` — L27
Regex `【(\d+)(?:†[^】]*)?】` matching gpt-oss's native citation markers such as `【1†L1-L3】`.

#### `clean_answer(text) -> str` — L30
Replaces those markers with `[1]` and strips whitespace. Applied to every generated answer.

#### `FORMAT_RULES` — L35
Instruction appended to answer prompts: GitHub-flavored Markdown, no HTML, cite as `[n]`.

---

## 16. `backend/tools/greeting.py`

#### `SYSTEM` — L7
Prompt: friendly 1–3 sentence reply; may mention the three capabilities; includes `{username}` and `{doc_count}`.

#### `respond(query, history, username, doc_count) -> str` — L14
Messages = `[SystemMessage(SYSTEM formatted), last 4 history turns, HumanMessage(query)]` → `get_fast_llm().invoke(...)`.
**Returns** the reply text. **Called by:** `nodes.greeting_node`.

---

## 17. `backend/tools/direct_answer.py`

#### `NEEDS_WEB` — L7
Sentinel string `"NEEDS_WEB_SEARCH"`.

#### `SYSTEM` — L9
Prompt: answer from training knowledge, include today's date; for recent/time-sensitive/unknown information reply **exactly**
the sentinel; never invent facts.

#### `answer(query, history) -> tuple[str, bool]` — L21
Calls the main LLM with system prompt + history + question and cleans citations.
**Returns** `("", True)` if the sentinel appears (→ escalate to web search), otherwise `(answer_text, False)`.
**Called by:** `nodes.direct_node`.

---

## 18. `backend/tools/rag.py`

#### `NO_ANSWER` — L7
Sentinel `"NO_ANSWER_IN_DOCUMENTS"`.

#### `SYSTEM` — L9
Strict grounding prompt: use only the excerpts, cite `[n]`, state what isn't covered for partial answers, reply exactly the
sentinel when nothing answers the question, never use outside knowledge. (Placeholders filled with `.replace`.)

#### `format_context(docs) -> str` — L22
Numbers chunks for the prompt:
```
[1] (source: report.pdf, page 3)
<chunk text>
```
Also used by `nodes.evaluate_node` to give the evaluator the same context.

#### `generate(query, docs, history) -> str | None` — L30
Main LLM with the system prompt, history, and "Document excerpts … Question: …". Cleans citations.
**Returns** `None` if the output contains `NO_ANSWER`, else the answer. **Called by:** `nodes.generate_rag_node`.

#### `to_sources(docs) -> list[dict]` — L38
UI source cards: `{"type": "document", "title", "document", "page", "score", "snippet" (first 280 chars)}`.
Their order matches the `[n]` numbers in the answer.

---

## 19. `backend/tools/web_search.py`

#### Constants
- `_HEADERS` (L16) — browser-like `User-Agent`/`Accept-Language` so sites and DuckDuckGo serve normal pages.
- `_PAGE_CHARS` (L21) — 2500: max characters kept per result.

#### `_tavily(query, n) -> list[dict]` — L25
Tavily API (`search_depth="advanced"`). **Returns** `[{"title", "url", "content"}]`. Only used when `TAVILY_API_KEY` is set.

#### `_unwrap_ddg(href) -> str` — L37
DuckDuckGo wraps links as `//duckduckgo.com/l/?uddg=<encoded real URL>`; this adds the scheme and decodes the real URL.

#### `_fetch_text(url) -> str` — L46
Downloads a page (8 s timeout, follows redirects), requires `200` + HTML, parses with `lxml`, removes
`script/style/nav/footer/header/aside/form/noscript`, keeps `p/li/h1-h3` texts longer than 40 chars, truncates to `_PAGE_CHARS`.
Returns `""` on any failure (one bad site never breaks the search).

#### `_duckduckgo(query, n) -> list[dict]` — L61
POSTs to `html.duckduckgo.com/html/`, parses result blocks with XPath (`result__body`, `result__a`, `result__snippet`), skips ads
and non-HTTP links, collects up to `n`. Then fetches the top 4 pages **in parallel** and appends their text to the snippets.
**Returns** `[{"title", "url", "content"}]`.

#### `search(query, n=None) -> list[dict]` — L91
Public entry: Tavily first (if configured; logs and falls back on error), then DuckDuckGo; logs and returns `[]` if everything fails.
**Called by:** `nodes.web_search_node`.

#### `SYSTEM` — L106
Prompt for web answers: base the answer on results, cite `[n]`, prefer recent/authoritative sources, flag conflicts, say so if the
results don't answer.

#### `generate(query, results, history) -> str` — L117
Formats results as `[i] title (url)\ncontent`, calls the main LLM, cleans citations. **Called by:** `nodes.generate_web_node`.

#### `to_sources(results) -> list[dict]` — L129
`{"type": "web", "title", "url", "snippet"}` per result.

---

## 20. `backend/evaluation/evaluator.py`

**Purpose:** LLM-as-judge steps inside the agent loop.

#### `class DocGrades(BaseModel)` — L12
`relevant_ids: list[int]` — 1-based numbers of relevant excerpts.

#### `GRADE_PROMPT` — L18
Asks the judge to mark excerpts that genuinely help answer the question ("keyword overlap alone is not enough").

#### `grade_documents(query, docs) -> list[dict]` — L30
Numbers the candidate chunks (first 900 chars each), invokes `structured(DocGrades)`, keeps chunks whose number is returned
(out-of-range numbers ignored). **Fail-open:** on error, logs and returns all docs. **Called by:** `nodes.grade_node`.

#### `class AnswerEval(BaseModel)` — L45
`sufficient: bool`, `grounded: bool`, `score: int (1–5)`, `missing: str`.

#### `EVAL_PROMPT` — L52
Strict evaluator: an answer that is vague, partial or says "not available" is **not** sufficient; `grounded` means every claim
is supported by the given context.

#### `evaluate_answer(query, answer, context=None) -> dict` — L67
Includes up to 6000 chars of context when provided (none for direct answers → grounded is trivially true).
**Returns** the `AnswerEval` as a dict. **Fail-safe:** on error returns an accepting verdict (`score 3`) plus `"error"`, so the loop
can't spin on a broken judge. **Called by:** `nodes.evaluate_node`.

---

## 21. `backend/graph/state.py`

#### `class AgentState(TypedDict, total=False)` — L5
The shared state ("backpack") passed between graph nodes. `total=False` → every key is optional.

| Group | Keys |
|---|---|
| Inputs | `query`, `user_id`, `username`, `history`, `doc_names` |
| Routing | `intent`, `standalone_query`, `query_is_clear`, `route_reason` |
| Query loop | `search_queries`, `rewritten_queries`*, `rewrite_reason`, `attempts` |
| Evidence | `documents`, `web_results` |
| Output | `answer`, `tool`, `sources`, `evaluation`, `best`, `trace`* |

\* Declared as `Annotated[list, operator.add]`: LangGraph **appends** node updates to these lists instead of replacing them.
All other keys are overwritten by the latest node that returns them.

---

## 22. `backend/graph/nodes.py`

**Purpose:** one function per step of the agent. Each takes `state: AgentState` and returns a dict of updated keys, always
including a `trace` list.

#### `_step(node, detail, **data) -> dict` — L22
Builds a trace entry `{"node", "detail", "data"}` — what the UI shows under **Agent steps** and streams live.

### Router

#### `class Route(BaseModel)` — L27
`intent: Literal["greeting","rag","web_search","direct"]`, `reasoning: str`, `query_is_clear: bool`, `standalone_query: str`.
Field descriptions are sent to the model as instructions.

#### `ROUTER_PROMPT` — L40
Defines the four intents, tie-breakers (prefer `rag` when the user has relevant documents; vague messages with documents → `rag`
+ unclear), and injects today's date, document names, recent conversation and the message.

#### `router_node(state) -> dict` — L67
Formats the prompt (up to 40 document names, last 6 history turns) → `structured(Route).invoke`.
On error: `intent = "rag" if documents else "direct"`, query treated as clear.
**Returns** `intent`, `query_is_clear`, `standalone_query`, `route_reason`, `search_queries=[standalone]`, `attempts=0`,
`rewrite_reason` (`"vague_query"` if unclear), and the trace step.

### Simple answerers

#### `greeting_node(state) -> dict` — L101
`greeting.respond(...)` → `answer`, `tool="greeting"`, empty `sources`, `evaluation=None`.

#### `direct_node(state) -> dict` — L109
`direct_answer.answer(standalone_query, history)`.
- Needs web → switches `intent="web_search"`, resets `attempts`, sets `search_queries`; trace "escalating to web search".
- Else → `answer`, `tool="direct"`.

### Query rewriting

#### `class Rewrite(BaseModel)` — L125
`rewritten_query: str`, `expansions: list[str]` (2–3 alternatives), `strategy: str`.

#### `REWRITE_PROMPT` — L134
Inputs: target (web search engine vs. semantic document search), original and standalone question, history, document names
(RAG only), reason, evaluator feedback, **all previously tried queries** (must not be repeated), attempt number and a strategy
guide (attempt 1: clarify/terminology; attempt 2+: step-back + decomposition).

#### `_REASONS` — L150
Maps `rewrite_reason` codes (`vague_query`, `no_relevant_docs`, `insufficient_answer`, `no_web_results`) to explanatory sentences.

#### `rewrite_node(state) -> dict` — L158
Builds the prompt (including evaluator `missing` feedback when present) → `structured(Rewrite)`. On error reuses the standalone
query. **Returns** `search_queries` (`[rewritten, *expansions]` for RAG — up to 4; `[rewritten, first expansion]` for web),
`rewritten_queries=[rewritten]` (appended), trace.

### RAG path

#### `retrieve_node(state) -> dict` — L199
`retrieve(search_queries, user_id)`; increments `attempts`. Returns `documents` (candidates) and a trace with queries, candidate
count and top score.

#### `grade_node(state) -> dict` — L214
`grade_documents(standalone_query, candidates)` → `documents` = relevant subset. If empty, sets `rewrite_reason="no_relevant_docs"`.

#### `generate_rag_node(state) -> dict` — L228
`rag.generate(...)`. If it returns `None` (chunks don't answer): clears `answer`/`documents`, sets `rewrite_reason="no_relevant_docs"`.
Else returns `answer`, `tool="rag"`, `sources`.

### Web path

#### `web_search_node(state) -> dict` — L239
Runs `web_search.search` for up to 2 queries, deduplicates by URL, keeps at most `web_results + 2`, increments `attempts`.
No results → `rewrite_reason="no_web_results"`.

#### `generate_web_node(state) -> dict` — L260
`web_search.generate(...)` → `answer`, `tool="web_search"`, `sources`.

### Quality control & endings

#### `evaluate_node(state) -> dict` — L268
1. Builds context: RAG → `format_context(documents)`; web → numbered page texts (1200 chars each); direct → none.
2. `evaluate_answer(...)`.
3. Keeps `best` = the highest-scoring candidate so far (`answer`, `tool`, `sources`, `evaluation`).
4. If not (`sufficient` and `grounded`): `rewrite_reason="insufficient_answer"`; if the tool was `direct`, escalates:
   `intent="web_search"`, `attempts=0`, `search_queries=[standalone]` plus an `escalate` trace step.

#### `no_results_node(state) -> dict` — L300
Chooses the message: web → "couldn't find results…"; no documents → "You haven't uploaded any documents yet…"; otherwise
"No matches found in your documents… I searched with N different phrasing(s)…". Sets `tool="no_results"`.

#### `finalize_node(state) -> dict` — L316
- Last evaluation passed (or none) → keep the answer as is.
- Otherwise (retry budget exhausted): return `best` (or the current answer) and append a caveat note for `rag`/`web_search`.

---

## 23. `backend/graph/workflow.py`

**Purpose:** wires nodes into a LangGraph state machine and exposes the run/stream entry points. The module docstring contains an
ASCII diagram of the graph.

### Routing functions (conditional edges)
Each takes the state and returns the **name of the next node**.

| Function | Line | Returns |
|---|---|---|
| `route_after_router` | L29 | `greeting`, `direct`, `no_results` (rag with no documents), `rewrite` (unclear), `retrieve` (rag) or `web_search` |
| `route_after_direct` | L42 | `web_search` if escalated, else `evaluate` |
| `route_after_rewrite` | L46 | `web_search` or `retrieve` by intent |
| `route_after_grade` | L50 | `generate_rag` if chunks; `rewrite` if `attempts < max_retrieval_attempts`; else `no_results` |
| `route_after_generate_rag` | L58 | `evaluate` if an answer exists; else `rewrite` / `no_results` by budget |
| `route_after_web` | L64 | `generate_web` if results; else `rewrite` / `no_results` by `max_web_attempts` |
| `route_after_evaluate` | L72 | `finalize` if sufficient & grounded; `web_search` for an escalated direct answer; `rewrite` for weak rag/web answers within budget; else `finalize` |

### `build_graph()` — L85
Creates `StateGraph(AgentState)`, registers the 12 nodes, then edges:
`START→router`, conditional edges using the functions above, fixed edges `greeting→END`, `retrieve→grade`,
`generate_web→evaluate`, `no_results→END`, `finalize→END`. **Returns** the compiled graph.

### `get_graph()` — L118
`@lru_cache` — compile once, reuse.

### `_initial_state(query, user, history, doc_names) -> AgentState` — L122
Seeds the state: inputs plus empty `rewritten_queries`/`trace`, `best=None`, `evaluation=None`.

### `_RECURSION_LIMIT = 40` — L137
Maximum graph steps per run (a safety net; the worst legitimate path is ~20 steps).

### `run_agent(query, user, history, doc_names) -> AgentState` — L140
`get_graph().invoke(initial_state, {"recursion_limit": 40})` → final state. **Called by:** `routes/chat.chat`.

### `stream_agent(query, user, history, doc_names) -> Iterator[(kind, payload)]` — L145
Runs `graph.stream(..., stream_mode="updates")`, which yields each node's update as soon as the node finishes.
- For each trace step in the update → `yield ("step", step)`.
- Merges the update into a local copy of the state (appending for `trace` and `rewritten_queries`, replacing otherwise).
- Finally `yield ("final", state)`.

**Called by:** `routes/chat.chat_stream`.

---

## 24. Package markers & data folders

| Path | Purpose |
|---|---|
| `backend/__init__.py` and every `*/__init__.py` | Empty files marking directories as Python packages so `backend.x.y` imports resolve. `tools/__init__.py` is the only one with code (§15). |
| `backend/data/` | Runtime: `app.db` (SQLite) and `.jwt_secret`. Created automatically; git-ignored. |
| `backend/documents/<user_id>/` | Runtime: uploaded originals saved as `<doc_id><ext>`. |
| `backend/vector_db/qdrant_data/` | Runtime: embedded Qdrant storage (single-process). |
| `backend/__pycache__/` | Python bytecode cache; safe to delete. |

---

# Frontend

## 25. Frontend build & config files

| File | Purpose |
|---|---|
| `index.html` | HTML shell with `<div id="root">` and the `/src/main.jsx` module script; dark body classes. |
| `package.json` | Dependencies (`react`, `react-dom`, `lucide-react`, `react-markdown`, `remark-gfm`) and dev tools (`vite`, `@vitejs/plugin-react`, `tailwindcss`, `postcss`, `autoprefixer`); scripts `dev`, `build`, `preview`. |
| `package-lock.json` | Exact resolved dependency versions for reproducible installs. |
| `vite.config.js` | React plugin; dev server on port 5173; **proxy** `/api` → `http://127.0.0.1:8000` so the browser makes same-origin requests (no CORS problems, any dev port works). |
| `tailwind.config.js` | Files Tailwind scans for class names (`index.html`, `src/**/*.{js,jsx,ts,tsx}`). |
| `postcss.config.js` | Runs Tailwind and Autoprefixer when CSS is built. |
| `frontend/**/__init__.py` | Leftover empty Python files; unused and safe to delete. |

---

## 26. `src/main.jsx` & `src/index.css`

**`main.jsx`** — mounts `<App />` into `#root` inside `React.StrictMode` (which double-invokes effects in development to surface
bugs) and imports `index.css`.

**`index.css`** — Tailwind's `base`/`components`/`utilities` layers plus a `.markdown` component style (headings, lists, links,
inline/fenced code, blockquotes, tables) used for rendered agent answers.

---

## 27. `src/services/api.jsx`

**Purpose:** the only place the frontend talks to the backend.

#### Constants
- `API_BASE_URL` — `import.meta.env.VITE_API_URL || '/api'`.
- `TOKEN_KEY` — `'agentic_rag_token'` (localStorage key).

#### `auth` object
| Method | Behaviour |
|---|---|
| `getToken()` | Reads the token from `localStorage`; `null` if storage is unavailable. |
| `setToken(token)` | Saves it (errors ignored). |
| `clear()` | Removes it. |

#### `setUnauthorizedHandler(fn)`
Registers a callback invoked when a request with a token gets `401`. `App.jsx` passes `() => setUser(null)` (→ login screen).

#### `request(path, { method, body, headers, raw })`
Core HTTP helper:
1. Adds `Authorization: Bearer <token>` when logged in.
2. Sends `FormData` as-is (browser sets the multipart boundary) or JSON with `Content-Type: application/json`.
3. On `401` with a token → clears it and calls the unauthorized handler.
4. On non-2xx → throws `Error(detail)` using the backend's `detail` (string, or joined validation messages).
5. Returns the raw `Response` if `raw`, `null` for `204`, otherwise parsed JSON.

#### `apiService` methods
| Method | Endpoint | Notes |
|---|---|---|
| `checkHealth()` | `GET /health` | |
| `login(u, p)` / `register(u, p)` | `POST /auth/login` / `/auth/register` | Store the returned token; resolve to the user. |
| `me()` | `GET /auth/me` | Session restore. |
| `logout()` | — | Clears the token. |
| `sendMessage(message, conversationId)` | `POST /chat` | Non-streaming (kept for completeness). |
| `streamMessage(message, conversationId, {onMeta, onStep, onFinal})` | `POST /chat/stream` | See below. |
| `getConversations()` / `getMessages(id)` / `deleteConversation(id)` | conversation endpoints | |
| `getDocuments()` / `uploadDocument(file)` / `deleteDocument(id)` | document endpoints | Upload builds `FormData` with field `file`. |
| `getStats()` | `GET /stats` | |

#### `streamMessage` in detail
Uses `request(..., { raw: true })`, then reads `res.body.getReader()` with a `TextDecoder`. Incoming bytes are appended to a
`buffer`; every complete event (terminated by a blank line `\n\n`) is cut off and passed to the inner **`dispatch(chunk)`**, which
parses the `event:` and `data:` lines, `JSON.parse`s the data and calls `onMeta` / `onStep` / `onFinal`. Leftover text at the end
is dispatched too.

---

## 28. `src/App.jsx`

### `LoginScreen({ onAuthenticated })`
State: `mode` (`'login'|'register'`), `username`, `password`, `error`, `busy`.
- **`handleSubmit(e)`** — prevents the page reload, calls `apiService.login` or `register` (trimmed username), passes the user to
  `onAuthenticated`; maps the browser's `"Failed to fetch"` to "Cannot reach the backend server."
- Renders the form (HTML `minLength`/`maxLength` mirror backend rules), an error banner, a spinner while `busy`, and a toggle link
  between sign-in and sign-up.

### `App()` (default export)
State: `user`, `checkingSession` (true if a token exists at load), `activePage` (`chat|documents|dashboard`), `conversations`,
`activeConversationId`, `systemOnline`.

| Piece | Behaviour |
|---|---|
| Mount effect | Registers the 401 handler; `checkHealth()` → `systemOnline`; if a token exists, `me()` restores `user` (or clears a bad token); ends `checkingSession`. |
| `refreshConversations()` | `useCallback`; loads the sidebar list. |
| Effect on `user` | Loads conversations after login. |
| `handleLogout()` | Clears token and all state, returns to chat page. |
| `handleDeleteConversation(id)` | Deletes; if it was open, switches to a new chat; refreshes the list. |
| Render | Spinner while checking the session → `LoginScreen` if no user → layout: `Sidebar`, header (active title + online dot), and the active page. `Chat` receives callbacks to register a newly created conversation and refresh ordering. |

---

## 29. `src/pages/Chat.jsx`

#### `fromApi(m)`
Maps a stored message (`MessageOut`) to the UI shape: `{id, sender: 'user'|'agent', text, tool, sources, trace, rewrittenQueries,
evaluation, latencyMs}`.

#### `Chat({ conversationId, onConversationCreated, onMessageSent, onGoToDocuments })`
State: `messages`, `loading` (agent running), `loadingHistory`. Ref: `createdHereRef`.

| Piece | Behaviour |
|---|---|
| Effect on `conversationId` | `null` → empty chat. If the id equals `createdHereRef.current` (just created by this page mid-stream) → do nothing, to avoid wiping live messages. Otherwise fetch messages; a `cancelled` flag ignores late responses after switching chats. |
| `updatePending(fn)` | Updates only the message flagged `pending: true`. |
| `handleSend(text)` | Appends the user bubble and a pending agent bubble (`steps: []`), then `streamMessage`: **onMeta** → for new chats record the id and call `onConversationCreated`; **onStep** → append the step to the pending bubble; **onFinal** → replace it with the final answer. Errors turn the pending bubble into an error bubble. Finally refreshes the sidebar and clears `loading`. |
| Render | `ChatWindow` + `InputBox` (disabled while loading). |

---

## 30. `src/pages/Documents.jsx`

Constants: `ACCEPTED` extensions, `MAX_MB = 20`, `formatSize(bytes)` (KB/MB text), `STATUS_STYLES` (badge colours).

State: `documents`, `loadingList`, `uploading` (current filename), `uploadMessage`, `dragging`; ref `fileInputRef`.

| Function | Behaviour |
|---|---|
| `loadDocuments()` | Fetches the list; shows errors in the banner. Called on mount and after uploads. |
| `uploadOne(file)` | Client-side type and size checks, then `uploadDocument`; returns a `{type, text}` message (success / duplicate / error). |
| `handleFiles(fileList)` | Uploads files **sequentially**, resets the file input, shows a combined result message, reloads the list. |
| `handleDelete(doc)` | `window.confirm`, then `deleteDocument` and removes the row locally. |
| Render | Hidden file input; drag-and-drop zone (`onDragOver` must `preventDefault()` for drop to work); status banner; list with chunks/pages/size/error, status badge and delete button. |

---

## 31. `src/pages/Dashboard.jsx`

- `TOOL_META` — label and bar colour per tool.
- **`Stat({ label, value, accent, hint })`** — one metric tile.
- **`Dashboard()`** — fetches `/stats` on mount; shows error or spinner; otherwise:
  - **`pct(n, d)`** — percentage string, `'—'` when `d` is 0;
  - **RAG hit rate** = `rag / (rag + no_results)`;
  - tiles: total queries, RAG hit rate, active documents (+chunks), average latency, queries rewritten, answers judged sufficient;
  - routing breakdown bars sorted by count, width = share of all queries.

---

## 32. `src/components/Sidebar.jsx`

Props: `user`, `activePage`, `setActivePage`, `conversations`, `activeConversationId`, `onSelectConversation`, `onNewChat`,
`onDeleteConversation`, `onLogout`.

- **`navClass(active)`** — returns highlighted vs. normal Tailwind classes.
- Renders brand, **New Chat**, a scrollable conversation list (trash icon appears on hover via `group-hover`), Documents and
  Dashboard navigation, username and **Log Out**.

---

## 33. `src/components/ChatWindow.jsx`

- `SUGGESTIONS` — four starter prompts (one per tool) with icons.
- **`ChatWindow({ messages, loadingHistory, onSuggestion, onGoToDocuments })`**:
  - `bottomRef` + effect on `messages` → smooth-scroll to the newest message;
  - `loadingHistory` → spinner; no messages → welcome text, suggestion buttons (send immediately) and a link to Documents;
  - otherwise a list of `<Message>` keyed by message id.

---

## 34. `src/components/InputBox.jsx`

**`InputBox({ onSend, disabled })`** — controlled text input (`text` state).
**`handleSubmit(e)`** — prevents reload, ignores empty/whitespace or disabled, calls `onSend(text)`, clears the box.
The send button is disabled when empty or while the agent is running.

---

## 35. `src/components/Message.jsx`

- `NODE_LABELS` — human-readable names for trace nodes (`grade_documents` → "Grade chunks", …).
- **`TraceList({ steps, live })`** — ordered list of `label — detail`; when `live`, adds a spinner row ("Routing your question…" /
  "Working…").
- **`Markdown({ text })`** — `ReactMarkdown` with `remark-gfm` (tables, strikethrough, autolinks); links open in a new tab with
  `rel="noopener noreferrer"`; wrapped in `.markdown` styles.
- **`Message({ msg })`** — one chat bubble:
  - user → indigo, right-aligned plain text; error → red plain text; pending → live `TraceList`; answer → `Markdown`;
  - below answers: **"Query rewritten as …"** (if any), `SourceCard`, footer with `ToolBadge`, quality score chip
    (green if sufficient, amber otherwise; tooltip shows `missing`), latency, and an **Agent steps (n)** toggle (`showTrace`) that
    reveals the full `TraceList`.

---

## 36. `src/components/SourceCard.jsx`

**`SourceCard({ sources })`** — returns nothing if there are no sources. Header toggles `open` and shows the count with a globe
(web) or file icon. Each card shows `[n]`, then either a clickable title (web) or filename (document), page, and score; when `open`,
the snippet (clamped to 4 lines).

---

## 37. `src/components/ToolBadge.jsx`

**`ToolBadge({ tool })`** — looks up `{label, icon, color}` for `rag`, `web_search`, `direct`, `greeting`, `no_results`; unknown
values fall back to "Direct LLM". Renders a small pill.

---

# Project root

## 38. Root files

| File | Purpose |
|---|---|
| `.env` | Secrets and overrides (`GROQ_API_KEY`, `GEMINI_API_KEY`, optional `TAVILY_API_KEY`, `JWT_SECRET`, `QDRANT_URL`, any setting). Never commit it. |
| `.gitignore` | Excludes `.env`, `.venv/`, caches, runtime data (`backend/data/`, `backend/vector_db/qdrant_data/`, uploads), `node_modules/`, `dist/`. |
| `README.md` | Quick start and overview. |
| `requirements.txt` | Pinned Python dependencies (FastAPI, LangChain/LangGraph, Groq, Gemini, Qdrant, pypdf, python-docx, bcrypt, …). |
| `test_rag_pipeline.py` | End-to-end smoke test (below). |
| `sample.pdf` | Default input for the smoke test. |
| `.venv/` | Local Python virtual environment. |
| `docs/` | `DOCUMENTATION.md` (system docs) and this `CODE_REFERENCE.md`. |

### `test_rag_pipeline.py`
- `BASE_URL` — `http://localhost:8000/api`.
- `PDF_PATH` — first CLI argument or `./sample.pdf`.
- `QUERIES` — `(label, message)` pairs covering greeting, RAG, vague RAG, not-in-documents, direct and web search.
- **`test_pipeline()`** — registers a random `tester_xxxxxx` user; uploads the file (asserts 200); sends every query in a single
  conversation via `POST /chat` and prints tool, intent, latency, every trace step, the first 300 characters of the answer and the
  sources. Run with `python test_rag_pipeline.py [file]` while the backend is running.

---

## Appendix A — Call graph of a chat request

```
Chat.handleSend
 └─ apiService.streamMessage ─ POST /api/chat/stream
     └─ get_current_user ─ decode_access_token, db.get_user
     └─ chat_stream
         ├─ _prepare ─ db.get_conversation | db.create_conversation, db.list_messages, db.list_documents, db.add_message
         ├─ stream_agent ─ get_graph ─ build_graph
         │   ├─ router_node ─ structured(Route)
         │   ├─ [rewrite_node ─ structured(Rewrite)]
         │   ├─ retrieve_node ─ retrieve ─ get_embeddings.embed_query, vectorstore.search
         │   ├─ grade_node ─ grade_documents ─ structured(DocGrades)
         │   ├─ generate_rag_node ─ rag.generate ─ get_llm, clean_answer
         │   ├─ evaluate_node ─ evaluate_answer ─ structured(AnswerEval)
         │   └─ finalize_node
         │   (or greeting_node ─ greeting.respond | direct_node ─ direct_answer.answer |
         │    web_search_node ─ web_search.search, generate_web_node ─ web_search.generate | no_results_node)
         └─ _persist ─ db.add_message
 ◄─ SSE: meta → step… → final ─ onMeta / onStep / onFinal ─ Message renders
```

## Appendix B — Call graph of a document upload

```
Documents.handleFiles ─ uploadOne ─ apiService.uploadDocument ─ POST /api/documents/upload
 └─ get_current_user
 └─ upload_document
     ├─ _safe_name, file_sha256, db.find_document_by_hash
     ├─ db.add_document, _user_dir (write file)
     ├─ ingest_file
     │   ├─ load_file ─ _clean
     │   ├─ split_documents
     │   ├─ get_embeddings().embed_documents
     │   └─ vectorstore.upsert ─ get_client ─ _ensure_collection
     └─ db.update_document (indexed | failed)
 ◄─ UploadOut ─ Documents.loadDocuments refreshes the list
```
