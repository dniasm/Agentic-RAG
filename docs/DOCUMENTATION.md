# Agentic RAG — Project Documentation

> Full technical documentation. For a quick start, see [`README.md`](../README.md).

## Table of contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Tech stack](#3-tech-stack)
4. [Getting started](#4-getting-started)
5. [Configuration reference](#5-configuration-reference)
6. [Project structure](#6-project-structure)
7. [The agent workflow](#7-the-agent-workflow)
8. [Ingestion pipeline](#8-ingestion-pipeline)
9. [Retrieval](#9-retrieval)
10. [Backend module reference](#10-backend-module-reference)
11. [Authentication & security](#11-authentication--security)
12. [Data model](#12-data-model)
13. [HTTP API reference](#13-http-api-reference)
14. [Frontend](#14-frontend)
15. [Testing](#15-testing)
16. [Troubleshooting](#16-troubleshooting)
17. [Extending the system](#17-extending-the-system)
18. [Known limitations](#18-known-limitations)

---

## 1. Overview

Agentic RAG is a full-stack question-answering application. A user signs in, uploads documents into a
private vector store, and chats with an **agent** that decides, per message, how to answer:

| Intent | What happens |
|---|---|
| **greeting** | Small talk — a short friendly reply. |
| **rag** | Answer strictly from the user's uploaded documents, with citations. |
| **web_search** | Search the internet for current / post-cutoff information and answer with links. |
| **direct** | Answer from the model's own knowledge (escalates to web search if it can't). |

What makes it *agentic* rather than a fixed pipeline:

- **Query rewriting before search** — vague or context-dependent questions are rewritten and expanded
  into several search queries before retrieval.
- **Self-correction loops** — retrieved chunks are graded for relevance, and generated answers are
  evaluated for sufficiency and groundedness. Failures trigger another rewrite → search cycle with
  a different strategy, up to a retry budget.
- **Honest failure** — if nothing relevant is found after all retries, the user is told
  *"No matches found"* instead of receiving a hallucinated answer.
- **Transparency** — every decision is recorded as a trace step and streamed live to the UI.

---

## 2. Architecture

```
┌──────────────────────── Browser (React, Vite :5173) ─────────────────────────┐
│  Login ─ Chat (SSE stream) ─ Documents (upload) ─ Dashboard (stats)          │
└───────────────┬──────────────────────────────────────────────────────────────┘
                │  /api/*  (Vite dev proxy → 127.0.0.1:8000, Bearer JWT)
┌───────────────▼──────────────────── FastAPI (:8000) ─────────────────────────┐
│  routes/auth ─ routes/chat ─ routes/documents ─ routes/health                │
│        │               │                    │                                │
│  security.py     graph/workflow.py     rag/ingestion.py                      │
│  (bcrypt, JWT)   (LangGraph agent)     (load → chunk → embed)                │
│        │               │   │   │                │                            │
│        ▼               │   │   └── tools/web_search ──► Tavily / DuckDuckGo  │
│   SQLite (app.db)      │   └────── rag/retrieval ──┐                         │
│   users, documents,    │                           ▼                         │
│   conversations,       │                  Qdrant (per-user filter)           │
│   messages             │                           ▲                         │
│                        ├── Groq LLMs (gpt-oss-120b / 20b)                    │
│                        └── Gemini embeddings (gemini-embedding-001, 768-d) ──┘
└──────────────────────────────────────────────────────────────────────────────┘
```

**Request lifecycle (chat):**

1. The frontend POSTs the message to `/api/chat/stream` with the user's JWT.
2. `get_current_user` validates the token; `_prepare` loads/creates the conversation, the last
   6 messages (short-term memory) and the user's indexed document names, and saves the user message.
3. `stream_agent` runs the LangGraph workflow. Each finished node's trace step is sent as an SSE `step` event.
4. The final state is persisted as an assistant message (with sources, trace, evaluation, latency in `meta`)
   and sent as the SSE `final` event.

---

## 3. Tech stack

| Layer | Technology | Notes |
|---|---|---|
| Frontend | React 18, Vite 5, Tailwind 3, lucide-react, react-markdown + remark-gfm | Single-page app, no router library |
| API | FastAPI, Uvicorn, Starlette `StreamingResponse` (SSE) | Sync routes run in a threadpool |
| Agent | LangGraph `StateGraph` | Conditional edges implement the loops |
| LLMs | Groq — `openai/gpt-oss-120b` (answers), `openai/gpt-oss-20b` (router, grader, rewriter, evaluator) | Structured output via Groq `json_schema` mode |
| Embeddings | Google Gemini `models/gemini-embedding-001`, 768 dimensions | `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY` task types |
| Vector DB | Qdrant — embedded on-disk by default, or a server via `QDRANT_URL` | Cosine distance |
| Web search | Tavily (if key set) → DuckDuckGo HTML + page fetching (fallback) | `httpx` + `lxml` |
| Persistence | SQLite (stdlib `sqlite3`) | Users, documents, conversations, messages |
| Auth | bcrypt password hashing, hand-rolled HS256 JWT (stdlib `hmac`) | No extra JWT dependency |
| Parsing | pypdf, python-docx, LangChain `RecursiveCharacterTextSplitter` | PDF, DOCX, TXT, MD |

---

## 4. Getting started

### Prerequisites

- Python 3.12+ (developed on 3.14), Node.js 18+
- A **Groq** API key and a **Gemini** API key. Tavily is optional.

### 1) Environment file

Create `.env` in the project root:

```env
GROQ_API_KEY=your-groq-key
GEMINI_API_KEY=your-gemini-key
# Optional
TAVILY_API_KEY=your-tavily-key
JWT_SECRET=some-long-random-string
QDRANT_URL=http://localhost:6333
```

### 2) Backend

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt

# Either works:
uvicorn app:app --reload       # same command from the project root or from inside backend/
```

Check: <http://localhost:8000/api/health> → `{"status": "healthy", ...}`.
Interactive API docs: <http://localhost:8000/docs>.

### 3) Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

### 4) Use it

1. Open the app and **Sign up** (username 3–32 chars `A-Z a-z 0-9 _ . -`, password ≥ 6 chars).
2. Go to **Documents** and upload a PDF/DOCX/TXT/MD file.
3. Go to **New Chat** and ask questions. Expand **Agent steps** under an answer to see the agent's path.

Runtime data is created automatically:

| Path | Contents |
|---|---|
| `backend/data/app.db` | SQLite database |
| `backend/data/.jwt_secret` | Auto-generated JWT secret (if `JWT_SECRET` unset) |
| `backend/vector_db/qdrant_data/` | Embedded Qdrant storage |
| `backend/documents/<user_id>/` | Uploaded original files |

To reset everything: stop the backend and delete those paths.

---

## 5. Configuration reference

All settings live in `backend/config/settings.py` (`Settings`, a pydantic-settings class). Every field
can be overridden by an environment variable / `.env` entry with the same name (case-insensitive).

| Setting | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | — | **Required.** Groq LLM access. |
| `GEMINI_API_KEY` | — | **Required.** Gemini embeddings. |
| `TAVILY_API_KEY` | `""` | Optional. Enables Tavily; otherwise DuckDuckGo. |
| `LLM_MODEL` | `openai/gpt-oss-120b` | Model that writes user-facing answers. |
| `FAST_LLM_MODEL` | `openai/gpt-oss-20b` | Model for routing, grading, rewriting, evaluation. |
| `LLM_TEMPERATURE` | `0.2` | Temperature of the answer model (fast model is fixed at 0). |
| `EMBEDDING_MODEL` | `models/gemini-embedding-001` | Embedding model. |
| `EMBEDDING_DIM` | `768` | Vector size. Changing it requires a new collection. |
| `QDRANT_URL` / `QDRANT_API_KEY` | `""` | Use a Qdrant server instead of the embedded store. |
| `QDRANT_PATH` | `backend/vector_db/qdrant_data` | Embedded store location. |
| `COLLECTION_NAME` | `agentic_rag_docs` | Qdrant collection. |
| `UPLOAD_DIR` | `backend/documents` | Where original files are stored. |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1000` / `200` | Characters per chunk and overlap between chunks. |
| `MAX_UPLOAD_MB` | `20` | Upload size limit. |
| `RETRIEVAL_K` | `6` | Nearest neighbours fetched per query. |
| `MIN_RELEVANCE_SCORE` | `0.45` | Cosine-similarity floor applied before LLM grading. |
| `MAX_RETRIEVAL_ATTEMPTS` | `3` | RAG retrieval rounds (initial + rewrites). |
| `MAX_WEB_ATTEMPTS` | `2` | Web search rounds. |
| `WEB_RESULTS` | `5` | Results per web search. |
| `HISTORY_TURNS` | `6` | Past messages passed to the agent as memory. |
| `DATABASE_PATH` | `backend/data/app.db` | SQLite file. |
| `JWT_SECRET` | `""` | Token signing key; auto-generated and persisted if empty. |
| `JWT_EXPIRE_MINUTES` | `1440` | Token lifetime (24 h). |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Allowed origins (any localhost port is also allowed by regex). |

Frontend: `VITE_API_URL` (optional) overrides the API base URL; by default the app calls `/api`
and the Vite dev server proxies it to `http://127.0.0.1:8000`.

---

## 6. Project structure

```
day7/
├── .env                      # secrets (not committed)
├── README.md                 # quick start
├── docs/DOCUMENTATION.md     # this file
├── requirements.txt          # Python dependencies
├── test_rag_pipeline.py      # end-to-end smoke test
├── backend/
│   ├── app.py                # FastAPI app, lifespan, CORS, router mounting
│   ├── config/
│   │   ├── settings.py       # all configuration
│   │   └── llm.py            # Groq client factories + structured-output helper
│   ├── api/
│   │   ├── security.py       # bcrypt + JWT
│   │   ├── dependencies.py   # get_current_user (auth guard)
│   │   └── routes/
│   │       ├── auth.py       # register / login / me
│   │       ├── chat.py       # chat, streaming chat, conversations, stats
│   │       ├── documents.py  # list / upload / delete documents
│   │       └── health.py     # health check
│   ├── models/
│   │   ├── database.py       # SQLite schema + data-access functions
│   │   └── schemas.py        # pydantic request/response models
│   ├── rag/
│   │   ├── ingestion.py      # load → clean → chunk → embed → upsert
│   │   ├── vectorstore.py    # Qdrant client, embeddings, per-user filters
│   │   └── retrieval.py      # multi-query retrieval + merge
│   ├── tools/
│   │   ├── __init__.py       # shared helpers (history, date, citation cleanup)
│   │   ├── greeting.py       # small-talk responder
│   │   ├── direct_answer.py  # parametric-knowledge answerer
│   │   ├── rag.py            # grounded answer generation
│   │   └── web_search.py     # search backends + web answer generation
│   ├── evaluation/
│   │   └── evaluator.py      # chunk grading + answer evaluation (LLM-as-judge)
│   ├── graph/
│   │   ├── state.py          # AgentState
│   │   ├── nodes.py          # every graph node
│   │   └── workflow.py       # edges, routing functions, run/stream helpers
│   ├── data/                 # runtime: app.db, .jwt_secret
│   ├── documents/            # runtime: uploaded files per user
│   └── vector_db/            # runtime: qdrant_data/
└── frontend/
    ├── index.html, vite.config.js, tailwind.config.js, postcss.config.js, package.json
    └── src/
        ├── main.jsx, App.jsx, index.css
        ├── services/api.jsx          # HTTP + SSE client, token storage
        ├── pages/Chat.jsx, Documents.jsx, Dashboard.jsx
        └── components/Sidebar.jsx, ChatWindow.jsx, InputBox.jsx,
                       Message.jsx, SourceCard.jsx, ToolBadge.jsx
```

---

## 7. The agent workflow

The agent is a LangGraph `StateGraph` defined in `backend/graph/workflow.py`. Nodes live in
`backend/graph/nodes.py`; each node takes the current `AgentState` and returns a partial update.

### 7.1 Graph

```
START → router ─┬─ greeting ───────────────────────────────────────────────► END
                │
                ├─ direct ──(NEEDS_WEB_SEARCH)──────────────────────────► web_search
                │     └──► evaluate ──(insufficient)──── escalate ──────► web_search
                │
                ├─ rag, no documents ─────────────────────────────────► no_results ► END
                ├─ rag/web, vague ──► rewrite
                │
                ├─ rag ──► retrieve ──► grade ──(0 relevant)──► rewrite / no_results
                │             ▲           │
                │             │       generate_rag ──(NO_ANSWER_IN_DOCUMENTS)──► rewrite / no_results
                │             │           │
                │          rewrite ◄── evaluate ──(insufficient, budget left)
                │                         │
                │                     finalize ──► END
                │
                └─ web ──► web_search ──(0 results)──► rewrite / no_results
                              │
                         generate_web ──► evaluate ──(insufficient)──► rewrite ──► web_search
```

### 7.2 Nodes

| Node | Model | Purpose | Key state written |
|---|---|---|---|
| `router` | fast | Classifies intent, judges query clarity, produces a standalone query using chat history and the user's document names. Falls back to `rag` (if docs) / `direct` on error. | `intent`, `query_is_clear`, `standalone_query`, `search_queries`, `attempts=0` |
| `greeting` | fast | Friendly 1–3 sentence reply. | `answer`, `tool=greeting` |
| `direct` | main | Answers from model knowledge; emits `NEEDS_WEB_SEARCH` when information is recent/unknown. | `answer`, `tool=direct` or `intent=web_search` |
| `rewrite` | fast | Rewrites and expands the query. Receives the reason, evaluator feedback and all previously tried queries. Attempt 1 clarifies; later attempts broaden (step-back) and decompose. RAG gets up to 4 queries; web gets 2. | `search_queries`, `rewritten_queries` (+=) |
| `retrieve` | — | Multi-query vector search scoped to the user. | `documents`, `attempts` (+1) |
| `grade` | fast | LLM relevance grading of candidate chunks; drops irrelevant ones. | `documents`, `rewrite_reason` |
| `generate_rag` | main | Grounded answer with `[n]` citations, or the `NO_ANSWER_IN_DOCUMENTS` signal. | `answer`, `sources`, `tool=rag` |
| `web_search` | — | Searches up to 2 queries, dedupes by URL. | `web_results`, `attempts` (+1) |
| `generate_web` | main | Answer from web results with citations. | `answer`, `sources`, `tool=web_search` |
| `evaluate` | fast | Judges `sufficient`, `grounded`, `score` (1–5), `missing`. Tracks the best-scoring candidate. Escalates weak direct answers to web search. | `evaluation`, `best`, `rewrite_reason` |
| `no_results` | — | Explains that nothing relevant was found (no docs / no matches / no web results). | `answer`, `tool=no_results` |
| `finalize` | — | Returns the answer; if retries were exhausted and it is still weak, returns the best candidate with a caveat note. | `answer`, `sources`, `evaluation` |

### 7.3 Routing functions (`workflow.py`)

| Function | Decision |
|---|---|
| `route_after_router` | greeting → `greeting`; direct → `direct`; rag without documents → `no_results`; unclear query → `rewrite`; else `retrieve` (rag) or `web_search`. |
| `route_after_direct` | Escalated → `web_search`; else `evaluate`. |
| `route_after_rewrite` | `web_search` or `retrieve` depending on intent. |
| `route_after_grade` | Relevant chunks → `generate_rag`; none & budget left → `rewrite`; else `no_results`. |
| `route_after_generate_rag` | Answer produced → `evaluate`; no-answer signal → `rewrite` / `no_results`. |
| `route_after_web` | Results → `generate_web`; none → `rewrite` / `no_results`. |
| `route_after_evaluate` | Sufficient & grounded → `finalize`; weak direct → `web_search`; weak rag/web with budget → `rewrite`; else `finalize`. |

Loops terminate because every retrieval/search increments `attempts`, which is compared with
`MAX_RETRIEVAL_ATTEMPTS` / `MAX_WEB_ATTEMPTS`. A global `recursion_limit` of 40 steps is an extra safeguard.

### 7.4 State (`graph/state.py`)

`AgentState` is a `TypedDict`. Most keys are **replaced** by node updates; two are **accumulated**
with `Annotated[list, operator.add]`:

- `trace` — list of `{node, detail, data}` steps (what the UI shows).
- `rewritten_queries` — every rewritten query produced across retries.

Other key fields: `query`, `user_id`, `username`, `history`, `doc_names`, `intent`, `standalone_query`,
`query_is_clear`, `search_queries`, `rewrite_reason` (`vague_query` / `no_relevant_docs` /
`insufficient_answer` / `no_web_results`), `attempts`, `documents`, `web_results`, `answer`, `tool`,
`sources`, `evaluation`, `best`.

### 7.5 Structured output

All decision-making LLM calls use `structured(ModelClass)` from `config/llm.py`, which wraps the model with
`with_structured_output(..., method="json_schema").with_retry(stop_after_attempt=2)`.
Groq's JSON-schema mode was chosen over tool-calling mode because gpt-oss models intermittently fail
tool calls (`tool_use_failed`). The schemas are `Route`, `Rewrite`, `DocGrades` and `AnswerEval`.

### 7.6 Worked examples (from the smoke test)

| Question | Path |
|---|---|
| "Hey there! What can you do?" | router → greeting |
| "Summarize the uploaded document" | router → retrieve → grade (4/4) → generate → evaluate (5/5) → finalize |
| "skills projects stuff??" | router (vague) → rewrite → retrieve (4 queries) → grade (3/4) → generate → evaluate → finalize |
| "Refund policy for enterprise customers?" (not in docs) | router → retrieve → grade (0/4) → rewrite → retrieve → grade (0) → rewrite → retrieve → grade (0) → no_results |
| "Difference between a process and a thread" | router → direct → evaluate → finalize |
| "Latest AI news this week" | router → web_search → generate_web → evaluate → finalize |

---

## 8. Ingestion pipeline

`POST /api/documents/upload` → `routes/documents.py::upload_document` → `rag/ingestion.py::ingest_file`.

1. **Validate** — extension in `{.pdf, .docx, .txt, .md}`; size ≤ `MAX_UPLOAD_MB` (read at most limit+1 bytes);
   filename sanitized (`_safe_name` strips directories and unsafe characters).
2. **Deduplicate** — SHA-256 of the bytes. If the same user already has it indexed, return `status: "duplicate"`.
   A previously failed copy is cleaned up and re-ingested.
3. **Record** — a `documents` row with `status="processing"`; the file is saved as
   `backend/documents/<user_id>/<doc_id><ext>`.
4. **Load** (`load_file`) — PDF: one LangChain `Document` per page with `page` metadata (pypdf);
   DOCX: paragraphs + table rows; TXT/MD: raw text. `_clean` removes null bytes, re-joins hyphenated
   line breaks and collapses whitespace. No text → `IngestionError` (e.g. scanned PDFs).
5. **Chunk** (`split_documents`) — `RecursiveCharacterTextSplitter`, 1000 chars / 200 overlap, splitting on
   paragraphs → lines → sentences → words; chunks ≤ 20 chars are dropped.
6. **Embed** — Gemini, `task_type="RETRIEVAL_DOCUMENT"`, filename passed as title, 768 dims.
7. **Upsert** — Qdrant points with deterministic ids `uuid5(doc_id:chunk_index)` and payload
   `{page_content, metadata: {user_id, doc_id, source, page, chunk_index, start_index}}`
   (LangChain-compatible layout), in batches of 128.
8. **Finish** — status `indexed` with page/chunk counts, or `failed` with the error (partial vectors removed).

Deleting a document removes its vectors (filter on `user_id` + `doc_id`), the stored file and the DB row.

---

## 9. Retrieval

`rag/retrieval.py::retrieve(queries, user_id, k, min_score)`:

1. Deduplicate the query list, preserving order.
2. Embed all queries in parallel (thread pool, `task_type="RETRIEVAL_QUERY"`).
3. For each query, search Qdrant for the top `k` points **filtered to `metadata.user_id`**.
4. Drop hits below `MIN_RELEVANCE_SCORE` (cosine).
5. Merge hits by point id, keeping the highest score and the query that found it.
6. Return the top `k + 2` by score (headroom for the LLM grader).

The LLM grader (`evaluation/evaluator.py::grade_documents`) then keeps only chunks that actually help answer
the question — this is the main precision filter; the cosine floor only removes obvious noise.

---

## 10. Backend module reference

### `app.py`
- Adds the project root to `sys.path` so the app starts from either the root or `backend/`.
- `lifespan` — verifies required API keys, runs `init_db()`, opens the Qdrant collection.
- CORS: `CORS_ORIGINS` plus a regex allowing any `localhost` / `127.0.0.1` port.
- Mounts `health`, `auth`, `chat`, `documents` routers under `/api`.

### `config/settings.py`
`Settings` (pydantic-settings) reads `.env`; `settings` is the shared instance. See [§5](#5-configuration-reference).

### `config/llm.py`
| Function | Description |
|---|---|
| `get_llm()` | Cached `ChatGroq` for `LLM_MODEL` (answers). |
| `get_fast_llm()` | Cached `ChatGroq` for `FAST_LLM_MODEL`, temperature 0. |
| `structured(model_cls, fast=True)` | Schema-constrained runnable returning a pydantic object; json_schema mode + 1 retry. |

### `models/database.py`
One shared SQLite connection guarded by a re-entrant lock. `tx()` is a context manager providing a
serialized transaction (commit on success, rollback on error). All queries use parameter placeholders and
filter by `user_id`.

| Group | Functions |
|---|---|
| Setup | `init_db`, `_get_conn`, `tx`, `_now`, `_one`, `_all` |
| Users | `create_user`, `get_user_by_username`, `get_user` |
| Documents | `add_document`, `update_document` (allow-listed columns), `find_document_by_hash`, `list_documents`, `get_document`, `delete_document` |
| Conversations | `create_conversation`, `get_conversation`, `list_conversations`, `rename_conversation` (unused), `delete_conversation` |
| Messages | `add_message` (also bumps `conversations.updated_at`), `list_messages(conv_id, limit)` |
| Stats | `user_stats` — documents/chunks, conversations, queries per tool, rewrites, evaluations, average latency |

### `models/schemas.py`
Pydantic models: `Credentials`, `UserOut`, `TokenOut`, `DocumentOut`, `UploadOut`, `ChatRequest`,
`ChatResponse`, `ConversationOut`, `MessageOut`.

### `api/security.py` and `api/dependencies.py`
See [§11](#11-authentication--security).

### `rag/vectorstore.py`
| Function | Description |
|---|---|
| `get_embeddings()` | Cached Gemini embeddings client (768 dims). |
| `get_client()` | Cached Qdrant client (server or embedded) and ensures the collection. |
| `_ensure_collection` | Creates the collection (cosine); errors if an existing collection has a different vector size; creates payload indexes on a server. |
| `user_filter(user_id, doc_id=None)` | Qdrant filter on `metadata.user_id` (+ `metadata.doc_id`). |
| `upsert`, `search`, `delete_document_vectors`, `count_user_vectors` | Lock-protected Qdrant operations. |

### `rag/ingestion.py`, `rag/retrieval.py`
See [§8](#8-ingestion-pipeline) and [§9](#9-retrieval).

### `tools/`
| Module | Functions | Notes |
|---|---|---|
| `__init__.py` | `history_to_messages`, `history_to_text`, `today`, `clean_answer`, `FORMAT_RULES` | `clean_answer` converts gpt-oss `【1†L1-L3】` citations to `[1]`. |
| `greeting.py` | `respond(query, history, username, doc_count)` | Fast model. |
| `direct_answer.py` | `answer(query, history) -> (text, needs_web)` | Sentinel `NEEDS_WEB_SEARCH`. |
| `rag.py` | `format_context`, `generate(query, docs, history) -> str \| None`, `to_sources` | Sentinel `NO_ANSWER_IN_DOCUMENTS` → `None`. |
| `web_search.py` | `search(query, n)`, `generate(query, results, history)`, `to_sources` | Tavily → DuckDuckGo fallback; DuckDuckGo results are enriched by fetching and cleaning the top 4 pages (2,500 chars each). Never raises; returns `[]` on failure. |

### `evaluation/evaluator.py`
| Function | Returns | Failure behaviour |
|---|---|---|
| `grade_documents(query, docs)` | Relevant subset of `docs` | Fail-open (keeps all) |
| `evaluate_answer(query, answer, context=None)` | `{sufficient, grounded, score, missing}` | Accepts the answer (score 3) |

### `graph/`
See [§7](#7-the-agent-workflow). Public entry points in `workflow.py`:
`run_agent(query, user, history, doc_names)` → final state, and
`stream_agent(...)` → generator of `("step", trace_step)` … `("final", state)`.

### `api/routes/`
See [§13](#13-http-api-reference). Internal helpers in `chat.py`: `_prepare` (conversation, history,
doc names, save user message), `_persist` (save assistant message + meta, build response),
`_error_state` (graceful failure answer).

---

## 11. Authentication & security

### Passwords
- `_prehash`: SHA-256 → base64 (44 bytes), so passwords longer than bcrypt's 72-byte limit remain fully significant.
- `hash_password`: bcrypt with a random salt (cost 12). Only the hash is stored.
- `verify_password`: `bcrypt.checkpw`; malformed hashes return `False`.

### Tokens (JWT, HS256)
- `create_access_token(user_id, username)` → `base64url(header).base64url(payload).base64url(HMAC-SHA256)`;
  payload `{sub, name, iat, exp}`, lifetime `JWT_EXPIRE_MINUTES`.
- `decode_access_token(token)` → verifies the signature with `hmac.compare_digest` (constant time), requires
  `alg == HS256`, rejects expired tokens, and returns `None` for any malformed input.
- Secret: `JWT_SECRET`, or a random value generated once into `backend/data/.jwt_secret`.

### Request guard
`dependencies.get_current_user` (used via `Depends`) reads the `Authorization: Bearer` header, decodes
the token, confirms the user still exists and returns `{id, username}`. Every failure returns the same
`401 Not authenticated`. The frontend clears its token and shows the login screen on any 401.

### Data isolation & input safety
- Every SQL query filters by `user_id`; every Qdrant query filters by `metadata.user_id`.
- SQL uses placeholders only; dynamic column names in `update_document` are allow-listed.
- Upload filenames are sanitized and files are stored under generated ids, preventing path traversal.
- Upload size is enforced while reading. Login errors do not reveal whether a username exists.

---

## 12. Data model

### SQLite (`backend/data/app.db`)

```
users ──< documents
  │
  └──< conversations ──< messages
```

| Table | Columns | Constraints |
|---|---|---|
| `users` | `id`, `username`, `password_hash`, `created_at` | `username` unique, case-insensitive |
| `documents` | `id`, `user_id`, `filename`, `file_hash`, `size_bytes`, `pages`, `chunks`, `status` (`processing`/`indexed`/`failed`), `error`, `created_at` | `UNIQUE(user_id, file_hash)`, cascade on user delete |
| `conversations` | `id`, `user_id`, `title`, `created_at`, `updated_at` | cascade on user delete |
| `messages` | `id`, `conversation_id`, `role` (`user`/`assistant`), `content`, `tool`, `meta` (JSON), `created_at` | cascade on conversation delete |

`messages.meta` for assistant messages:

```json
{
  "intent": "rag",
  "sources": [{"type": "document", "title": "a.pdf", "document": "a.pdf", "page": 2, "score": 0.71, "snippet": "..."}],
  "rewritten_queries": ["..."],
  "evaluation": {"sufficient": true, "grounded": true, "score": 5, "missing": ""},
  "trace": [{"node": "router", "detail": "Intent → rag. ...", "data": {}}],
  "latency_ms": 5796
}
```

### Qdrant

Collection `agentic_rag_docs`, vectors of size 768, cosine distance. Point payload:

```json
{
  "page_content": "chunk text",
  "metadata": {"user_id": "…", "doc_id": "…", "source": "a.pdf", "page": 2, "chunk_index": 7, "start_index": 5400}
}
```

---

## 13. HTTP API reference

Base URL `http://localhost:8000/api`. All endpoints except `health`, `auth/register` and `auth/login`
require `Authorization: Bearer <token>`. Errors have the shape `{"detail": "message"}`.

### Health

| Method & path | Response |
|---|---|
| `GET /health` | `{"status": "healthy", "llm": "...", "embeddings": "...", "web_search": "duckduckgo" \| "tavily"}` |

### Auth

| Method & path | Body | Response |
|---|---|---|
| `POST /auth/register` | `{"username", "password"}` | `201` `{"access_token", "token_type": "bearer", "user": {"id", "username"}}` — `409` if taken, `422` if invalid |
| `POST /auth/login` | `{"username", "password"}` | `200` same as above — `401` on bad credentials |
| `GET /auth/me` | — | `{"id", "username"}` |

### Documents

| Method & path | Body | Response |
|---|---|---|
| `GET /documents` | — | `[DocumentOut]` newest first |
| `POST /documents/upload` | multipart `file` | `{"status": "success" \| "duplicate", "filename", "chunks_indexed", "document": DocumentOut}` — `400` bad type/empty, `413` too large, `422` no extractable text, `500` indexing failed |
| `DELETE /documents/{id}` | — | `204` — `404` if not found |

`DocumentOut`: `{id, filename, size_bytes, pages, chunks, status, error, created_at}`.

### Chat

**`POST /chat`** — body `{"message": "...", "conversation_id": null | "id"}` (omit/null to start a new conversation).

```json
{
  "conversation_id": "…", "message_id": "…",
  "answer": "…markdown with [n] citations…",
  "tool": "rag | web_search | direct | greeting | no_results",
  "intent": "rag",
  "sources": [ … ], "rewritten_queries": [ … ],
  "evaluation": {"sufficient": true, "grounded": true, "score": 5, "missing": ""},
  "trace": [ {"node": "router", "detail": "…", "data": {…}} ],
  "latency_ms": 5796
}
```

**`POST /chat/stream`** — same body; responds with `text/event-stream`:

```
event: meta
data: {"conversation_id": "…", "title": "…"}

event: step                      (repeated, one per trace step, as nodes finish)
data: {"node": "retrieve", "detail": "Attempt 1: …", "data": {…}}

event: final
data: { …same object as POST /chat… }
```

### Conversations & stats

| Method & path | Response |
|---|---|
| `GET /conversations` | `[{id, title, created_at, updated_at}]`, most recently updated first |
| `GET /conversations/{id}/messages` | `[{id, role, content, tool, meta, created_at}]` oldest first |
| `DELETE /conversations/{id}` | `204` (messages cascade) |
| `GET /stats` | `{documents, chunks, conversations, queries, by_tool, queries_rewritten, answers_evaluated, answers_sufficient, avg_latency_ms}` |

Example:

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"secret123"}' | jq -r .access_token)
curl -X POST localhost:8000/api/documents/upload -H "Authorization: Bearer $TOKEN" -F file=@report.pdf
curl -X POST localhost:8000/api/chat -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"What are the key findings of the report?"}'
```

---

## 14. Frontend

### Data flow

```
App.jsx ── user, conversations, activeConversationId, activePage
   ├── LoginScreen            → apiService.login / register
   ├── Sidebar                → select / new / delete conversation, navigate, log out
   ├── pages/Chat             → apiService.streamMessage → ChatWindow → Message → SourceCard, ToolBadge
   ├── pages/Documents        → getDocuments / uploadDocument / deleteDocument
   └── pages/Dashboard        → getStats
```

### `services/api.jsx`
- Token stored in `localStorage` (`agentic_rag_token`), all access wrapped in `try/catch`.
- `request()` adds the bearer token, handles JSON vs `FormData`, extracts the backend `detail` on errors,
  and on a 401 clears the token and calls the registered unauthorized handler (→ login screen).
- `streamMessage()` reads the SSE response with `ReadableStream.getReader()`, buffers chunks, splits events on
  blank lines and dispatches `onMeta` / `onStep` / `onFinal`.

### Components & pages
| File | Responsibility |
|---|---|
| `App.jsx` | Session restore via `/auth/me`, login/register screen, layout, conversation list refresh, health indicator. |
| `pages/Chat.jsx` | Loads a conversation's messages; on send, appends the user bubble and a *pending* agent bubble that fills with live steps, then is replaced by the final answer. `createdHereRef` prevents reloading a conversation that was just created mid-stream. |
| `components/ChatWindow.jsx` | Empty state with suggestions, message list, auto-scroll. |
| `components/Message.jsx` | Markdown rendering (GFM), rewritten-query note, sources, tool badge, quality score, latency, collapsible **Agent steps**. |
| `components/SourceCard.jsx` | Numbered sources (document + page / web link) with expandable snippets. |
| `components/ToolBadge.jsx` | Colored badge per tool (`rag`, `web_search`, `direct`, `greeting`, `no_results`). |
| `components/Sidebar.jsx` | New chat, conversation list with delete, Documents / Dashboard nav, user & logout. |
| `components/InputBox.jsx` | Message input and send button. |
| `pages/Documents.jsx` | Multi-file drag & drop / picker, client-side type & size checks, sequential upload, status list, delete. |
| `pages/Dashboard.jsx` | Stat tiles (queries, RAG hit rate, documents, latency, rewrites, sufficiency) and routing breakdown bars. |
| `index.css` | Tailwind layers + `.markdown` styles for rendered answers. |

### Dev proxy
`vite.config.js` proxies `/api` to `http://127.0.0.1:8000`, so the browser makes same-origin requests
(no CORS issues, any Vite port works). For a production build, serve `frontend/dist` behind the same
origin as the API, or set `VITE_API_URL`.

---

## 15. Testing

`test_rag_pipeline.py` is an end-to-end smoke test against a running backend:

```bash
python test_rag_pipeline.py                 # uses ./sample.pdf
python test_rag_pipeline.py path/to/file.pdf
```

It registers a throwaway user (`tester_xxxxxx`), uploads the file, then sends one message per path
(greeting, RAG, vague RAG, not-in-documents, direct, web search) in one conversation, printing the tool,
latency, every trace step, the answer and the sources. Each run leaves a test user and document in the
local database.

Manual checks worth doing after changes: upload the same file twice (→ duplicate), call a protected
endpoint without a token (→ 401), and inspect **Agent steps** in the UI for routing decisions.

---

## 16. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ModuleNotFoundError: No module named 'backend'` or `Could not import module "app"` | You're in a folder other than the project root or `backend/`. `uvicorn app:app --reload` works from those two (the root-level `app.py` re-exports `backend/app.py`). |
| UI shows **"Cannot reach the backend server"** | Backend not running on `127.0.0.1:8000`, or you're opening an old build. The Vite proxy handles ports/CORS — restart `npm run dev` after changing `vite.config.js`. |
| `[Errno 10048] … address already in use` | Another process uses port 8000. Stop it or use `--port 8001` (and update the proxy target). |
| Qdrant `JSONDecodeError` / lock errors at startup | Two backend processes are using the embedded store at once. Run a single backend, or use a Qdrant server (`QDRANT_URL`). |
| `Missing required settings in .env` | `GROQ_API_KEY` or `GEMINI_API_KEY` missing from the root `.env`. |
| `Collection … has vector size X but EMBEDDING_DIM=Y` | Changed embedding size. Set a new `COLLECTION_NAME` or delete `backend/vector_db/qdrant_data`. |
| `404 models/… is not found` (embeddings) | Model retired by Google (e.g. `text-embedding-004`). Use `gemini-embedding-001`. |
| Groq `model_decommissioned` / not found | Update `LLM_MODEL` / `FAST_LLM_MODEL` to a model listed by Groq. |
| Upload returns 422 "No extractable text" | Scanned/image-only PDF. OCR it first. |
| Answers routed to the wrong tool | Inspect the router's reason in **Agent steps**; adjust `ROUTER_PROMPT` in `graph/nodes.py`. |
| Web search returns nothing | DuckDuckGo rate limiting or blocking. Add a `TAVILY_API_KEY`. |
| `Router failed` / `grading failed` warnings in the log | Transient Groq errors; the agent falls back safely. Persistent failures usually mean an invalid key or model name. |

---

## 17. Extending the system

**Add a new intent/tool** (e.g. a calculator):
1. Add the value to `Route.intent`'s `Literal[...]` and describe it in `ROUTER_PROMPT` (`graph/nodes.py`).
2. Implement the tool in `backend/tools/` and a node in `nodes.py` that sets `answer`, `tool`, `trace`.
3. Register the node in `build_graph()` and route to it in `route_after_router` (`graph/workflow.py`).
4. Add a badge entry in `ToolBadge.jsx` and a label in `Message.jsx` `NODE_LABELS`.

**Support a new file type:** extend `SUPPORTED_EXTENSIONS` and `load_file` in `rag/ingestion.py`, and `ACCEPTED`
in `pages/Documents.jsx`.

**Swap models/providers:** change `LLM_MODEL` / `FAST_LLM_MODEL` in `.env`. For another provider, edit the
factories in `config/llm.py`; keep structured output working for `Route`, `Rewrite`, `DocGrades`, `AnswerEval`.
Changing the embedding model or dimension requires re-ingesting documents into a new collection.

**Tune retrieval quality:** `CHUNK_SIZE` / `CHUNK_OVERLAP`, `RETRIEVAL_K`, `MIN_RELEVANCE_SCORE`,
`MAX_RETRIEVAL_ATTEMPTS`. Candidate improvements: hybrid (BM25 + dense) search, a cross-encoder reranker,
or per-document filtering from the chat UI.

**Scale out:** point `QDRANT_URL` to a Qdrant server (enables multiple workers and payload indexes), and
replace SQLite with Postgres by reimplementing `models/database.py` behind the same functions.

---

## 18. Known limitations

- **Latency** — each agent step is an LLM call; retry-heavy paths (e.g. "no matches found") can take 30–60 s.
- **Tokens cannot be revoked** — logout only removes the browser's copy; tokens stay valid until expiry.
  Tokens live in `localStorage` (an HttpOnly cookie would be more robust against XSS).
- **Single-process embedded Qdrant** — use a Qdrant server for multiple workers.
- **Synchronous ingestion** — large files block the upload request until indexed; a background job queue would scale better.
- **No OCR** — image-only PDFs cannot be indexed.
- **LLM judges are fallible** — grading and evaluation reduce, but do not eliminate, irrelevant or ungrounded answers.
- **DuckDuckGo scraping** is best-effort and may be rate-limited; Tavily is more reliable.
- **No rate limiting** on login or chat endpoints.
