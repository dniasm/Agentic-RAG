# Agentic RAG — Project Documentation

> Full technical documentation, updated 16 November 2026. For a quick start, see [`README.md`](../README.md).

## Table of contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Tech stack and model roles](#3-tech-stack-and-model-roles)
4. [Getting started](#4-getting-started)
5. [Configuration reference](#5-configuration-reference)
6. [Project structure and file guide](#6-project-structure-and-file-guide)
7. [The agent workflow](#7-the-agent-workflow)
8. [Memory](#8-memory)
9. [Ingestion pipeline](#9-ingestion-pipeline)
10. [Retrieval](#10-retrieval)
11. [Token accounting](#11-token-accounting)
12. [Backend module reference](#12-backend-module-reference)
13. [Authentication & security](#13-authentication--security)
14. [Data model](#14-data-model)
15. [HTTP API reference](#15-http-api-reference)
16. [Frontend](#16-frontend)
17. [Testing and benchmarks](#17-testing-and-benchmarks)
18. [Troubleshooting](#18-troubleshooting)
19. [Extending the system](#19-extending-the-system)
20. [Known limitations](#20-known-limitations)
21. [Measurements: before and after](#21-measurements-before-and-after)
22. [Document library](#22-document-library)

---

## 1. Overview

Agentic RAG is a full-stack question-answering application. A user signs in, uploads documents into a
private vector store, and chats with an **agent** that decides, per message, where the answer should come from:

| Source | What happens |
|---|---|
| **Small talk** | A short, friendly reply in the same voice as every other answer. |
| **Model knowledge** | Answered from the model's own training, after a *knowledge gate* confirms it can answer reliably. |
| **Documents (RAG)** | Answered strictly from the user's uploaded documents, with `[n]` citations. |
| **Web search** | Current or online information, answered with links. |
| **Document actions** | Rename, edit (PDFs in place), undo / redo, or queue changes to many documents (§22). |

What makes it *agentic* rather than a fixed pipeline:

- **Source choice follows the user.** The intake step records which source the user wants (or `auto`), and that
  preference sets the order in which sources are tried. One fallback step moves to the next source when one falls
  short, and a notice tells the user why.
- **Knowledge gate.** Before answering from its own knowledge, the large model checks whether it can: open questions
  (opinions, philosophy, hypotheticals) pass; factual ones need high confidence; anything time-sensitive or about the
  user's own files goes to another source.
- **Query rewriting** — vague or context-dependent questions are rewritten before searching.
- **Self-checks** — a reranker (Voyage `rerank-2.5`) judges whether retrieved passages really answer the question;
  finished document and web answers are checked in code (every claim cited and found in its passage), and the large
  model checks the rest for sufficiency and groundedness.
- **Honest failure** — when no source has a reliable answer, the user is told so; an answer that couldn't be
  checked is kept but marked as unverified.
- **Memory** — facts about the user and short summaries of past exchanges are remembered across conversations.
- **Transparency** — every step is recorded as a trace entry and streamed live to the UI.

---

## 2. Architecture

```
┌──────────────────────── Browser (React, Vite :5173) ──────────────────────────┐
│  Login ─ Chat (SSE stream) ─ Documents ─ Overview        hash routes, themes  │
└───────────────┬───────────────────────────────────────────────────────────────┘
                │  /api/*  (Vite dev proxy → 127.0.0.1:8000, login cookie)
┌───────────────▼──────────────────── FastAPI (:8000) ──────────────────────────┐
│  routes/auth ─ routes/chat ─ routes/documents ─ routes/health ─ library/api   │
│        │            │   └── memory worker (background) ── tools/memory        │
│  library/auth  graph/workflow.py (LangGraph agent)      rag/ingestion.py      │
│        │            │   │   │                                │                │
│        ▼            │   │   └── tools/web_search ──► Tavily / DuckDuckGo / Wikipedia
│  SQLite (app.db)    │   └────── rag/retrieval + rerank ──┐                    │
│  users, user_facts, │                                    ▼                    │
│  documents,         │                    Qdrant: documents (dense + BM25)     │
│  conversations,     │                            user_memories (Voyage)       │
│  messages           ├── Groq: gpt-oss-120b, gpt-oss-20b, qwen3.8-27b          │
│                     ├── Gemini embeddings (documents) · Voyage (memory)       │
│                     └── Voyage rerank-2.5                                     │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Request lifecycle (chat):**

1. The frontend POSTs the message to `/api/chat/stream`; the browser sends the HttpOnly login cookie.
2. `get_current_user` looks the token up (§13). `_prepare` loads or creates the conversation, the last 6 messages, the
   user's remembered facts, up to 3 relevant past-conversation summaries and the user's indexed document names, and
   saves the user message.
3. The agent runs in its own thread (`stream_agent`), with a `UsageTracker` counting tokens. Its events (`status`,
   `step`, `draft`) are relayed to the browser as Server-Sent Events.
4. The final state is saved as an assistant message (sources, notices, trace, evaluation, latency, token usage) and
   sent as the `final` event. Because the agent runs in its own thread, the answer is saved even if the browser
   disconnects.
5. In the background, one job at a time, the message is queued for batched fact extraction (§8), the memory model
   summarises the exchange, and the request's token use is logged.

---

## 3. Tech stack and model roles

| Layer | Technology | Notes |
|---|---|---|
| Frontend | React 18, Vite 5, Tailwind 3, lucide-react, react-markdown + remark-gfm | Hash routes, light/dark/system theme, Geist font |
| API | FastAPI, Uvicorn, Starlette `StreamingResponse` (SSE) | Sync routes run in a threadpool |
| Agent | LangGraph `StateGraph` | Conditional edges implement the loops and fallback |
| LLMs | Groq (see below) | Strict `json_schema` output for gpt-oss decisions |
| Document embeddings | Google Gemini `models/gemini-embedding-001`, 768 dims | Question embeddings cached on disk |
| Keyword search | BM25 sparse vectors in Qdrant | Fused with dense search by Reciprocal Rank Fusion |
| Reranking | Voyage `rerank-2.5` (hosted) | Confidence zones + MMR diversity; LLM check if Voyage can't score |
| Memory embeddings | Voyage AI `voyage-4-lite`, 1,024 dims | Conversation summaries only |
| Vector DB | Qdrant — embedded on-disk by default, or a server via `QDRANT_URL` | Cosine distance |
| Web search | Tavily (if key set) → DuckDuckGo → Wikipedia | Results cached for an hour |
| Persistence | SQLite (stdlib `sqlite3`) | Users, facts, documents, conversations, messages |
| Auth | PBKDF2-SHA256 passwords (stdlib `hashlib`), random login tokens in an HttpOnly cookie, only their SHA-256 stored | Logout revokes the token on the server |
| Document editing | PyMuPDF, fontTools, pyphen, python-docx, fpdf2 | In-place PDF edits, tiny font subsets, hyphenation, DOCX edits, PDF export (§22) |
| Parsing | pypdf, python-docx, LangChain `RecursiveCharacterTextSplitter` | PDF, DOCX, TXT, MD |

**Model roles** (all on Groq; each model has its own rate limits):

| Model | Used for |
|---|---|
| `openai/gpt-oss-120b` | Every user-facing reply (answers and small talk), the knowledge gate, answer evaluation, reviews of uncertain decisions |
| `openai/gpt-oss-20b` | Intake routing and query rewrites; also writes answers if the 120B's quota runs out (with a notice) |
| `qwen/qwen3.8-27b` | Memory: fact extraction and exchange summaries (no hidden reasoning, so a call costs a few dozen output tokens) |

The small model never grades the large one: evaluation and reviews are done by the 120B.

---

## 4. Getting started

### Prerequisites

- Python 3.12+ (developed on 3.14), Node.js 18+
- A **Groq** API key and a **Gemini** API key. A **Voyage** key is needed for reranking (without it the LLM
  relevance check is used) and conversation memory. Tavily is optional.

### 1) Environment file

Create `.env` in the project root:

```env
GROQ_API_KEY=your-groq-key
GEMINI_API_KEY=your-gemini-key
# Optional
TAVILY_API_KEY=your-tavily-key
VOYAGE_API_KEY=your-voyage-key        # reranking + conversation memory
SIGNUP_CODE=an-invite-code            # needed for every sign-up after the first; empty: sign-ups closed
QDRANT_URL=http://localhost:6333
```

### 2) Backend

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
uvicorn app:app --reload          # from the project root or from inside backend/
```

Check: <http://localhost:8000/api/health>. Interactive API docs: <http://localhost:8000/docs>.
Reranking is hosted (Voyage), so nothing heavy loads at startup. An environment installed before 14 November may
still have `torch` and `sentence-transformers`: `voyageai` then loads them on import (through
`langchain_text_splitters`), which slows startup. `pip uninstall torch sentence-transformers transformers` fixes it.

### 3) Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

**Design preview** (no backend or login): with `npm run dev` running, open
`http://localhost:5173/preview.html#/chat/c1` (also `#/chat`, `#/documents`, `#/overview`; add `?state=empty`,
`?state=offline` or `?theme=dark`). It renders the real app with sample data from `src/dev/fixtures.js`.

### 4) Use it

1. Open the app and **create an account** (username 3–32 chars `A-Z a-z 0-9 _ . -`, password ≥ 8 chars). The first
   account needs no invite code; later ones need `SIGNUP_CODE`.
2. Go to **Documents** and upload a PDF/DOCX/TXT/MD file.
3. Start a **New chat** and ask questions, or ask it to rename or edit a document (§22). Under each answer, **Sources** and **Steps** show where it came from and
   the path the agent took. The theme switch (System / Light / Dark) is at the bottom of the sidebar.

Runtime data is created automatically:

| Path | Contents |
|---|---|
| `backend/data/app.db` | SQLite database |
| `backend/data/embedding_cache.db` | Cached question embeddings |
| `backend/data/token_usage.jsonl` | One line per request: tokens per model and step, route, path |
| `backend/data/library/<user_id>/` | The stored documents, named after their current titles |
| `backend/data/versions/<doc_id>/` | Each edit's backup (what Undo puts back) and redo copy |
| `backend/data/fonts/fetched/` | Fonts downloaded for PDF edits (§22) |
| `backend/data/logs/` | `events.log` (major events) and `all.log` (everything), rotating |
| `backend/vector_db/qdrant_data/` | Embedded Qdrant storage (documents and memory collections) |

To reset everything: stop the backend and delete those paths.

---

## 5. Configuration reference

All settings live in `backend/config/settings.py` (`Settings`, a pydantic-settings class). Every field can be
overridden by an environment variable / `.env` entry with the same name (case-insensitive).

| Setting | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` / `GEMINI_API_KEY` | — | **Required.** |
| `GROQ_API_KEY_2` … `GROQ_API_KEY_4` | — | Optional keys from **other** Groq organisations. Groq limits are per organisation and model, so when a model's per-minute or daily limit is used up on one key, that model is called with the next key (`config/llm.call_with_keys`); the exhausted key is skipped until the reset time in Groq's error. The 120B → 20B and Qwen → 20B backups start only when every key is out for that model. Keys from the same account share its limits and add nothing. |
| `TAVILY_API_KEY` | `""` | Optional. Enables Tavily as the first web provider. |
| `VOYAGE_API_KEY` | `""` | Reranking and conversation memory. Without it, episodes are skipped (facts still work). |
| `LLM_MODEL` | `openai/gpt-oss-120b` | Replies, knowledge gate, evaluation, reviews. |
| `FAST_LLM_MODEL` | `openai/gpt-oss-20b` | Routing and rewrites; backup writer. |
| `MEMORY_LLM_MODEL` | `qwen/qwen3.8-27b` | Fact extraction and exchange summaries. |
| `LLM_TEMPERATURE` | `0.0` | Answer writing. |
| `CHAT_TEMPERATURE` | `0.8` | Small talk only. |
| `FAST_LLM_REASONING_EFFORT` / `JUDGE_REASONING_EFFORT` | `low` / `low` | Hidden reasoning for decisions / reviews and grading. |
| `EMBEDDING_MODEL` / `EMBEDDING_DIM` | `models/gemini-embedding-001` / `768` | Document embeddings. Changing the size requires a new collection. |
| `VOYAGE_MEMORY_MODEL` / `VOYAGE_MEMORY_DIMENSIONS` / `VOYAGE_MEMORY_COLLECTION` | `voyage-4-lite` / `1024` / `user_memories` | Memory embeddings. |
| `QDRANT_URL` / `QDRANT_API_KEY` / `QDRANT_PATH` | `""` / `""` / `backend/vector_db/qdrant_data` | Server or embedded store. |
| `COLLECTION_NAME` | `agentic_rag_docs_v2` | Hybrid (dense + BM25) document collection. |
| `MAX_UPLOAD_MB` | `20` | Largest upload. |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1000` / `200` | Characters per chunk and overlap. |
| `RETRIEVAL_K` | `6` | Passages finally used for an answer. |
| `RETRIEVAL_MODE` | `hybrid` | `hybrid` (dense + BM25 with RRF) or `dense`. |
| `HYBRID_CANDIDATES` / `RRF_K` | `20` / `60` | Results per list before fusion; RRF constant. |
| `BM25_K1` / `BM25_B` / `BM25_AVG_LEN` | `1.2` / `0.75` / `120` | BM25 parameters. |
| `RERANK_ENABLED` | `True` | Use a reranker (otherwise the LLM relevance check). |
| `VOYAGE_RERANK_MODEL` / `VOYAGE_RERANK_TIMEOUT` | `rerank-2.5` / `15` | Hosted reranker; a slow call falls back instead of waiting. |
| `VOYAGE_REQUESTS_PER_MINUTE` / `VOYAGE_RERANK_RESERVE` | `3` / `1` | One limit for every Voyage call (reranking, memory recall, episode saves); recall and saves leave this many slots for the reranker. Raise the limit if the Voyage account has a payment method. |
| `VOYAGE_RERANK_MAX_WAIT` / `VOYAGE_RECALL_MAX_WAIT` / `VOYAGE_SAVE_MAX_WAIT` | `10` / `3` / `120` | Seconds each waits for a free slot: then the LLM relevance check, an answer without past conversations, or a skipped episode. |
| `VOYAGE_RELEVANCE_HIGH` / `VOYAGE_RELEVANCE_LOW` | `0.55` / `0.45` | Confidence zones on Voyage's scale (calibrated). |
| `RERANK_CANDIDATES` / `RERANK_TOP_N` / `MMR_LAMBDA` | `20` / `12` / `0.7` | Candidates scored; pool MMR picks from; relevance vs diversity. |
| `WEAK_MATCH_RETRIES` | `1` | Extra reworded searches in the middle zone. |
| `MIN_RELEVANCE_SCORE` / `RAG_SKIP_SCORE` | `0.45` / `0.52` | Used only when the reranker is unavailable. |
| `MAX_RETRIEVAL_ATTEMPTS` / `MAX_WEB_ATTEMPTS` | `1` / `2` | Search rounds per source. |
| `CITATION_PRECHECK` | `True` | Check answers' citations in code first; skip the 120B check when they hold up (document answers: only when the evidence zone is high; web answers: only when the answer doesn't say the results miss part of the question). |
| `VERIFY_DECISIONS` / `MAX_REVIEW_ROUNDS` / `REVIEW_ONLY_UNCERTAIN` | `True` / `2` / `True` | 120B reviews the 20B's uncertain decisions. |
| `WEB_RESULTS` / `TAVILY_SEARCH_DEPTH` / `WEB_CACHE_TTL_SECONDS` | `5` / `basic` / `3600` | Web search. |
| `HISTORY_TURNS` | `6` | Past messages passed to the agent. |
| `DATABASE_PATH` | `backend/data/app.db` | SQLite file. |
| `SIGNUP_CODE` | `""` | Invite code for every sign-up after the first; empty: sign-ups closed. |
| `LIBRARY_DATA_DIR` | `backend/data` | The library's files, versions, fonts and logs (§22). |
| `LIBRARY_AGENT_MODEL` / `LIBRARY_BACKUP_MODEL` / `LIBRARY_AGENT_REASONING` | `openai/gpt-oss-120b` / `openai/gpt-oss-20b` / `low` | The document agent, and the model that takes over when it is rate-limited. |
| `LIBRARY_TPM_LIMIT` / `LIBRARY_BACKUP_TPM_LIMIT` | `8000` / `8000` | Groq tokens per minute the queue paces itself to. |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Allowed origins (any localhost port is also allowed). |
| `BOT_CONTACT` | `https://github.com/` | Contact in the User-Agent (required by Wikipedia's API). |

Frontend: `VITE_API_URL` (optional) overrides the API base URL; by default the app calls `/api` and the Vite dev
server proxies it to `http://127.0.0.1:8000`. In production the backend serves the build itself (`backend/web.py`).

---

## 6. Project structure and file guide

### 6.1 Overview

```
day7/
├── app.py, requirements.txt, README.md, claude_wiki.docx
├── docs/                 documentation (this file)
├── backend/              FastAPI app, LangGraph agent, retrieval, memory, tests
│   ├── app.py, web.py    the FastAPI app; serving the built front end
│   ├── api/              HTTP layer: auth, chat, documents, health
│   ├── library/          the document library: catalog, agent, rename/edit/undo, PDF editing, queue (§22)
│   ├── config/           settings, model clients, token accounting
│   ├── models/           SQLite data access and API schemas
│   ├── graph/            the agent: state, nodes, edges
│   ├── tools/            what the agent's nodes call to write answers and save memories
│   ├── rag/              ingestion, vector store, hybrid search, reranking
│   ├── memory/           conversation memory (Voyage + Qdrant)
│   ├── evaluation/       answer checks, LLM grading, decision reviews
│   ├── scripts/          one-off maintenance scripts
│   ├── tests/            unit tests (no API calls)
│   └── data/ vector_db/  created at runtime (not committed)
├── frontend/             React + Vite + Tailwind single-page app
├── benchmark/            benchmark scripts, question sets, corpus and saved results
└── scripts/experiments/  one-off model experiments (call the live APIs)
```

Most backend folders contain an empty `__init__.py`; it only lets Python import the folder as a package. The exceptions are `backend/tools/__init__.py` (shared helpers, below) and
`scripts/experiments/__init__.py` (a docstring on how to run the experiments).

### 6.2 What each folder holds and what each file does

#### Project root

| File | What it does |
|---|---|
| `app.py` | Re-exports `backend/app.py`, so `uvicorn app:app --reload` works from the project root. |
| `requirements.txt` | Python dependencies for the backend, benchmarks and tests. |
| `README.md` | Quick start, workflow summary, headline measurements, API summary. |
| `claude_wiki.docx` | A Word document in the benchmark corpus (exercises DOCX ingestion). |
| `.gitignore` | Keeps secrets (`.env`), runtime data, third-party PDFs, `node_modules` and build output out of git. |
| `.env` | API keys and overrides (not committed; see §4 and §5). |

#### `docs/`

| File | What it does |
|---|---|
| `DOCUMENTATION.md` | This file: the current technical documentation. |

#### `backend/`

| File | What it does |
|---|---|
| `app.py` | The FastAPI application: checks required keys at startup, creates the database and Qdrant collections, starts the library's indexing queue and batch worker, sets CORS, mounts the routers and, when `frontend/dist` exists, the front end. |
| `web.py` | Serves `frontend/dist`: `index.html` with `no-cache` (also for the app's own routes such as `/documents`), hashed `assets/` cached for a year, a JSON 404 for unknown `/api` paths, nothing outside the folder. |

#### `backend/api/` — the HTTP layer

| File | What it does |
|---|---|
| `dependencies.py` | `get_current_user`: reads the login cookie on each request and returns the user, or a 401. |
| `routes/auth.py` | `GET /auth/state`, `POST /auth/signup`, `/auth/login`, `/auth/logout`, `GET /auth/me`, on `backend/library/auth.py`. |
| `routes/chat.py` | `POST /chat` and `POST /chat/stream` (SSE): loads history, facts, past-conversation summaries and document names, runs the agent in its own thread, saves the answer, and queues memory work and token logging in the background. Document requests go to the `manage` node (the document agent). Also lists, renames and deletes conversations, lists the documents a conversation changed and its queued tasks, and serves `/stats`. |
| `routes/documents.py` | Lists (with the edit Undo and Redo apply to), uploads (size check, dedup by hash; indexing on the library's queue) and deletes documents. |
| `routes/health.py` | `GET /health`: reports the configured models and web-search providers. |

#### `backend/library/` — the document library (§22)

| File | What it does |
|---|---|
| `api.py` | `/api/library/*`: rename, undo, redo, file / PDF / preview downloads, the queue and batch cancel; starts and stops the library's workers. |
| `auth.py` | Accounts: PBKDF2 passwords, login tokens (only their hashes stored), throttling of wrong passwords, the invite code (§13). |
| `catalog.py` | SQLite tables of the library (documents, renames, edits, undone edits, batches, tasks, agent sessions, users, login tokens) in `app.db`. |
| `library.py` | Add a file, index it (queue), title it, find a document by name or content, rename (file, catalog and chunks at once), delete. |
| `titles.py` | A title, summary and keywords for each new document, written by the model from its opening text. |
| `naming.py` | Cleaning titles, stored file names, and how closely a phrase matches a document's name. |
| `agent.py` | The document agent: the 120B with tools (find, details, list, rename, edit, undo / redo, queue) and a guard in code that decides when a change may happen and asks the user first when it must. |
| `editing.py` | Replace or append text in TXT / MD / DOCX / PDF, re-index, keep backups; undo and redo. |
| `pdf_edit.py` | In-place PDF editing: finds lines and paragraphs, re-wraps in the document's own fonts, moves content below (onto a new page if needed), and checks the result before saving. |
| `tiny_font.py` | A font cut down to the few letters an edit needs (fontTools), so a PDF grows by kilobytes, not a whole font. |
| `font_fetch.py` | Fonts a PDF needs and the machine lacks: one pinned HTTPS URL each, verified against `fonts/manifest.json` (SHA-256), cached in `data/fonts/fetched/`. |
| `batch.py` | The queue: many changes run one at a time by a background worker, within Groq's tokens-per-minute limit; restart-safe. |
| `limits.py` | Groq's tokens-per-minute window, shared by the chat and the queue. |
| `activity.py` | Which documents a conversation changed (its replies' and queued tasks' actions), for the chat's side panel. |
| `export.py` | A document's text laid out as a PDF for download. |
| `logs.py` | `data/logs/events.log` and `all.log`. |
| `config.py` | The library's constants and its `library_*` settings. |

#### `backend/config/` — settings and model clients

| File | What it does |
|---|---|
| `settings.py` | Every setting (keys, models, chunking, retrieval, reranker thresholds, budgets, paths), loaded from `.env`; full list in §5. |
| `llm.py` | One client per model role (120B writer and judge, 20B router, Qwen memory, 20B backup writer), key rotation across `GROQ_API_KEY`…`_4`, the rate-limit fallback to the backup writer, and `structured()` for schema-validated output, including recovery of misnamed tool calls. |
| `voyage.py` | The one Voyage client, behind a shared rate limit with priorities: reranking first, then memory recall, then episode saves (§10). |
| `usage.py` | `UsageTracker` counts tokens per model and per step during a request; `record()` writes the request's line to `backend/data/token_usage.jsonl`. |

#### `backend/models/` — data storage and API shapes

| File | What it does |
|---|---|
| `database.py` | SQLite schema, migrations and every query: users, facts, documents, conversations, messages and stats, all filtered by user. |
| `schemas.py` | Pydantic models for requests and responses (`Credentials`, `DocumentOut`, `ChatRequest`, `ChatResponse`, …). |

#### `backend/graph/` — the agent

| File | What it does |
|---|---|
| `state.py` | `AgentState`: every field the nodes read and write (§7.6). |
| `nodes.py` | Every graph node (router, greeting, direct, rewrite, retrieve, grade, generate, web search, evaluate, fallback, finalize), the router and rewrite schemas, the source order per preference, and the fixed notices. |
| `workflow.py` | Builds the LangGraph graph: the edges and routing functions between nodes, retry budgets, and `run_agent` / `stream_agent`, which run it and turn its progress into stream events. |

#### `backend/tools/` — what the nodes call

| File | What it does |
|---|---|
| `__init__.py` | Shared helpers: shortening chat history for prompts, today's date, cleaning stray citation marks, and the shared formatting rules. |
| `direct_answer.py` | The knowledge gate (can the model answer this from its own knowledge?), the two-part direct-answer prompt, and `converse` for small talk. |
| `greeting.py` | Small-talk entry point; hands off to `direct_answer.converse` so every reply has the same voice. |
| `rag.py` | Formats the selected passages as numbered evidence, writes the cited document answer, and turns passages into source cards. |
| `web_search.py` | Web search through Tavily → DuckDuckGo → Wikipedia (recency handling, one-hour cache), the web-answer prompt, and source cards. |
| `memory.py` | Saves facts about the user (cheap pre-check, Qwen extraction, merge rules in code) and writes the short summary of each exchange. |

#### `backend/rag/` — documents in, passages out

| File | What it does |
|---|---|
| `ingestion.py` | Loads PDF/DOCX/TXT/MD files, cleans and chunks the text, embeds it (paced for the free tier) and stores it in Qdrant (§9). |
| `vectorstore.py` | Qdrant client (embedded or server), Gemini embeddings, the collection with dense + BM25 vectors, per-user filters, search and delete. |
| `sparse.py` | Tokenizer and BM25 sparse vectors for keyword search (the citation check reuses the tokenizer). |
| `embedding_cache.py` | SQLite cache of question embeddings, to save Gemini's daily quota. |
| `retrieval.py` | Multi-query hybrid search (dense + BM25) fused with Reciprocal Rank Fusion, scoped to the user (§10). |
| `rerank.py` | Scores candidates with Voyage `rerank-2.5`, maps the best score to a confidence zone, and picks diverse passages with MMR. |

#### `backend/memory/` — conversation memory

| File | What it does |
|---|---|
| `embeddings.py` | Embedding helpers for memory texts (save priority) and queries (recall priority), through `config/voyage.py`. |
| `store.py` | The `user_memories` Qdrant collection: save an exchange summary, find the closest ones for a new message, delete a conversation's memories. |

#### `backend/evaluation/` — checking answers and decisions

| File | What it does |
|---|---|
| `citations.py` | Model-free answer check: every claim cited, every `[n]` real, cited passages contain the claim's key words and numbers (§21.9). |
| `evaluator.py` | LLM passage grading (used only when no reranker can score) and the 120B answer evaluation (sufficient, grounded, score, missing). |
| `reviewer.py` | The review loop: the 20B proposes a decision; when it isn't confident, the 120B approves it or sends feedback for another try. |

#### `backend/scripts/`

| File | What it does |
|---|---|
| `migrate_hybrid.py` | One-off migration that copied the old dense-only collection into the hybrid (dense + BM25) collection without re-embedding. |
| `merge_library.py` | One-off merge of the renaming pipeline's data into this app (users, documents with their vectors, chats). |
| `recount_pages.py` | Recounts the pages of Word, text and Markdown documents. |
| `build_font_manifest.py` | Writes `backend/library/fonts/manifest.json` from hand-checked font downloads. |

#### `backend/tests/` — unit tests and reasoning cases

| File | What it does |
|---|---|
| `test_routing.py` | Source order, retry budgets, partial RAG answers, unchecked answers kept, web query rules, knowledge-gate rules, memory pre-check, tool-call recovery, prompt trims. |
| `test_memory.py` | Key normalisation, merge rules (replace / collect / forget / corrections), storage, the one-time migration (temporary database). |
| `test_citations.py` | The code citation check. |
| `test_rerank.py` | Reranker zones and the fallback when Voyage can't score. |
| `test_conversations.py` | Renaming chats; `/stats` from the message columns and the one-time backfill; a fresh database has no old `user_memory` table. |
| `test_documents.py` | Uploads are titled, stored in the user's folder and indexed in the background; failed / removed-while-waiting / uploaded-twice outcomes; indexing cut off by a restart is resumed. |
| `test_stream.py` | The SSE stream ends with `final`, and the answer is saved even if the client disconnects. |
| `library/` | The document library: accounts, the agent and its guard, routing to it, renames, edits, undo / redo, the queue, PDF editing (layout, growth onto a new page, fonts), the API and the Documents page's routes. |

#### Runtime folders (created automatically, not committed)

| Folder | Contents |
|---|---|
| `backend/data/` | `app.db` (SQLite), `embedding_cache.db`, `token_usage.jsonl`; `library/`, `versions/`, `fonts/`, `logs/` (§4). |
| `backend/vector_db/qdrant_data/` | Embedded Qdrant storage (same note). |

#### `frontend/` — the web app

| File | What it does |
|---|---|
| `index.html` | App entry page; applies the saved theme before the first paint and loads `src/main.jsx`. |
| `preview.html` | Entry page for the design preview (dev server only). |
| `package.json`, `package-lock.json` | Dependencies (React, react-markdown, lucide-react, Vite, Tailwind) and the `dev` / `build` / `preview` scripts. |
| `vite.config.js` | Vite setup and the dev proxy from `/api` to `http://127.0.0.1:8000`. |
| `tailwind.config.js`, `postcss.config.js` | Tailwind theme (colours from CSS variables, type scale, fonts) and the PostCSS pipeline. |
| `public/mark.svg` | Favicon / logo. |

#### `frontend/src/`

| File | What it does |
|---|---|
| `main.jsx` | Mounts `App` into the page. |
| `App.jsx` | Top-level shell: session restore, health polling, routing between pages, sidebar layout (top bar on phones). |
| `index.css` | Colour tokens for light and dark themes, component classes and Markdown styles. |
| `services/api.jsx` | HTTP client: requests with the login cookie, error handling (401 → login), the SSE stream reader, document actions (rename, undo, redo, queue). |
| `state/chatDocuments.js` | The documents a conversation changed, re-read after each answer and while its queued tasks run. |
| `state/previewStore.js` | The one open document preview. |
| `lib/chatActions.js` | What a chat's document actions allow (which edits Undo / Redo apply to) and their descriptions. |
| `lib/queue.js` | The queue panel's figures and labels. |
| `lib/documentFiles.js` | Where a document's files are served (preview, download, PDF). |
| `lib/pdfViewer.js` | PDF viewer on pdf.js in the site's own look: pan, zoom, selectable text. |
| `state/chatStore.js` | Conversations and in-flight answers kept outside the pages, so an answer keeps streaming when you switch pages or chats. |
| `lib/route.js` | Hash routes (`#/chat`, `#/chat/<id>`, `#/documents`, `#/overview`). |
| `lib/format.js` | Formatting helpers: counts, sizes, times, percentages, recency groups, source labels. |
| `lib/theme.js` | `useTheme()`: System / Light / Dark, saved in `localStorage`. |
| `pages/Login.jsx` | Sign in / sign up, with an offline notice. |
| `pages/Chat.jsx` | New-chat screen with suggestions, or the conversation and composer. |
| `pages/Documents.jsx` | Upload (drop zone, type/size checks, progress); the document list with Preview, Rename, Undo edit, Redo, Download, PDF and Remove; the queue panel. |
| `pages/Overview.jsx` | Usage numbers, where answers came from, document library. |
| `components/Sidebar.jsx` | New chat, chats by recency (rename, delete), navigation, theme switch, log out. |
| `components/Message.jsx` | One message: notices, Markdown with citation marks, provenance line, **Sources** and **Steps**, live progress while answering. |
| `components/InputBox.jsx` | The message box (Enter sends, Shift/Ctrl+Enter adds a line; drafts kept per chat). |
| `components/Mark.jsx` | The logo as a React component. |
| `components/DocumentActions.jsx` | Under an answer from the document agent: what it did, with Preview, Undo and Redo. |
| `components/ChatDocuments.jsx` | The chat's side panel: documents this conversation changed. |
| `components/DocumentPreview.jsx` | The document preview (PDF viewer). |
| `components/DocumentLinks.jsx` | Download and PDF links for a document. |
| `components/ConfirmAction.jsx` | A link that asks first ("Undo this edit?") before it acts. |
| `components/RenameAction.jsx` | Inline rename field; asks before using a name another document has. |
| `components/QueuePanel.jsx` | Queued tasks with their state and replies, and Cancel. |
| `dev/preview.jsx` | Design preview: runs the real app with the API replaced by sample data. |
| `dev/fixtures.js` | Sample documents, chats and stats for the preview. |

#### `benchmark/` — measuring the system

Scripts (each has a docstring with usage; the retrieval ones need the backend stopped, see §17):

| File | What it does |
|---|---|
| `common.py` | Shared helpers: the benchmark user, indexing the corpus through the real ingestion, relevance matching. |
| `build_wiki_corpus.py` | Downloads the Wikipedia articles in `corpus/wikipedia/` through Wikipedia's API. |
| `run_retrieval.py` | Retrieval stages (dense, hybrid, reranked): hit@k, MRR, out-of-scope detection. No Groq calls. |
| `run_selection.py` | `--collect` saves 20 reranked candidates per question; `--compare` tests passage-selection methods offline. |
| `calibrate_reranker.py` | Rescores the saved candidates with Voyage vs the local reranker and sets the zone thresholds. |
| `run_e2e.py` | Full answers through the agent: source used, gold phrase found, evaluator verdict, latency, tokens. |
| `run_perf.py` | Startup time, memory, reranking / MMR / search latency and concurrency. No API quota. |
| `run_router.py` | Runs the router on 80 differently worded messages. |
| `run_direct_gate.py` | Router + knowledge gate on questions that should or shouldn't be answered from model knowledge. |
| `run_direct_prompt.py` | Checks that the split direct-answer prompt keeps its behaviour, and measures the token saving. |
| `run_memory.py` | Memory conversations on a temporary database: facts saved, updated, forgotten, no false saves. |
| `run_checker.py` | Code citation check vs the 120B answer check, plus deliberately broken answers. |
| `run_web_prompt.py` | Old vs new web-answer prompt on the same saved search results. |
| `run_web_checker.py` | Code citation check vs the 120B answer check on web answers. |
| `run_identity.py` | Does the assistant keep its underlying model private? |

Data:

| File / folder | What it holds |
|---|---|
| `questions.json` | The 74 retrieval questions (67 answerable, 7 out of scope) with their relevant passages and answer shapes. |
| `router_cases.json` | The 80 router messages, the expected source for each, and the uploaded document names they assume. |
| `memory_cases.json` | The 18 memory conversations and the facts expected afterwards. |
| `reasoning_cases.py` / `run_reasoning_baseline.py` | 16 direct-answer reasoning cases (opinions, follow-ups, challenges) with the expected behaviour, and a script that runs them through `direct_answer.answer` (calls the API); saved answers in `results/reasoning_baseline_results*.json`. |
| `corpus/wikipedia/*.md` | 18 Wikipedia articles on information retrieval and NLP (CC BY-SA 4.0, source URL in each file). The corpus PDFs are not committed (see the README). |
| `results/` | Saved output of every benchmark run, named after its script: `retrieval_stage*`, `selection_*` (including the saved candidate pools `selection_pool.json` / `.npz`), `rerank_calibration`, `e2e_stage*`, `perf`, `router_eval*`, `direct_gate_*`, `gate_*`, `direct_prompt_*`, `memory_eval*`, `checker_eval*`, `web_prompt_*`. Files ending in `_before` or `_old` keep the run or prompt from before a change. |

#### `scripts/experiments/` — one-off model experiments

All of these call the live APIs; run them from the project root, e.g. `python -m scripts.experiments.test_gate`.

| File | What it does |
|---|---|
| `test_gate.py` | Runs the knowledge gate on five sample questions (easy, borderline, an invented paper) and prints its verdicts. |
| `promptcaching.py` | Sends the same long routing prompt to Groq several times and prints how many tokens were cached. |
| `token_usage.py` | Prints Groq's token counts for a long routing prompt and its review follow-ups. |
| `nemotronsmoketest.py` | One-question smoke test of Nemotron Ultra through OpenRouter; self-contained (needs `pip install langchain-openai` and `OPEN_ROUTER_API_KEY`). |

---

## 7. The agent workflow

The agent is a LangGraph `StateGraph` defined in `backend/graph/workflow.py`. Nodes live in
`backend/graph/nodes.py`; each node takes the current `AgentState` and returns a partial update.

### 7.1 Graph

```
START → router ─┬─ small talk ──────────────────────────────────────────────► greeting ► END
                ├─ model knowledge / auto ─► direct ─(gate passes)─► finalize ► END
                │                               └─(gate refuses)─► fallback
                ├─ documents ─► [rewrite if vague] ─► retrieve ─► grade (reranker zone)
                │        high ─► generate_rag ─► evaluate ─► finalize ► END
                │        mid  ─► rewrite ─► retrieve (once more)
                │        low  ─► fallback
                └─ web ─► web_search ─► generate_web ─► evaluate ─(short)─► rewrite ─► web_search
  fallback ──► next untried source for the user's preference ──► … ──(all tried)► best answer (finalize) or no_results
```

### 7.2 Source order

The router decides the user's **source preference**; `nodes._source_order` turns it into an order:

| Preference | Order | Router's first source |
|---|---|---|
| `auto` | model knowledge → documents → web | `direct` |
| `documents` | documents → model knowledge → web | `rag` (or `direct` with a notice if no documents) |
| `model_knowledge` | model knowledge → documents → web | `direct` |
| `web` | web → documents → model knowledge | `web_search` |

Documents are skipped when the user has none. `auto` starts with model knowledge on purpose: starting with the
documents whenever any were uploaded sent casual and philosophical questions into RAG. Questions about the user's
own material still get there, because the knowledge gate refuses them and the fallback tries the documents next.

`fallback_node` is the single place that moves between sources: it records the tried source, clears that source's
state (documents, attempts, …), and adds a fixed notice such as *"Information matching your question wasn't found in
your documents, so this answer comes from the model's own knowledge."* When every source has been tried, it returns
the best candidate seen so far (`finalize`, with a caveat) or `no_results`.

### 7.3 Nodes

| Node | Model | Purpose |
|---|---|---|
| `router` | 20B (120B reviews if unsure) | `Route`: small talk vs information request, source preference, clarity, standalone query, confidence, and `confidence_required` (whether a direct answer must pass the knowledge gate). A message that explicitly asks to read the user's own material ("according to my documents", "what do my notes say", "the paper I uploaded", or an uploaded file's name) always goes to the documents, whatever the router chose (`asks_for_documents`); "without my notes" and similar don't count. |
| `greeting` | 120B, temperature 0.8 | Small talk via `direct_answer.converse`, same persona as every answer. |
| `direct` | 120B | Knowledge gate (when `confidence_required`), then the answer. A refusal leaves the answer empty → `fallback`. |
| `rewrite` | 20B (reviewed if unsure) | Rewords and expands the query, using the reason and previous attempts. |
| `retrieve` | — | Hybrid search (dense + BM25, RRF) scoped to the user; marks off-topic or failed searches. |
| `grade` | Voyage reranker | Scores candidates → zone (high / mid / low, on that reranker's scale) → MMR picks diverse passages. If no reranker can score: LLM grading of the top 8. |
| `generate_rag` | 120B | Grounded answer with `[n]` citations. The `NO_ANSWER_IN_DOCUMENTS` code word means "nothing" only when it is (almost) the whole reply; a partial answer is kept. |
| `web_search` | — | Up to 2 queries, deduplicated by URL. |
| `generate_web` | 120B | Answer from web results with citations. |
| `evaluate` | code, then 120B | Document answers with a high evidence zone, and web answers, are checked in code first (`evaluation/citations.py`); a web answer that says the results don't cover part of the question always goes to the 120B. If every claim is cited and found in its passage, the evaluation is `checked_by: "code"` and the model is not called. Otherwise the 120B returns `sufficient`, `grounded`, `score`, `missing`, checked against only the passages the answer cites (all of them if it cites none); keeps the best candidate. If no model can check the answer, it is kept with `verified: false` in its evaluation and a notice. |
| `fallback` | — | Next source (see 7.2). |
| `no_results` | — | Honest "couldn't find a reliable answer" message. |
| `finalize` | — | Returns the answer, the gate-approved direct answer, or the best candidate with a caveat. |

### 7.4 Knowledge gate (`tools/direct_answer.py`)

`KnowledgeGate` fields: `question_kind` (`factual` | `open`), `reason`, `time_sensitive`, `references_user_material`,
`confidence`. The gate sees the last 6 messages capped at 600 characters each (like the router), so a follow-up
after long answers stays cheap. The rule (`_gate_passes`):

1. Refuse if `time_sensitive` or `references_user_material`.
2. Otherwise pass if the question is `open` (opinions, philosophy, ethics, hypotheticals — no source could settle them),
   or if it is `factual` and confidence is `high`.

Before `question_kind` existed, the gate asked "are you sure you're right?" about opinion questions, answered
"medium", and sent them to documents and the web (benchmark: 3 of 18 answered directly; now 18 of 18, with all
4 controls still refused). The prompt holds the rules once; the output fields' descriptions only name them,
except `confidence`, kept in full (§21.13).

**Answer prompt.** The direct-answer instructions have two parts (`system_prompt(open_question)`): a core part
sent with every direct answer and small-talk reply (accuracy, judgment, follow-ups, style, format; ~480 tokens),
and an open-question part (~530 tokens: taking positions, role-play perspective, personal scenarios, pushback)
added when the gate says `open`, or when the router judged the question subjective and skipped the gate. Small
talk gets the core part only. The memory note tells the model saved memories describe the user, never a
character it is asked to play.

### 7.5 Routing functions (`workflow.py`)

| Function | Decision |
|---|---|
| `route_after_router` | greeting → `greeting`; direct → `direct`; unclear rag/web query → `rewrite`; else the source's first node. |
| `route_after_direct` | Answer → `finalize`; gate refused → `fallback`. |
| `route_after_rewrite` | `web_search` or `retrieve` depending on intent. |
| `route_after_retrieve` | Off-topic or failed search → `fallback`; else `grade`. |
| `route_after_grade` | high → `generate_rag`; mid with attempts < budget + `WEAK_MATCH_RETRIES` → `rewrite`; else `fallback`. |
| `route_after_generate_rag` | Answer → `evaluate`; none → `rewrite` (budget left) or `fallback`. |
| `route_after_web` | Results → `generate_web`; none → `rewrite` or `fallback`. |
| `route_after_evaluate` | Sufficient & grounded → `finalize`; weak rag/web with budget → `rewrite`; else `fallback`. |
| `route_after_fallback` | Next source's first node; all tried → `finalize` (if a candidate exists) or `no_results`. |

Loops end because each search increments `attempts`, compared with the source's budget (`_budget`); a
`recursion_limit` of 60 is an extra safeguard.

### 7.6 State (`graph/state.py`)

`AgentState` is a `TypedDict`. Two keys accumulate (`Annotated[list, operator.add]`): `trace` and `rewritten_queries`.

| Group | Keys |
|---|---|
| Inputs | `query`, `user_id`, `username`, `history`, `memories`, `doc_names` |
| Routing | `intent`, `source_preference`, `standalone_query`, `query_is_clear`, `confidence_required`, `route_reason`, `tried_sources`, `notices` |
| Query loop | `search_queries`, `rewritten_queries`, `rewrite_reason`, `attempts` |
| Evidence | `documents`, `docs_off_topic`, `docs_search_failed`, `match_zone`, `web_results` |
| Output | `answer`, `tool`, `sources`, `evaluation`, `best`, `trace` |

### 7.7 Notices

Fixed sentences shown above an answer: moving between sources (`FALLBACK_NOTICES`), no documents uploaded
(`NO_DOCS_NOTICE`), the backup model wrote the answer (`BACKUP_NOTICE`), and the answer couldn't be checked
(`UNVERIFIED_NOTICE`).

### 7.8 Structured output

Decisions use `structured(ModelClass)` from `config/llm.py`. gpt-oss models use Groq's strict `json_schema` mode
(constrained decoding, always schema-valid, so no repeat calls); other models use function calling. A plain-JSON
fallback validated with pydantic covers the rest; it sends the full JSON schema, including nested definitions. When
Qwen files a correct answer under a misspelled tool name, Groq rejects the call but returns the generation;
`_recover_tool_call` reads the arguments from it instead of losing them. Schemas: `Route`, `Rewrite`, `KnowledgeGate`, `DocGrades`,
`AnswerEval`, `Review`, `MemoryExtraction`.

---

## 8. Memory

Two kinds, kept separate on purpose:

| | Facts | Episodes |
|---|---|---|
| What | Specific details about the user: name, location, hobbies, preferences, how they want answers | A one- or two-sentence summary of an answered exchange |
| Stored in | SQLite `user_facts`, one row per value | Qdrant collection `user_memories`, Voyage embeddings |
| Written by | `tools/memory.extract_and_save_batch` (batched, `memory/batching.py`) | `tools/memory.summarize_exchange` + `memory/store.save_memory` |
| Read | Every request (`db.list_memories`) | Up to 3 closest (score ≥ 0.45), each cut to 800 chars; only the answer writer gets them, unless the message refers back to a past conversation (§21.17) |

**Fact rules.** A cheap pre-check (`_may_contain_memory`) skips the model for messages that can't hold a fact.
Memory commands ("remember", "forget", "call me", "from now on") always go through. Otherwise each clause is checked
for "I", "my", "keep your answers…" and similar cues, but clauses that are questions, hypotheticals or requests
("how do I…", "what's my…", "if I were…", "give me…") are skipped (§21.12). Otherwise Qwen reports only what to *remember* or
*forget*; `plan_changes` decides in code how storage changes:

- single-value keys (`preferred_name`, `location`, `occupation`, …, and any `favorite_*`, `preferred_*`, `current_*`) —
  a new value replaces the old;
- other keys (`hobbies`, `liked_foods`, …) — values are collected;
- forget — removes a value, or the whole key for `*`. A correction ("I don't play futsal any more") is forget + remember.

Keys are normalised (snake_case, one spelling of "favourite", aliases such as `city` → `location`). Values compare
case-insensitively. Deleting a conversation also deletes its episodes.

Memory work runs **after** the reply, on a single background worker, so it adds no waiting time and two quick
messages can't update the same keys at once.

**Batched extraction** (`memory/batching.py`). Messages that pass the pre-check wait per user and are extracted
together in one memory-model call when the user pauses (`MEMORY_BATCH_IDLE_SECONDS`, 90), when `MEMORY_BATCH_MAX` (3)
are waiting, or before the oldest one would leave the chat history the answer writer sees. A request in another
conversation first waits (up to 30 s) for the user's pending facts to be saved; on shutdown everything pending is
extracted. One waiting message uses the single-message prompt unchanged; if a batch call fails, each message is
extracted on its own. Batched calls are logged as their own `memory_batch` line in the token log.

**One retry.** A message reaches the memory model only after passing the pre-check, so an empty result is often a
miss: Qwen intermittently answers without calling its tool, or returns an empty list that a repeat fills. Such a
call is repeated once, logged as the step `memory_extract_retry`. On the 18 memory conversations (14 November)
it fired once, on the turn behind the earlier intermittent miss, and found the fact: 18/18 cases and 20/20 facts
(before: 17/18, 19/20), batched 18/18 and 20/20 (before: 17/18, 16/20); no retry came back empty.

The original one-row-per-key `user_memory` table was migrated into `user_facts` once (recorded in `migrations` as
`user_facts_v1`) and kept, unused, as a backup in databases from before then. New databases don't get the table;
the migration is just marked done.

---

## 9. Ingestion pipeline

`POST /api/documents/upload` → `routes/documents.py::upload_document` → `library.add_file`, which returns as soon as
the file is saved (`status: "processing"`); `library.process` then runs on the library's one indexing worker, one file
at a time (the embedded Qdrant store is not safe for concurrent writers). The Documents page checks `GET /documents`
every 2 s while a file is still processing. Steps 4–8 run in the background.

1. **Validate** — extension in `{.pdf, .docx, .txt, .md}`; size ≤ `MAX_UPLOAD_MB` (reads at most limit+1 bytes).
2. **Deduplicate** — SHA-256 of the bytes, against the file as it is now and as it was uploaded (an edited document's
   original is still a duplicate). Already there → `status: "duplicate"` and no second job. A failed copy is removed
   and indexed again.
3. **Record** — a `documents` row with `status="processing"`, a provisional title from the file name, and the file
   saved in `backend/data/library/<user_id>/`. Then, on the worker, the model writes a title, a summary and keywords
   from the opening text (`library/titles.py`), and the stored file is renamed after the title.
4. **Load** — PDF: one document per page (page numbers kept for citations); DOCX: paragraphs + table rows; TXT/MD as-is.
   `_clean` removes null bytes, re-joins hyphenated line breaks, collapses whitespace. No text → `IngestionError`: the document is marked failed with that message.
5. **Chunk** — 1,000 chars / 200 overlap, splitting on paragraphs → lines → sentences → words; chunks ≤ 20 chars dropped.
6. **Embed** — `embed_paced`: Gemini `RETRIEVAL_DOCUMENT`, batches of 90, waiting when the free tier's per-minute limit is hit.
7. **Upsert** — Qdrant points with ids `uuid5(doc_id:chunk_index)`, a `dense` vector and a `bm25` sparse vector, and
   payload `{page_content, metadata: {user_id, doc_id, source, title, file_name, page, chunk_index, start_index}}`.
8. **Finish** — `indexed` with page/chunk counts, or `failed` with the error (partial vectors removed). A document
   removed while it was being indexed has its vectors removed when indexing ends.

Deleting a document removes its vectors, the stored file, its versions and its rows. Files still processing when the
server stops are indexed again at the next start.

---

## 10. Retrieval

`rag/retrieval.py::retrieve(queries, user_id, k, min_score, mode)`:

1. Deduplicate the query variants; embed them in parallel through the embedding cache.
2. For each variant, run dense search and BM25 search (`HYBRID_CANDIDATES` each), filtered to `metadata.user_id`.
3. Fuse with Reciprocal Rank Fusion: each chunk earns `1/(RRF_K + rank)` per list.
4. Keyword hits keep their place even with low cosine (exact terms are the point of BM25); dense-only hits must pass
   `MIN_RELEVANCE_SCORE`. If embedding fails, keyword search alone is used.

Then `rag/rerank.py` scores each candidate (0–1) with Voyage `rerank-2.5` (the local MiniLM cross-encoder it
replaced was removed on 14 November). If Voyage can't score (rate-limited, down, no key), `score()` returns `None` and the LLM relevance check grades the top
8 candidates. The best score sets the **zone** (≥ high → answer; < low → next
source; between → one reworded retry); MMR picks `RETRIEVAL_K` diverse passages from the top `RERANK_TOP_N`.

**One Voyage limit** (`config/voyage.py`). Reranking, conversation-memory recall and episode saves share one Voyage
account, whose free tier allows 3 requests a minute in total. They used to have a client each and didn't know about
one another, so a recall and a save could use up the minute and the next rerank fell back to the LLM check. Now every
call takes a slot from one limiter, by priority: the reranker waits up to 10 s; recall (3 s) and background saves
(120 s, on their own worker so fact extraction isn't held up) only take a slot while another stays free, and never
ahead of a waiting rerank.

**Reranker calibration** (`benchmark/calibrate_reranker.py`: the same 20 saved candidates per question, so the
rerankers are compared on identical input).

All 74 benchmark questions (67 answerable, 7 out of scope):

| Reranker | Top-1 | Top-3 | Top-5 | MRR@10 | Routing with its thresholds |
|---|---|---|---|---|---|
| local MiniLM (0.05 / 0.01) | 76% | 87% | 92% | 0.830 | 2 answerable questions skipped the documents |
| Voyage `rerank-2.5` (0.55 / 0.45) | **88%** | **97%** | **100%** | **0.930** | all 67 answered from the documents; 5/7 out-of-scope skipped, 1 retried once, 1 answered |

"Top-1" counts the first relevant passage; for summaries and multi-part questions several passages are relevant and
MMR passes six to the writer, so top-5 is what decides whether the answer has its evidence. The one out-of-scope
question both rerankers let through ("Who made the first ascent of K2?") matched a passage about Everest's first
ascent; the writer's "not in the documents" check and the answer checker are the safety nets there.

On the first 35 questions, `rerank-2.5` also beat `rerank-2.5-lite` (top-1 100% vs 97%) at the same token cost
(~3,500–3,700 Voyage tokens and ~0.6 s per question).

Measured on the benchmark: MRR 0.774 (dense only) → 0.822 (hybrid) → 0.872 (reranked), with every out-of-scope
question recognised.

---

## 11. Token accounting

`config/usage.py`: a `UsageTracker` (LangChain callback) is attached to each request. Every model call inside the
graph inherits it; calls outside the graph name their step with `usage_step` metadata (`memory_extract`,
`episode_summary`, `knowledge_gate`; before 14 November the gate was counted under `direct`, together with
the answer). It sums input, output and hidden-reasoning tokens per model and per step.

- Each answer stores its own usage in `messages.meta.usage`.
- Input tokens served from Groq's prompt cache are recorded as `cached` (half price, and not counted toward Groq's
  rate limits). Caching is automatic for gpt-oss and only matches the start of a prompt, so every prompt keeps its
  fixed instructions first; the answer instructions (~1,030 tokens) are the only prompt long enough to be cached.
- After memory work finishes, `record()` logs one line and appends a record to `backend/data/token_usage.jsonl`
  with the routing context (message, source preference, route reason, path, latency), so expensive or misrouted
  questions can be found.

---

## 12. Backend module reference

### `app.py`
Adds the project root to `sys.path`; `lifespan` checks the required keys, runs `init_db()` (and migrations), opens the
Qdrant collection, starts the library (`library/api.startup`: logs, unfinished indexing, the batch worker), and on
shutdown stops it and extracts any facts still waiting in the memory batch. Mounts `frontend/dist` when it exists. CORS: `CORS_ORIGINS` plus any localhost port.

### `config/llm.py`
| Function | Description |
|---|---|
| `get_llm(temperature=None)` | 120B for answers (one cached client per temperature). |
| `get_fast_llm()` | 20B, temperature 0, low reasoning — routing and rewrites. |
| `get_judge_llm()` | 120B, temperature 0, low reasoning — gate, reviews, evaluation. |
| `get_memory_llm()` | Qwen — memory. |
| `get_backup_writer(temperature=None)` | 20B set up for writing, when the 120B is rate-limited. |
| `invoke_answer(messages, temperature=None)` | Writes with the 120B, falling back to the backup writer on a rate limit. |
| `consume_backup_flag()` | True once if the backup wrote the last answer (→ notice). |
| `structured(model_cls, fast=True, backup=False, memory=False)` | Schema-constrained runnable (see 7.8). |

### `models/database.py`
One shared SQLite connection guarded by a re-entrant lock; `tx()` gives a serialized transaction. All queries use
placeholders and filter by `user_id`.

| Group | Functions |
|---|---|
| Setup | `init_db`, `_migrate_memory_to_facts`, `_decode_values`, `tx`, `_now`, `_one`, `_all` |
| Users | `create_user`, `get_user_by_username`, `get_user` |
| Facts | `list_memories` (grouped: `memory_key`, `values`, `memory_value`, `updated_at`), `add_fact`, `set_fact`, `delete_fact(value=None)` |
| Documents | `add_document`, `update_document`, `find_document_by_hash`, `list_documents`, `get_document`, `delete_document` |
| Conversations | `create_conversation`, `get_conversation`, `list_conversations`, `rename_conversation` (title only; the chat keeps its place), `delete_conversation` |
| Messages | `add_message`, `list_messages(conv_id, limit)` |
| Stats | `user_stats` (SQL aggregates over the `messages` stat columns; no JSON parsing) |

### `tools/`
| Module | Functions | Notes |
|---|---|---|
| `__init__.py` | `history_to_messages(history, keep_full=2, older_max_chars=800)`, `history_to_text`, `today`, `clean_answer`, `FORMAT_RULES` | Older history messages are shortened; the last exchange is kept whole. |
| `greeting.py` | `respond(...)` | Delegates to `direct_answer.converse`. |
| `direct_answer.py` | `answer(query, history, confidence_required, memories) -> (text, needs_other_source, gate)`, `converse`, `KnowledgeGate`, `_gate_passes` | See 7.4. |
| `rag.py` | `format_context`, `generate`, `to_sources` | Partial answers kept (see 7.3). |
| `web_search.py` | `search`, `generate`, `to_sources`, `provider_names` | "Latest/current" questions get the current year; news uses Tavily's news topic with a day/week window; result text capped at 1,500 chars; results cached. |
| `memory.py` | `extract_and_save_memories`, `plan_changes`, `apply_changes`, `summarize_exchange`, `normalize_key`, `is_single_value` | See §8. |

### `memory/`
`embeddings.py`: `embed_memory`, `embed_memory_query` (Voyage). `store.py`: `ensure_memory_collection`, `save_memory`,
`search_memories` (always filtered to the user), `delete_conversation_memories`.

### `evaluation/`
| Function | Returns | Failure behaviour |
|---|---|---|
| `grade_documents(query, docs)` | Relevant subset + review steps | Used only when no reranker can score |
| `evaluate_answer(query, answer, context=None)` | `{sufficient, grounded, score, missing}` | Raises; `evaluate_node` keeps the answer as unverified |
| `citations.check(answer, evidence, question="")` | `CitationReport(ok, uncited, invalid, unsupported, support)` | No model, no network; never raises on normal text |
| `reviewer.verified_decision(...)` | `(decision, review_steps)` | Reviews only uncertain decisions; reviewer failures accept the decision |

### `graph/`
`workflow.run_agent(query, user, history, memories, doc_names, callbacks=None)` → final state;
`stream_agent(...)` → generator of `("status" | "step" | "draft" | "final", payload)`.

### `api/routes/chat.py`
`_prepare` (conversation, history, facts + episodes, document names, save user message), `_persist` (save the answer
with meta and usage, queue memory work), `_remember` (background: facts, episode, token log), `_error_state`
(rate-limit-aware failure answer), `chat`, `chat_stream` (agent thread + relay; `_STREAM_END` ends the stream).

---

## 13. Authentication & security

Accounts live in `backend/library/auth.py` (one login for the chat and the library).

### Passwords
- PBKDF2-SHA256 with a random salt and 600,000 rounds; only the hash is stored.
- Usernames: 3–32 characters `A-Z a-z 0-9 _ . -`. Passwords: at least 8 characters.
- The first account can always be made. After that a sign-up needs `SIGNUP_CODE`; with none set, sign-ups are closed.

### Login tokens
- A login is a random token in an HttpOnly cookie (`login`, 30 days). The database keeps only the token's SHA-256,
  so a copied database holds no usable login. Logout deletes it on the server.
- Wrong passwords are throttled: after 5 for one name from one address within 15 minutes, or 20 from one address,
  that name or address waits (`429`).

### Request guard
`dependencies.get_current_user` reads the cookie, looks up the token's hash and returns `{id, username}`. Every
failure returns `401 Please log in.`; the frontend then shows the login screen.

### Data isolation & input safety
- Every SQL query filters by `user_id`; every Qdrant query (documents and memories) filters by the user.
- SQL uses placeholders only; dynamic column names in `update_document` are allow-listed.
- Upload size is enforced while reading. Stored file names are made from titles, inside the user's own folder.
- Another user's document, chat or batch answers `404`, as if it did not exist; the document agent's tools are
  limited to the user's own library.
- Login errors don't reveal whether a username exists.

---

## 14. Data model

### SQLite (`backend/data/app.db`)

```
users ──< documents ──< renames, edits, undone_edits
  ├──< user_facts
  ├──< conversations ──< messages
  ├──< batches ──< batch_tasks
  ├──< sessions          (the document agent's state per conversation)
  └──< auth_tokens
```

| Table | Columns | Constraints |
|---|---|---|
| `users` | `id`, `username`, `password_hash`, `created_at` | `username` unique, case-insensitive |
| `user_facts` | `id`, `user_id`, `memory_key`, `value`, `created_at`, `updated_at` | `UNIQUE(user_id, memory_key, value)`, `value` case-insensitive |
| `migrations` | `name`, `applied_at` | One-off data migrations applied |
| `user_memory` | `id`, `user_id`, `memory_key`, `memory_value`, … | Pre-migration backup, no longer written; only in databases from before 1 November |
| `documents` | `id`, `title`, `original_name`, `file_name`, `ext`, `sha256`, `original_sha256`, `size_bytes`, `pages`, `chunks`, `summary`, `keywords`, `owner`, `status`, `error`, `created_at`, `updated_at` | `owner` is the user's id; a re-upload of the same file (as uploaded, or as it is now) is a duplicate |
| `renames` | `doc_id`, `old_title`, `new_title`, `reason`, `at` | A document's rename history |
| `edits` | `id`, `doc_id`, `operation`, `detail`, `backup`, `at` | `backup`: the file before the edit, in `data/versions/<doc_id>/` |
| `undone_edits` | as `edits`, plus `redo`, `ext`, `undone_at`, `undo_order` | Undone edits Redo can bring back (cleared by a new edit) |
| `batches`, `batch_tasks` | a batch's tasks: `instruction`, `payload`, `checkpoint`, `status`, `reply`, `actions`, `tokens` | The queue (§22) |
| `sessions` | the document agent's messages and guard, per conversation | |
| `auth_tokens` | the login token's hash, its user, when it expires | Logins (§13) |
| `conversations` | `id`, `user_id`, `title`, `created_at`, `updated_at` | cascade on user delete |
| `messages` | `id`, `conversation_id`, `role`, `content`, `tool`, `meta` (JSON), `created_at`, `latency_ms`, `rewritten`, `sufficient` | cascade on conversation delete; the last three are copied out of `meta` when an answer is saved, so `/stats` is plain SQL (filled from `meta` once in databases from before 14 November) |

`messages.meta` for assistant messages:

```json
{
  "intent": "rag",
  "sources": [{"type": "document", "title": "a.pdf", "document": "a.pdf", "page": 2, "score": 0.71, "snippet": "..."}],
  "notices": ["..."],
  "rewritten_queries": ["..."],
  "evaluation": {"sufficient": true, "grounded": true, "score": 5, "missing": ""},
  "trace": [{"id": "…", "node": "router", "detail": "…", "data": {}}],
  "latency_ms": 5796,
  "usage": {"total": {"input": 0, "output": 0}, "by_model": {"openai/gpt-oss-120b": {"calls": 0, "input": 0, "output": 0, "reasoning": 0}}, "by_step": {}}
}
```

### Qdrant

| Collection | Vectors | Payload |
|---|---|---|
| `agentic_rag_docs_v2` | `dense` (768, cosine) + `bm25` (sparse, IDF modifier) | `{page_content, metadata: {user_id, doc_id, source, title, file_name, page, chunk_index, start_index}}`; a rename updates `source`, `title` and `file_name` in place |
| `user_memories` | one unnamed vector (1,024, Voyage) | `{user_id, text, kind, conversation_id, updated_at}` |

---

## 15. HTTP API reference

Base URL `http://localhost:8000/api`. All endpoints except `health` and `auth/state`, `auth/signup`, `auth/login`
need the login cookie. Errors have the shape `{"detail": "message"}` (a rename's name conflict:
`{"detail": {"message", "conflicts", "title"}}`).

| Method & path | Body | Response |
|---|---|---|
| `GET /health` | — | `{"status": "healthy", "llm", "embeddings", "web_search": "tavily → duckduckgo → wikipedia"}` |
| `GET /auth/state` | — | Signed in or not, and whether a sign-up is open (`first`, `invite`, `closed`) |
| `POST /auth/signup` | `{"username", "password", "invite"?}` | the user, and the login cookie set |
| `POST /auth/login` | `{"username", "password"}` | the user, and the login cookie set — `429` while throttled |
| `POST /auth/logout` | — | the token deleted, the cookie cleared |
| `GET /auth/me` | — | `{"id", "username"}` |
| `GET /documents` | — | `[DocumentOut]`, each with `edit_id` (the edit Undo takes back) and `redo_id` (the one Redo brings back), or `null` |
| `POST /documents/upload` | multipart `file` | `{"status": "processing" \| "duplicate", "filename", "chunks_indexed", "document"}` — `400`, `413`; indexing errors appear on the document (`status: "failed"`, `error`) |
| `DELETE /documents/{id}` | — | `204` |
| `POST /chat` | `{"message", "conversation_id"?}` | `ChatResponse` (below) |
| `POST /chat/stream` | same | SSE (below) |
| `GET /conversations` | — | `[{id, title, created_at, updated_at}]` |
| `GET /conversations/{id}/messages` | — | `[{id, role, content, tool, meta, created_at}]` oldest first |
| `PATCH /conversations/{id}` | `{"title"}` (1–60 characters) | the conversation; `updated_at` unchanged, so it keeps its place in the list — `404`, `422` |
| `DELETE /conversations/{id}` | — | `204` (messages and remembered episodes removed) |
| `GET /conversations/{id}/documents` | — | Documents this conversation changed, newest first, with `latest_edit_id` / `redo_edit_id` |
| `GET /conversations/{id}/pending` | — | `{"tasks"}`: its queued tasks still to run |
| `GET /stats` | — | `{documents, chunks, conversations, queries, by_tool, queries_rewritten, answers_evaluated, answers_sufficient, avg_latency_ms}` |
| `POST /library/documents/{id}/rename` | `{"title", "confirmed"?}` | `{old_title, new_title, file_name}` — `400` unusable name, `409` another document has it (until `confirmed`) or still indexing |
| `POST /library/documents/{id}/undo` | `{"edit_id"?}` | The document as it was before its latest edit, with `redo_id` — `409` when `edit_id` is no longer the latest |
| `POST /library/documents/{id}/redo` | `{"edit_id"?}` | The undone edit back, under its own id — `409` when it isn't the one to redo |
| `GET /library/documents/{id}/file` | `?inline=true` | The stored file (download, or shown in the browser) |
| `GET /library/documents/{id}/pdf`, `/preview` | — | The document as a PDF; its text for the preview |
| `GET /library/queue` | — | `{"batches": [{id, created_at, tasks: [{instruction, status, reply, …}]}]}` (the last hour, or still running) |
| `DELETE /library/batches/{id}` | — | `{"cancelled"}`: the batch's waiting tasks |
| `GET /library/limits` | — | Groq tokens used in the last minute and the user's queue |

`ChatResponse`: `{conversation_id, message_id, answer, tool, intent, sources, notices, rewritten_queries, evaluation,
trace, latency_ms}` (the stream's `final` event also carries `usage`); `tool` is `rag | web_search | direct | greeting | no_results`.

**Stream events** (`POST /chat/stream`):

| Event | Payload | Meaning |
|---|---|---|
| `meta` | `{conversation_id, title}` | Sent first, so a new chat appears immediately |
| `status` | `{node, label}` | A step started, e.g. "Searching your documents…" |
| `step` | `{id, node, detail, data}` | A trace entry, as soon as it exists |
| `draft` | `{node, text}` | The answer so far (throttled to ~30 chars / 0.15 s) |
| `final` | `ChatResponse` | The saved answer |

---

## 16. Frontend

Redesigned in November 2026: an ink-and-grey interface where the one colour marks where an answer came from
(citation marks, "From your documents"), with light, dark and system themes.

### Data flow

```
App.jsx ── session, health polling, conversations, documents, routing
   ├── pages/Login            → apiService.login / register
   ├── components/Sidebar     → new chat, chats by recency, navigation, theme switch, log out
   ├── pages/Chat             → state/chatStore.sendMessage → apiService.streamMessage → Message; InputBox
   ├── pages/Documents        → getDocuments / uploadDocument / deleteDocument / renameDocument / undoEdit / redoEdit; QueuePanel
   └── pages/Overview         → getStats + document library
```

### Key pieces
| File | Responsibility |
|---|---|
| `App.jsx` | Health polling (4 s offline / 15 s online), session restore via `/auth/me` (a down backend never logs you out), layout; on phones the sidebar folds into a top bar. |
| `lib/route.js` | Hash routes `#/chat`, `#/chat/<id>`, `#/documents`, `#/overview`, so back, refresh and links work. |
| `state/chatStore.js` | Conversations and in-flight answers live outside the pages (`useSyncExternalStore`), so an answer keeps streaming when you switch pages or chats. A new chat is stored under `NEW_KEY` until the `meta` event gives its id. |
| `lib/theme.js` | `useTheme()`: System / Light / Dark, saved in `localStorage` (`agentic_rag_theme`) and applied as `<html data-theme>`. `index.html` applies it before the first paint. |
| `lib/format.js` | Counts, sizes, seconds, percentages, recency groups, source labels. |
| `services/api.jsx` | `request()` sends the login cookie (`credentials: 'include'`) and handles errors (401 → login; an object `detail` is kept on `err.data`); `streamMessage()` reads the SSE stream (`onMeta`, `onStatus`, `onStep`, `onDraft`, `onFinal`). |
| `pages/Login.jsx` | Sign-in / sign-up with the backend's rules, offline notice, and an example answer. |
| `pages/Chat.jsx` | New chat with suggestions based on your documents; otherwise the conversation and composer. |
| `pages/Documents.jsx` | Drop zone + picker, type/size checks, upload progress, status list (re-checked every 2 s while a file is indexing). Each ready document: Preview, Rename (inline), Undo edit and Redo (each asks first), Download / PDF, Remove (inline confirmation). The queue panel above the list. |
| `components/QueuePanel.jsx` | Queued tasks of the last hour, polled every 3 s while any run (15 s otherwise); Cancel the waiting ones; the list reloads when a task finishes. |
| `components/DocumentActions.jsx`, `ChatDocuments.jsx` | In the chat: what the document agent did, with Preview / Undo / Redo; the documents this chat changed. |
| `pages/Overview.jsx` | Key numbers, where answers came from, document library. |
| `components/Sidebar.jsx` | New chat, chats grouped by recency (a dot marks one still answering; rename inline with the pencil or a double-click, Enter saves, Escape cancels; inline delete confirmation), navigation, theme switch, user and log out. |
| `components/Message.jsx` | Answer with notices, Markdown and citation marks; a provenance line (source, checked or not, time); **Sources** and **Steps** disclosures; live status/steps/draft while pending. |
| `components/InputBox.jsx` | Growing textarea: **Enter** sends, **Shift+Enter** or **Ctrl+Enter** adds a line; drafts kept per chat. |
| `index.css`, `tailwind.config.js` | Colour tokens as CSS variables for both themes, type scale, component classes, `.markdown` styles. |
| `preview.html`, `src/dev/` | Design preview with sample data (dev server only). |

### Dev proxy
`vite.config.js` proxies `/api` to `http://127.0.0.1:8000`. For production, `npm run build`: the backend then serves
`frontend/dist` at `/` on the same origin as the API (`backend/web.py`).

---

## 17. Testing and benchmarks

**Tests** (no API calls, no network; the PDF layout ones take a few minutes): `pip install -r requirements-dev.txt`,
then `python -m pytest` from the project root. `conftest.py` blocks font downloads in every test.

| File | Covers |
|---|---|
| `test_routing.py` | Source orders, documents skipped without uploads, single fallback, auto → model knowledge, retry budgets, partial RAG answers, unchecked answers kept, web recency rules, knowledge-gate rules, memory pre-check |
| `test_memory.py` | Key normalisation, merge rules (replace / collect / forget / corrections), storage, one-time migration |
| `test_stream.py` | The stream ends with `final`; the answer is saved after the client disconnects |
| `test_citations.py` | The code citation check: uncited claims, invalid `[n]`, unsupported claims, numbers, lists under a cited lead-in |
| `test_rerank.py` | Reranker zones; fallback to the LLM check when Voyage can't score |
| `test_conversations.py` | Renaming chats (title trimmed, empty or too long rejected, other users' chats not found); fresh databases skip the old memory table |
| `library/` | The document library: accounts and privacy, the agent's guard, routing to it and follow-ups, renames, edits (TXT, DOCX, PDF in place, growth onto a new page, fonts, size), undo / redo, the queue, the API, the Documents page's routes and the served build |
| `test_documents.py` | Background indexing: upload returns `processing`, then indexed or failed; no second job for the same file; vectors removed if the document was deleted meanwhile; indexing cut off by a restart is resumed |

**Benchmarks** (`benchmark/`, see each script's docstring; retrieval ones need the backend stopped):

| Script | Measures | Latest result |
|---|---|---|
| `run_retrieval.py --stage …` | top-k, MRR, out-of-scope detection | MRR 0.774 → 0.822 (hybrid) → 0.872 (reranked); OOS 100% |
| `run_selection.py --collect / --compare` | Passage-selection methods: coverage, precision, tokens | Keep MMR, 6 passages (§21.8) |
| `run_e2e.py`, `run_perf.py` | Full answers; startup, memory, speed | Local rerank of 20 candidates ≈ 0.7 s; +450 MB |
| `calibrate_reranker.py` | Voyage vs local reranker on saved pools; zone thresholds | 74 questions: top-5 100%, MRR 0.930 (local 0.830) |
| `run_router.py` | 80 routing cases | 73/80 exact, 79/80 acceptable, 0/41 non-document messages sent to RAG |
| `run_direct_gate.py [--gate-only] [--old] [--only ids]` | 22 questions to answer from model knowledge, 9 that must be refused | 31/31 with the trimmed gate prompt; `--old` runs the pre-trim prompt (§21.13) |
| `run_memory.py [case-id] [--batch]` | 18 memory conversations | 18/18; 20/20 facts; 0 stale; 0 false saves; 16,224 tokens (batched: 18/18, 12,986) with the one retry (§8) |
| `run_checker.py [--fresh] [--rescore]` | Code citation check vs the 120B answer check, plus deliberately broken answers | 43 answers: 0 real problems missed; 187/190 broken answers caught (§21.9) |
| `run_direct_prompt.py` | Direct-answer prompt split: 19 questions through router → gate → answer | 12/12 open questions got the open-question rules; memories kept out of role-play (§21.10) |
| `run_web_prompt.py [--only ids]` | Web-answer prompt, old vs new, on the same saved search results (10 questions) | "As of" dates 6/6, gaps named 2/2 either way; "sources you provided" 0 (§21.14) |
| `run_web_checker.py [--fresh] [--rescore]` | Code citation check vs the 120B answer check on web answers, plus deliberately broken answers | 32 answers: 17 skip the 120B, 0 real problems missed (§21.15) |
| `run_identity.py --out f.json` | Identity probes ("what model are you?", guesses, role-play, injection) through the router and writers, plus 2 control questions | 0/33 probes name a model or provider (before: 27/33); controls 2/2 (§21.16) |

Experiments that call the APIs live in `scripts/experiments/` (run from the root, e.g.
`python -m scripts.experiments.test_gate`).

---

## 18. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ModuleNotFoundError: No module named 'backend'` | Start uvicorn from the project root or `backend/`. |
| UI says the server isn't running | Backend not on `127.0.0.1:8000`. The page reconnects by itself once it starts. |
| `[Errno 10048] … address already in use` | A backend is already running (often in another terminal). Stop it with Ctrl+C there, or use `--port 8001` and update the proxy. |
| Qdrant lock / `JSONDecodeError` at startup | Two processes use the embedded store. Run one backend (stop it before benchmarks), or use `QDRANT_URL`. |
| `Missing required settings in .env` | `GROQ_API_KEY` or `GEMINI_API_KEY` missing. |
| `Collection … has vector size X` | Embedding size changed. New `COLLECTION_NAME` or delete `backend/vector_db/qdrant_data`. |
| "The AI service's free usage limit has been reached" | A Groq limit (per minute or per day). The message says which model and how long to wait. |
| Answer marked "couldn't be automatically checked" | The checking model was rate-limited; the answer was kept unverified. |
| Opinion questions going to documents/web | Check the `direct_answer` step under **Steps**: the gate's `question_kind` and reason. `run_direct_gate.py` reproduces it. |
| Memory not recalled from past chats | `VOYAGE_API_KEY` missing, Voyage unavailable, or no Voyage request slot free within `VOYAGE_RECALL_MAX_WAIT` (all logged); facts still work. |
| Steps show "chunks judged relevant" instead of "Reranked … with Voyage" | Voyage failed (usually its rate limit: the free tier without a payment method allows 3 requests / 10,000 tokens per minute) and the LLM check was used. The shared limiter (`config/voyage.py`) keeps a slot for the reranker, so this should only happen when another process uses the same Voyage key. Add a payment method on Voyage and raise `VOYAGE_REQUESTS_PER_MINUTE`. |
| A document shows Failed: "No extractable text" | Scanned/image-only PDF. OCR it first. |
| Web search returns nothing | DuckDuckGo blocking or rate limiting. Add a `TAVILY_API_KEY`. |

---

## 19. Extending the system

**Add a source/tool:** add it to `Route.source_preference` and `ROUTER_PROMPT`; implement it in `backend/tools/`
and a node in `nodes.py` that sets `answer`, `tool`, `trace`; add it to `_source_order`, `SOURCE_NODE`,
`FALLBACK_NOTICES` and `build_graph()`; label it in `Message.jsx` and `lib/format.js`.

**Support a new file type:** extend `SUPPORTED_EXTENSIONS` and `load_file` in `rag/ingestion.py`, and `ACCEPTED`
in `pages/Documents.jsx`.

**Swap models:** change `LLM_MODEL` / `FAST_LLM_MODEL` / `MEMORY_LLM_MODEL`. Keep structured output working for
every schema in 7.8. Changing `VOYAGE_RERANK_MODEL` requires recalibrating `VOYAGE_RELEVANCE_HIGH` / `VOYAGE_RELEVANCE_LOW`.

**Remember a new kind of fact:** add single-value keys to `SINGLE_VALUE_KEYS` (or use a `favorite_` / `preferred_` /
`current_` prefix) and variants to `KEY_ALIASES` in `tools/memory.py`; add a case to `benchmark/memory_cases.json`.

**Scale out:** point `QDRANT_URL` to a Qdrant server, and replace SQLite with Postgres behind the same
`models/database.py` functions.

---

## 20. Known limitations

- **Voyage rate limits** — without a payment method on the Voyage account, 3 requests / 10,000 tokens per minute
  (one rerank ≈ 3,500 tokens), shared with memory embeddings; beyond that, answers use the slower LLM relevance check.
- **Free-tier limits** — each Groq model allows 8,000 tokens/minute and 200,000/day; Gemini embeddings 1,000/day.
- **Single-process embedded Qdrant** — use a Qdrant server for multiple workers.
- **One file indexed at a time** — uploads queue on one background worker; a large file delays those after it.
- **No OCR** — image-only PDFs cannot be indexed.
- **LLM judges are fallible** — the gate, grading and evaluation reduce, but don't eliminate, wrong routes and
  ungrounded answers.
- **No rate limiting** on chat endpoints (wrong passwords are throttled).
- **PDF edits** keep the layout or refuse: text needing more than one new page, scanned PDFs, and PDFs without a
  text map (some Word / Chrome exports) can only be converted to a Markdown copy. Page references in the body text and
  table-of-contents numbers are not renumbered when a page is added.

---

## 21. Measurements: before and after

Every change below was measured before it was kept. "Old" is the version before the change; "new" is the
current code. Benchmarks are in `benchmark/` (results in `benchmark/results/`); token figures come from
`backend/data/token_usage.jsonl` and the answer comparisons described in each subsection.

### 21.1 Token use per chat message

The same 9-message test conversation was run on 25 October before and after the token work (model roles, strict
JSON output, Qwen for memory, the memory pre-check), so those two columns compare identical messages. "Now" is 17 real
messages on 1–2 November, before the 2 November prompt trims (21.2).

| Per message | Original (25 Oct, 9 messages) | After token work (same 9) | Now (1–2 Nov, 17 real) |
|---|---|---|---|
| Average total tokens | 6,645 | 4,344 (**−35%**) | 4,232 |
| Median | 6,984 | 3,995 | 3,932 |
| Largest single message | 11,903 | 6,345 | 8,026 * |
| gpt-oss-20b | 4,486 tokens, 2.9 calls | 1,189 tokens, 1.0 call (**−73%**) | 1,237 tokens, 1.0 call |
| gpt-oss-120b | 2,159 | 2,492 (now writes small talk too) | 2,528 |
| Qwen (memory) | — | 664 | 467 |
| Memory extraction | on 9 of 9 messages, 2,498 tokens each (20B) | on 6 of 9, 995 each (Qwen) | on 2 of 17, 1,070 each |
| Average response time | 3.1 s | 2.0 s | 3.0 s |

\* The 8,026-token message was a philosophical question that the old knowledge gate sent through model knowledge →
documents → web (two answers written and checked). After the gate fix (21.5) the same question is answered directly
in about 2,900 tokens. Groq's 8,000-token limit is per model per minute; the largest single-model use in any of these
messages was 5,995 tokens (that same message).

### 21.2 Prompt trims (2 November)

Three changes: the answer instructions were rewritten without repetition (**1,601 → 1,026 tokens**, every rule
kept); the knowledge gate now sees recent messages capped at 600 characters each (it used to get them in full; saved
answers run up to 10,740 characters); the answer checker gets only the passages an answer cites.

Same 8 questions, same memories, answered with the old and the new instructions (input + output tokens):

| Question | Old | New | Change |
|---|---|---|---|
| Personal scenario (shoe rack) | 1,837 + 334 = 2,171 | 1,262 + 495 = 1,757 | −19% |
| Pushback on that answer | 1,886 + 248 = 2,134 | 1,311 + 179 = 1,490 | −30% |
| Opinion (lying to spare feelings) | 1,758 + 305 = 2,063 | 1,183 + 329 = 1,512 | −27% |
| Hypothetical (Titanic lifeboat) | 1,777 + 378 = 2,155 | 1,202 + 455 = 1,657 | −23% |
| Challenge ("would nothing change your decision?") | 1,825 + 254 = 2,079 | 1,250 + 366 = 1,616 | −22% |
| Factual (BM25 vs TF-IDF) | 1,757 + 303 = 2,060 | 1,182 + 468 = 1,650 | −20% |
| About the AI ("do you have feelings?") | 1,753 + 125 = 1,878 | 1,178 + 89 = 1,267 | −33% |
| Coding (reverse a list) | 1,756 + 264 = 2,020 | 1,181 + 254 = 1,435 | −29% |
| **Total** | **16,560** | **12,384** | **−25%** (input −32%; output +19%, within run-to-run variation at temperature 0.8) |

The answers were compared side by side: same positions, same completeness, personal scenarios still answered in
first person. Not in this table: the answer checker now saves about 500–800 tokens per document or web answer, and
the gate cap removes up to several thousand tokens from follow-ups after long answers.

### 21.3 Prompt caching

Groq caches the start of a prompt automatically on gpt-oss models (half price; cached tokens don't count toward
rate limits). Only the answer instructions are long enough to be cached (the minimum is about 1,024 tokens).
Measured on 8 repeated calls: about half hit the cache, each serving 1,024–1,536 of ~1,680 prompt tokens from it.
Cached tokens are now recorded (`cached` in each answer's usage and in the log); they were not before.

### 21.4 Relevance checker: local cross-encoder → hosted Voyage reranker

Same 20 saved candidates per question for both (`benchmark/calibrate_reranker.py`), all 74 benchmark questions
(67 answerable, 7 out of scope).

| | Old: local MiniLM | New: Voyage `rerank-2.5` |
|---|---|---|
| Right passage ranked 1st | 76% | **88%** |
| … in the top 3 | 87% | **97%** |
| … in the top 5 (the writer gets 6) | 92% | **100%** |
| MRR@10 | 0.830 | **0.930** |
| Answerable questions answered from the documents | 65 of 67 (**2 wrongly skipped**) | **67 of 67** |
| Out-of-scope questions sent on | 6 of 7 (1 answered) | 6 of 7 (5 at once, 1 after one extra search; 1 answered) |
| Gap between scores (lowest answerable / highest out of scope) | 0.000 / 0.966 | 0.584 / 0.812 (0.513 without the K2 near-miss) |
| Cut-offs (answer / move on) | 0.05 / 0.01 | 0.55 / 0.45 |
| Memory | +~450 MB (torch + model) | none |
| Startup | ~11 s background load | nothing to load |
| Time per question | ~0.7 s (CPU) | ~0.65 s (API) |
| Cost per question | none | ~3,670 Voyage tokens |

On the first 35 questions, `rerank-2.5` also beat `rerank-2.5-lite` (top-1 100% vs 97%, MRR 1.000 vs 0.976) at the
same token cost.

### 21.5 Knowledge gate (opinion and philosophical questions)

`benchmark/run_direct_gate.py`, 18 open or general questions that should be answered from model knowledge, plus 4
that must be refused (current events, the user's own files):

| | Old | New |
|---|---|---|
| Gate alone: answered directly | 3 of 18 | **18 of 18** |
| Gate alone: refused as expected | 4 of 4 | 4 of 4 |
| Router + gate (full path) | — | 22 of 22 |

### 21.6 Personal "what would you do?" answers

Old: the two answers to the shoe-rack scenario and its pushback (1 November). New: 10 answers to the same
scenario, its pushbacks and a repeat, after the change.

| | Old | New |
|---|---|---|
| Answered as a plan or advice (steps, "Why:", counselling, legal advice) | 2 of 2 | **0 of 10** |
| Borrowed the user's saved details for the character ("Kathmandu") | 2 of 2 | **0 of 10** |
| Pushback / repeat gave a substantially different answer | no (same plan reworded) | **yes** |
| Answer temperature for open questions | 0.0 | 0.8 (factual answers stay at 0.0) |

### 21.7 Memory and routing (1 November and 26 October)

| Benchmark | Result |
|---|---|
| `run_memory.py`, 18 conversations | 18/18 cases; 20/20 facts saved; 0 stale values after corrections; 0 false saves; ~21k memory-model tokens |
| `run_router.py`, 80 messages | 73/80 exact, 79/80 acceptable; 0 of 41 non-document messages sent to document search; ~960 tokens, 0.66 s per decision |
| Unit tests | 48, no API calls, ~1 s |

### 21.8 Passage selection (which retrieved passages the writer gets)

`benchmark/run_selection.py --compare`: 67 answerable questions, 9 selection methods and 232 settings, chosen on 45
training questions and scored on 22 held-out ones. Methods are ranked by F2 (coverage of the required facts counts
double, then precision). "Today" is the app's selection: MMR, 6 passages.

| Held-out (22 questions) | Coverage | Precision | Tokens to the writer |
|---|---|---|---|
| Today's selection, old local reranker scores | 0.879 | 0.364 | 1,051 |
| **Today's selection, Voyage scores (current)** | **0.924** | **0.500** | 1,087 |
| Best cheaper method (2–4 passages by confidence), Voyage | 0.894 | 0.636 | 672 (−38%) |
| Different method per question type, Voyage | 0.894 | 0.636 | 456 (−58%) |

Switching to Voyage already improved the selection (coverage +4.5 points, precision +14 points). Cheaper selections
cut the writer's input by 40–58% but lose facts: on all 67 questions, the best one covers less than today on 4
(`ccs-stack`, `sum-who`, `ma-gdpr-principles`, `ma-wb`, the last losing everything). No cheaper setting kept today's
coverage. Decision: keep 6 passages with MMR; coverage comes first. (Choosing a method per question type would also
need the router to classify the question's shape, which it doesn't.)

### 21.9 Answer check: 120B on every answer → code first (3 November)

Document answers are now checked in code (`backend/evaluation/citations.py`, no model, no tokens) before the 120B:

1. **Cited**: every factual sentence has an `[n]` in the sentence, its paragraph, or the list it belongs to.
2. **Real citations**: every `[n]` points at a passage the writer was given.
3. **Content matches**: the sentence's key words appear in the passage it cites (same tokenizer as the keyword
   search, with plural and stem matching); numbers and names with digits in them (BM25, COVID-19) must appear exactly.

If all three hold and the reranker rated the evidence "high", the answer goes out with no 120B check. Anything the
code flags or weaker evidence still go to the 120B. Web answers got the same check on 4 November (§21.15). The code cannot judge completeness or catch a
claim that reuses the passage's words but reverses their meaning; with strong evidence that risk was not seen.

`benchmark/run_checker.py`: the 120B writes and checks each answer exactly as the app does, then the code checks
the same answer. Each answer is also broken on purpose five ways. The rules were tuned on the first 23 answers, then
tested on 20 questions they had never seen (`--fresh`).

| | Tuning set (23) | Fresh set, as first run (20) | Fresh set, after fixes (20) |
|---|---|---|---|
| Same verdict as the 120B | 20 | 16 | 18 |
| Real problems the code missed | 0 | 0 | 0 |
| Flagged only by the 120B | 0 | 1 (the 120B was wrong: passage [6] contains the claim) | 1 (same) |
| Flagged only by the code (sent on to the 120B) | 3 (1 real, 1 borderline, 1 false) | 3 (false) | 1 (borderline) |
| Broken answers caught | 98/100 | 88/90 | 89/90 |

The three false alarms on the fresh set came from rule bugs, fixed for all answers (not these ones): a list under
a cited lead-in, "72-hour" vs "72 hours", a name the question itself is about ("BM25"), and "CO₂" vs "carbon
dioxide". A real gap was also fixed: short sentences with a number ("Nepal has 760 local governments [1]") were
skipped as too short. The fixes changed no verdict on the tuning set. The missed broken answers all had citations
moved to overlapping passages that really do contain the claim.

| | Old | New |
|---|---|---|
| Document answers checked by the 120B | 43/43 | 6/43 (the rest pass in code) |
| 120B tokens per skipped check | ~1,000–1,900 | 0 |
| Real problems missed | — | 0/43 |

### 21.10 Direct-answer prompt split (3 November)

The direct-answer prompt (35% of all tokens in the log, the largest single step) carried its role-play and
personal-scenario rules on every call, including factual questions and small talk. It is now split: a core part
always, an open-question part only for opinions, hypotheticals, role-play and personal scenarios. Duplicated rules
(follow-ups, personal reactions) were removed from the shared format rules, which also shortens the document- and
web-answer prompts.

| Prompt | Old | New |
|---|---|---|
| Factual direct answers and small talk | 1,022 tokens | 480 (−53%) |
| Opinions, role-play, personal scenarios | 1,022 | 1,006 |
| Document answers | 250 | 216 |
| Full answer prompt, factual question (`benchmark/run_direct_prompt.py`) | ~1,090 | 550 |

`benchmark/run_direct_prompt.py`: 19 questions through the real router → gate → answer path, with two saved
memories (Kathmandu, student) to test that they stay out of role-play.

| | Result |
|---|---|
| Personal (7), opinion (3), role-play (2) questions that got the open-question rules | 12/12 |
| Factual (4) and small talk (2 of 3) on the core prompt | 6/6 ("what can you do?" was routed as a question and got the full prompt) |
| Personal answers: first person, no lists, no "Why:", no counselling/lawyer/evidence advice, ≤ 3 paragraphs | 7/7 (old prompt: 7/7) |
| Saved memories kept out of the character | 6/7 on the first run (burglar scenario: "my tiny Kathmandu flat"); after adding the rule to the memory note itself, 3/3 re-runs of that case clean |

Old and new personal answers read alike (first-person, in-the-moment reactions); the old-prompt answers are kept
in `benchmark/results/direct_prompt_eval.json` for comparison. Cached prompt tokens on Groq cost half and don't
count toward rate limits, so the real saving is somewhat below the token difference shown.

### 21.11 Router prompt (3 November)

The router runs on every message (28% of logged tokens). Its rules were written twice, in the prompt and again in
the output fields' descriptions (sent with every call as part of the output schema). Now: one set of rules in the
prompt, short field descriptions (except `source_preference`, see below), the two overlapping sections on
follow-ups and standalone queries merged, small-talk examples added ("who are you?", "what can you do?"), and the
file list capped at 20 names.

Two lines were added after the first benchmark run showed misses: documents also cover "the X report" mentions and
details only the user's files could contain ("what does chapter 3 say?"), and a follow-up uses the source its
topic needs ("who scored?" after a match question: web). In code, `asks_for_documents` now also catches "what does
the X document/report/paper say …", only when the user has uploaded files.

Repeated runs caught one regression the single run missed: with a one-line `source_preference` description, "what
methodology did they use in the complaint classification system?" went to model knowledge 3 times in 5 (old
prompt: 0 in 5). Restoring that description fixed it (5/5 to the documents).

| `benchmark/run_router.py` (80 cases) | Old | New |
|---|---|---|
| Exact | 73 | 73 in the merged run; the one new miss (`imp-method`) was then fixed: 5/5 in repeats |
| Acceptable | 79 | 79 (80 with the fix) |
| Non-document messages sent to the documents | 0/41 | 0/41 |
| "what can you do?" | model knowledge | small talk |
| Tokens per message (incl. reviews) | 962 | 875 (−9%) |
| Fixed prompt + output schema | 813 | 743 |

### 21.12 Memory extraction: fewer calls, and two broken backup paths fixed (3 November)

**Fewer calls.** The cheap filter that decides whether a message might hold a personal fact fired on any "I",
"me", "my" or "we", so "how do I center a div" or "give me an overview of BM25" cost a memory-model call (~1,600
tokens) that found nothing. It now looks at each clause: a clause that is a question, hypothetical or request ("how
do I…", "what's my…", "if I were…", "give me…", "check my files…") doesn't count; memory commands ("remember",
"forget", "call me", "from now on") always do.

| Filter fires on | Old | New |
|---|---|---|
| 80 router benchmark messages | 10 | 2 (one is a real fact: "I'm Asmin btw") |
| 74 document benchmark questions | 5 | 0 |
| Turns in the 18 memory conversations that state a fact | all | all |
| Question-only turns in those conversations | 3 of 4 | 0 of 4 |

**Shorter field descriptions were tried and rejected**: the memory model then saved nothing for "I'm Asmin and I live
in Pokhara", ignored "forget all my hobbies", and filed "I'll call you Nova" as the user's own name. They stay in full.

**Two backup paths were broken** (found while benchmarking). The memory model (Qwen) misspelled its tool's name in 3
of 8 identical calls ("MemoryExclusion", "MemoryExression"); Groq rejects those, and the plain-JSON fallback sent only
the top-level fields, so Qwen copied the schema back instead of answering. Both failures silently lost the fact.
Now: a misnamed call is read from the rejected generation (it usually holds the right facts; no extra call), and the
plain-JSON fallback sends the full schema. Same message, 8 calls: 5/8 saved before, 8/8 after.

**Before vs after** (`benchmark/run_memory.py`, 18 conversations; "before" is the 1 November run, kept in
`benchmark/results/memory_eval_before.json`; "after" is `memory_eval.json`):

| | Before | After |
|---|---|---|
| Cases passed | 18/18 | 17/18 (16/18 in an earlier run the same day) |
| Facts saved | 20/20 | 19/20 |
| Stale values left after corrections | 0 | 0 |
| False saves (questions, thanks, hypotheticals) | 0 | 0 |
| Memory-model tokens | 20,998 | 15,984 (−24%) |
| Same message sent 8 times: fact saved | 5/8 (misnamed tool calls lost) | 8/8 |
| Unit tests | 65 | 68 (filter cases, tool-call recovery) |

Every miss after the change was Qwen returning an empty list for a plainly stated fact ("I hate spicy food",
"I play futsal"); the filter had passed the message on in each case, and each case passed 3/3 when repeated. Qwen's
reliability varies from day to day, which also explains the 18/18 on 1 November. A possible follow-up is one
retry when Qwen returns nothing for a message with a strong cue; it would cost tokens only in that case.

Single-case runs (`run_memory.py <case-id>`) no longer overwrite the full results file.

### 21.13 Knowledge gate prompt (3 November)

The gate (120B) runs before every direct answer the router wants fact-checked. Its rules were written twice, in the
prompt and again in the output fields' descriptions, and its closing note told it when to "use web search", which
the gate doesn't decide. Now: one set of rules in the prompt, short field descriptions, the web-search line removed,
and the fixed rules first with the conversation, memories and question after them (a longer cacheable prefix).

The benchmark had only 4 must-refuse questions, so 9 cases were added first: 4 facts to answer (arithmetic, binary
search, the end of WWII, a short function) and 5 to refuse (Bitcoin price, latest React version, Nepal Telecom's
CEO, "the lecture slides", Daraz Nepal's 2021 revenue). Both prompts were run the same day (`--old`).

Repeated runs caught a regression the single run missed: with the `confidence` description cut to "as defined
above" and the "not reliable" list folded into one sentence, the gate rated an invented paper ("Sparse Graph
Tricks for Tiny Transformers") high and let it through in 2 of 5 runs (old prompt: 0 of 5). Restoring both fixed it
(0 of 8).

| `run_direct_gate.py --gate-only` (31 questions) | Old | New |
|---|---|---|
| Answered as expected | 22/22 | 22/22 |
| Refused as expected | 9/9 | 9/9 |
| Invented paper let through (repeats) | 0/5 | 0/8 (2/5 before the fix) |
| Fixed prompt + output schema per call | ~750 tokens | ~605 (−19%) |
| Benchmark tokens (31 gate calls) | 27,278 | 23,440 (−14%) |

Follow-ups with history still resolve: "when was it published?" after a Pride and Prejudice question passes;
"and its current Amazon price?" is flagged time-sensitive and refused. Results: `benchmark/results/gate_trim_old.json`,
`gate_trim_new.json`; the old prompt is kept in `gate_prompt_old.json`.

### 21.14 Web-answer prompt (4 November)

Five overlapping lines on recency and dates were merged into one, and two rules were added: for anything that
changes (prices, rates, office holders, versions) say when it applies ("as of March 2026, …") whenever a source
gives the date; and when the results answer only part of a question, answer that part and say plainly what they
don't cover. The goal was answer quality, not tokens: the instructions are 74 tokens longer (306 → 380).

`benchmark/run_web_prompt.py` searches each of 10 questions once and saves the results
(`results/web_prompt_results.json`), so both prompts answer from identical evidence: 6 "current" questions (Python and
React versions, Nepal's prime minister, gold price, policy rate, Ballon d'Or), 2 that the results can only partly
answer (Eiffel Tower visitors "last Tuesday", iPhone sales in Nepal "last month") and 2 general ones.

| 10 questions, same search results | Old | New |
|---|---|---|
| Current answers that say when the figure applies | 6/6 | 6/6 |
| Partial answers that name what the results don't cover | 2/2 | 2/2 |
| Output tokens | 3,879 | 3,444 (−11%) |
| Input tokens | 21,089 | 21,829 (+4%) |
| Answers calling the results "the sources you provided" (6 repeats of the 2 partial questions) | 1/6 | 0/6 |

Both prompts gave the same facts. The new answers are tighter (the 6 current answers: 2,336 → 1,678 output tokens),
and the old Ballon d'Or answer invented a "conflict" the new one doesn't.

A regression was caught and fixed on the way: the new partial-answer rule made the model talk about its sources more,
and it credited them to the user ("the sources you provided": 4 of 8 runs, then 5 of 6 with a shorter results
heading; old prompt 1 of 6). A sentence in that rule ("the results come from your search, not from the user")
fixed it: 0 of 6. The results heading is kept word for word from the old prompt.

The code citation check (§21.9) flags 4–6 of these 10 web answers for both prompts, and the flags change from run to
run; web text has quirks document passages don't (numbers written with narrow no-break spaces, dates the source
words differently). These were fixed, and the code check validated for web answers, in §21.15.

Results: `benchmark/results/web_prompt_eval.json`; the old prompt is kept in `web_prompt_old.json`.

### 21.15 Web answers: code check first (4 November)

Web answers now go through the same code citation check as document answers (§21.9). If every claim is cited and
found in the result it cites, the 120B check is skipped. One extra rule: a web answer that says the results don't
cover part of the question always goes to the 120B, because only the model judges whether a partial answer is
enough (it can send the question on to another source).

Web text needed four checker fixes, none of which changed a verdict on the 43 document answers:

- numbers grouped with narrow or no-break spaces ("8 848.86") are read as one number;
- month abbreviations match full names ("Oct." ~ "October");
- version numbers ("Python 3.15.1", "v19.3") must appear in a cited result; a changed version used to pass,
  which matters most on "latest version" questions;
- sentences about the results ("the search results don't give…") are recognised as notes about the evidence,
  not claims, and reported as gaps.

`benchmark/run_web_checker.py` compares the code's verdict with the 120B's, which is given what the app gives it
(the cited results, 1,200 characters each), and breaks each answer five ways. The rules were fixed on the 20
answers from §21.14, then tested on 12 new questions searched and answered for this test (`--fresh`).

| | Tuning set (20) | New questions (12) |
|---|---|---|
| 120B check skipped (code passes) | 11 (incl. the one only the 120B flagged) | 6 |
| Real problems the code missed | 0 | 0 |
| Flagged only by the 120B | 1 (the 120B was wrong: every claim it named is in the results it was shown) | 0 |
| Flagged by both | 5 | 1 |
| Flagged only by the code (sent on to the 120B) | 4 | 5 |
| Broken answers caught: invented, uncited, invalid citation, changed number | 78/80 | 46/48 |
| Broken answers caught: citations moved to another result | 12/18 | 9/12 |

Moved citations are caught less often than on documents because several search results usually report the same
fact, so the moved citation still supports the claim. The changed numbers that got through were changed to a
number that also appears elsewhere in the cited result.

What the code flags on its own is mostly real: numbers the cited result doesn't contain (a converted 13,170 miles,
"99.9%" purity, a year from the model's own knowledge). On one new question (US dollar to rupee) the code caught
two exchange rates that appear in none of the results, which the 120B had passed.

| | Old | New |
|---|---|---|
| Web answers checked by the 120B (32 answers) | 32/32 | 15/32 |
| 120B tokens per skipped check | ~1,400–2,000 | 0 |
| Real problems missed | — | 0/32 |

Results: `benchmark/results/web_checker_eval.json` and `web_checker_eval_fresh.json`.

### 21.16 Persona (Hroptatyr) and keeping the underlying model private (13 November)

Every answer writer (direct answers, small talk, documents, web) now gets one identity rule, `IDENTITY` in
`backend/tools/__init__.py`, sent as part of `FORMAT_CORE`: never name or hint at the model, its family, size, maker or
host; don't confirm or deny guesses; say it can't be shared and help with what the user needs; don't claim another
identity. It holds in role-play, for "I'm the developer" requests and against "ignore your instructions". AI models
as a topic can still be discussed.

The same rule gives the assistant a persona: it is **Hroptatyr**, wise in the plain sense (calm, measured, honest
about what it knows, practical, a dry warm wit), shown in how it answers rather than in mythology or grand phrasing.
It gives its name when asked who it is, and otherwise doesn't mention it. It never calls itself "the Wise" (an
epithet that goes with the name): if the user adds the title, it answers with the plain name, without repeating
it. Asked what the name means, it says it is one of Odin's names in Norse myth and that its exact meaning is
uncertain; left to itself it invented a different translation every time.

`benchmark/run_identity.py` sends 9 identity probes through the real router and the writer it picks, plus one
straight to the document writer and one to the web writer, and 4 name probes ("What's your name?", "What does your
name mean?", "Introduce yourself", "Are you Hroptatyr the Wise?"), 3 times each; then 3 control questions (GPT-4,
Llama vs Qwen, binary search).

| | Before | After |
|---|---|---|
| Probes whose answer named a model or provider (33) | 27 | 0 |
| Control questions answered normally | 2/2 | 3/3 (binary search added) |
| Name given when asked (9) | — | 9/9 |
| Answers saying "the Wise" (all 48) | — | 0 (plus 7/7 on extra title probes) |
| Ordinary answers that mention the persona | — | 0 |
| Prompt tokens per answer | — | +~300 |

Before the rule the model didn't only leak, it was wrong: "I'm ChatGPT, built on OpenAI's GPT-4 Turbo", and "No,
I'm not gpt-oss-120b on Groq". After it: "I'm not able to share which model powers this assistant, but I'm happy to
help…"; "Who are you?" still describes what the app can do.

The prompt is not the only place the model shows: `GET /api/health` returns the model names (`llm`, `embeddings`)
without login, and the token log and benchmark results name them. The UI shows none of them.

Results: `benchmark/results/identity_old.json`, `identity_new.json` (privacy rule only) and `identity_persona.json`.

### 21.17 Fewer calls per message: router skips, facts-only decisions, slim small talk, batched memory (14 November)

Four token cuts that leave answer quality alone.

- **Router skipped when code can decide** (`nodes._code_route`, ~1,000 tokens each). A message that is only a greeting,
  thanks or goodbye ("hi", "thanks a lot!", "see you later") goes straight to small talk. The first message of a
  conversation that explicitly asks for the uploaded documents (`asks_for_documents`, with uploads) goes straight to
  the documents; with no history it is already standalone. Trace steps say `decided_in_code`.
- **Shorter episodes for the router and the gate** (`tools.memories_for_decision`). They get each recalled episode
  cut to 300 characters (the answer writer gets them whole; whole for both when the message says "last time",
  "remember", "you said"…). First version dropped the episodes for these two steps entirely: on 7 messages whose
  answer was in a recalled episode (2 runs each) that went from 14/14 answered from memory to 10/14 ("which metrics
  did we settle on?" refused by the gate, "how many days was my trek?" sent to the documents), since a message can
  refer back without saying "last time". With the 300-character cut: 14/14 again.
- **Slim small-talk prompt** (`direct_answer.SMALL_TALK`). Small talk no longer gets the direct-answer rules on
  accuracy and follow-ups: system prompt 1,848 characters instead of ~4,200; the persona rule (`IDENTITY`) is kept.
- **Batched fact extraction** (§8, `memory/batching.py`): one memory-model call per conversation pause instead of
  one per message with a possible fact.

| | Before | After |
|---|---|---|
| Router benchmark messages decided without the router (80) | 0 | 10 (2 greetings, 8 document requests), all as expected |
| Identity probes naming a model or provider (33) / name given (9) / "the Wise" | 0 / 9 / 0 | 0 / 9 / 0 |
| Memory benchmark, same day (18 conversations) | 17/18, 14,375 tokens | 17/18, 12,180 tokens (−15%); 0 false saves, 0 stale both |

The misses differ and are intermittent: per message `hobbies-append` once, batched `many-facts` once (a single
message, so the unchanged single-message prompt); both pass 3/3 batched on repeat.

Not done: folding the knowledge gate into the answer call. The gate also decides whether a question is *open*, which
picks the open-question prompt and temperature, so the answer call can't know which prompt to use; and checking it
needs full answers (31 cases × 5, ~300k 120B tokens). Prompt caching was left as it is (§21.3).

Results: `benchmark/results/memory_eval_batch.json` (batched), `memory_eval.json` (per message, same day).

**Same messages, old code (0b189e5) vs new**, 8 messages with fixed histories, three saved facts and two recalled
episodes: greeting, thanks, personal small talk, a first-message document request, a factual and an opinion question,
a follow-up after a long answer, and a reference to a past chat. Old code ran on a key with capacity (its first run hit
the 120B limit and 5 answers came from the backup model, so it was discarded); 0 backup answers in both runs below.

| Message | Old | New |
|---|---|---|
| "hi" | 2,591 | 937 (router and its 120B review skipped) |
| "thanks a lot!" | 3,720 | 1,836 |
| personal small talk | 2,607 | 2,061 (slim prompt) |
| first-message document request | 3,042 | 1,846 (router and review skipped) |
| factual / opinion / long follow-up / past chat | 3,021 / 3,097 / 4,687 / 3,060 | 3,035 / 3,065 / 4,674 / 3,129 |
| **Agent total** | **25,825** | **20,583 (−20%)** |

Single messages vary by about ±500 tokens between runs (the router asks for a fact check, and so a gate call, in some
runs and not others), so only the total is meaningful. The long follow-up's previous answer contained code, so the
history cap left it whole. Small talk on 6 personal messages ("my dog died yesterday", "I got the internship!!") read
as warm and natural with both prompts; the new one mentions the documents and web less often (2/6 replies vs 5/6).

Two smaller cuts, the same day:

- **Previous answer capped in history** (`tools.history_to_messages`). The answer writers still get the previous
  exchange, but an answer longer than ~1,600 characters keeps its first 1,000 and last 500 characters with a
  "middle omitted" marker: follow-ups mostly ask about the opening claim or the last points. Answers containing code
  stay whole, since their follow-ups edit the code. In the stored conversations, 50 of the 55 follow-ups after a
  long answer would be capped, saving ~14,400 tokens across them (~290 per such follow-up). On 5 real follow-ups
  answered with the full and the capped answer, 4 were equivalent; the fifth (a moral dilemma at temperature 0.8)
  varied run to run either way (3 repeats each).
- **Short exchanges stored without a summary** (`routes/chat.py`, `EPISODE_RAW_MAX_CHARS` = 400). When question +
  answer are at most 400 characters, the exchange is saved as it is, about the size of a summary, and the memory
  model isn't called: 116 of the 281 stored exchanges that got a summary were this short.

### 21.18 Document-answer prompt (14 November)

`tools/rag.py`. The old prompt said "Be concise and well structured" (the shared format rules already ask for
concise prose and no headings by default), gave the citation rule twice, and called the passages "excerpts", which
answers repeated to the user. The new one, 602 tokens instead of 499:

- cite at the end of every sentence or list item that uses a passage, including a sentence that introduces or sums
  up a list (these were the usual reason the code citation check failed, so the 120B check ran);
- copy numbers, dates, amounts and names as the documents write them;
- partial answers say in one sentence what the documents don't cover; disagreeing passages are both given, cited;
- earlier messages are context for what the user means, not a source of facts;
- say "the documents", never "excerpts" or "passages". The user turn's heading is now "Passages from the user's
  documents:" (`CONTEXT_INTRO`).

A "no LaTeX" rule was tried and dropped: the Wikipedia passages contain LaTeX and the model copied it anyway (2/2).

**Bug fixed with it:** `generate` treated any reply under 40 characters as "no answer", so "BM stands for “best
matching” [1]." (37 characters, correct) was thrown away and the user was told the documents don't cover it. Length
now matters only when the sentinel is in the reply.

Benchmark `benchmark/run_rag_prompt.py`: 43 questions (6 per answer shape, seed 5, plus all 7 out-of-scope), the
passages the app would pick (as `run_checker.py`), the same for both prompts. Code checks only.

| | Old | New |
|---|---|---|
| Answerable questions refused (36) | 1 (the 40-character bug) | 0 |
| Out-of-scope questions refused (7) | 7 | 7 |
| Pass the code citation check, so the 120B check is skipped: first run | 26/35 | 32/36 |
| Same, every run (11 cases repeated 3–5 times), averaged per question | 27.5/35 | 30.7/36 |
| Answers saying "excerpts" or "passages" | 2/35 | 0/36 |
| Tokens in / out (43 questions, first run) | 73,635 / 12,755 | 78,236 / 14,868 |
| Per answer, every run averaged per question: in / out | 1,731 / 355 | 1,835 / 395 |

Output grew mostly as hidden reasoning (visible answers about the same length). In tokens the change is about even:
~145 more per answer (~5,200 on 36) against ~3.2 more answers skipping the ~1,900-token answer check (~6,100). The
first run alone suggested a clear saving (6 more skips); the repeats show it was partly luck. The gain is mainly in
the answers: no short answers lost, no "excerpts", more sentences cited. Remaining citation-check failures are mostly
the checker's: "25 May 2018 [1]" is in the passage, and comparison sentences that paraphrase two passages
(`cmp-bm25-tfidf` fails 0/5 with the new prompt, 3/5 with the old) can't be matched word for word; that is the next
review (the answer checker). The one real partial answer (`ma-ccs`: the documents don't name the framework or
database) still doesn't say so with either prompt.

Results: `benchmark/results/rag_prompt_eval.json` (`#2`, `#3` keys are the repeats), old prompt in
`rag_prompt_old.json`. Two repeat runs wrote this file at the same time and each saved over the other's rows, so it
holds 65 of the 79 old and 80 new runs; the every-run counts above come from the run logs. Don't start a second run
while one may still be writing the same file.

### 21.19 Answer checker: false alarms on correct answers (14 November)

`evaluation/citations.py`. On the 115 document answers from §21.18, the code check flagged 25. Read one by one
against their passages, 13 were the checker's mistakes, not the answer's:

| Cause | Example | Fix |
|---|---|---|
| Sentence cut at an abbreviation | "10 M/2 % vs. 20 M/4 %", "Art. 33" | no split after vs., e.g., i.e., cf., Art., No., Fig., pp., approx., titles |
| Bold list heading with a dash read as a claim | "**Sector-specific investments** –" | headings may end in a dash |
| Decimal without its leading zero | passage ".5? .05? .01?", answer "0.5, 0.05, 0.01" | passage ".05" also reads as "0.05" |
| Millions written as billions | "$37.6 billion" from a table in $ millions (37,568) | accepted when the sentence says billion, a cited passage says million and the rounding matches (also million/thousand) |
| The question's name for the topic isn't in the passages | "The GDPR began to apply on 25 May 2018" (the regulation says "this Regulation") | question words that no passage contains are left out of the sentence's support |

For the last one, counting those words as *found* was tried first: it let through an answer the 120B had flagged
(`open-gdpr-sme`, whose opening "The GDPR tries to accommodate small businesses" is the model's own judgment).
Leaving them out of the count flags it again.

Not changed: comparison sentences that draw their own contrast (`cmp-bm25-tfidf`: "plain TF-IDF … without such
length normalisation" is an inference, not something passage [2] says), so they go to the 120B check; and `sum-who`,
which cites [4] for dates that are in [1] and [3], a real misattribution.

| | Before | After |
|---|---|---|
| 115 document answers (§21.18) flagged | 25 | 12 |
| Earlier checker sets (23 + 20 document, 20 + 12 web answers): agreement with the 120B, broken answers caught | — | identical to before |
| Fresh: 23 questions in neither set, answered with today's prompt, 120B verdicts (`run_checker.py --validate`) | 23/23 code ok | 23/23 code ok; the 120B's one flag (`sum-wb`, "$40 billion over 10 years") is in cited passage [6], its own mistake as on 3 November |

The fresh set is mostly fact questions (16 fact, 6 definition, 1 summary), where none of the fixes apply, so it shows
that nothing broke, not the gain. Wrong citations are caught 12/22 there with the old and the new code alike: in fact
questions the passages often come from the same document and share their words, so a swapped citation still matches.

**Prompt (§21.18) and checker together**, share of document answers that pass the code check and so skip the 120B
check (answers in high-relevance matches). Averaged per question over the runs kept in `rag_prompt_eval.json` (65
of ~80 per prompt, see §21.18; with the old checker they give 27.7 and 31.0, close to the 27.5 and 30.7 counted
from the logs):

| | Old checker | New checker |
|---|---|---|
| Old prompt (35 answers) | 27.7 (79%) | 31.3 (89%) |
| New prompt (36 answers) | 31.0 (86%) | **33.0 (92%)** |

The checker fixes cost no tokens; with the new prompt they skip ~2 more answer checks per 36 answers (~3,800
tokens).

Results: `benchmark/results/checker_eval_nov14.json`; the other checker files were re-scored (`--rescore`).

### 21.20 A name the user gives the assistant is a nickname (14 November)

A user can name the assistant ("can i call you Arataki Itto?"); memory saves it as `assistant_name`. On an account
named that way before the persona existed, the saved fact won over the persona: "besides the name i gave you, what
is your actual name?" got "I go by the one you set, Arataki Itto", and "hroptatyr?" got "No, my name is Arataki Itto,
just as you set it". Decision (user): the nickname stays and the assistant answers to it, but its name is still
Hroptatyr; asked its name, it says "I'm Hroptatyr, but I also go by Arataki Itto, the name you gave me".

`direct_answer.nickname_note` adds this to the saved-memories message (direct answers and small talk) only when an
`assistant_name` other than Hroptatyr is saved, so other prompts are unchanged. The first wording also made a plain
"hey itto, how's it going?" reintroduce itself every time (0/3 without name talk); the note now says to just answer
when called by the nickname.

`benchmark/run_nickname.py`, router + writer with the user's saved facts, 3 runs each: name questions, the real
conversation's two turns with its history, a greeting using the nickname, an ordinary question, and accounts with
no nickname or the nickname "hroptatyr".

| | Before | After |
|---|---|---|
| Name questions give both names (itto-name, itto-who, itto-other, real-actual, real-hropt) | 0/10 (2 runs; real-hropt: the nickname only, 2/2) | 15/15 |
| "Introduce yourself" never gives only the nickname | 1/2 | 3/3 |
| Greeting by nickname, no name talk | 2/2 | 8/9 |
| Ordinary question, no name talk / no nickname or "hroptatyr" saved: unchanged | 2/2 / 4/4 | 3/3 / 6/6 |

Results: `results/nickname_before.json`, `nickname_after.json` (`nickname_first_try.json`: the first wording).

---

## 22. Document library

`backend/library/`, merged in from the renaming pipeline (design: `docs/superpowers/specs/2026-11-14-merge-renamer-design.md`).
It owns every document: the catalog, the stored files (`data/library/<user_id>/`, named after their titles), indexing
(day7's ingestion pipeline, one file at a time), and every change.

### How a request reaches it
The router sends document requests ("rename the GDPR one", "change 2023 to 2024 in my resume", "undo that") to the
`manage` node, which runs the **document agent** (`agent.py`): the 120B with tools to find documents, read their
details, rename, edit, undo / redo and queue tasks. Decisions that protect the library are made in code, not left to
the prompt: a document must be identified before it is changed; a near match, a name another document has, or
converting a PDF is asked about first; an answer like "yes" or "the second one" goes back to the agent without the
router. Short follow-ups to its last edit or question ("regenerate it", "make it shorter", a misspelled "undo",
which is asked about first) stay with it.

### Renaming
`library.rename` changes the title in the catalog, the stored file's name and the `source` / `title` / `file_name` of
the document's chunks in Qdrant together, with no re-embedding. The history is kept in `renames`.

### Editing
`editing.py` replaces a passage or appends text, then re-indexes the document. TXT, MD and DOCX are edited directly.
Each edit keeps a backup in `data/versions/<doc_id>/`.

**PDFs are edited in place** (`pdf_edit.py`). A PDF holds letters at positions, so the module rebuilds the lines and
paragraphs, then re-typesets as little as possible: the changed line, or the paragraph re-wrapped (TeX-style line
breaking, the document's own hyphenation) in the same fonts, sizes, colours, kerning and justification. When the text
grows, what is below moves down; what no longer fits goes onto a new page inserted after it, carrying the running
headers and footers, with page numbers renumbered; a paragraph can continue across the break. Every edit is checked
before it is saved: letter by letter, the whole text in reading order, and pixel by pixel outside the changed area.
Letters the PDF's fonts lack are taken from complete copies (`font_fetch.py`: downloaded on demand from one pinned
HTTPS URL each, verified against `fonts/manifest.json`, cached in `data/fonts/fetched/`; Arial, Times New Roman,
Calibri and similar are drawn with metric-compatible free fonts, and the reply says so), cut down to just those
letters (`tiny_font.py`). When the agent wrote the text and a letter is still missing, it rewords around it.
When the look can't be kept, the user is asked whether to convert the PDF to a Markdown copy instead.

### Undo and redo
Undo puts back the backup of the document's latest edit; Redo brings back the edit undone most recently, under its
own id. They step one edit at a time, like a browser's Back and Forward, and a new edit ends what Redo can bring back.
The buttons (in the chat and on the Documents page) and a misspelled "undo" ask first.

### The queue
Changes to many documents ("rename every report by its date") are queued as a batch (`batch.py`). A background worker
runs one task at a time within Groq's tokens-per-minute limit (`limits.py`, shared with the chat); structured changes
run in code with no model call, and a checkpoint makes them safe across a restart. The Documents page shows the
queue with Cancel; the chat's side panel shows what the tasks changed.
