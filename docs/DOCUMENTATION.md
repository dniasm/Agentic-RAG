# Agentic RAG — Project Documentation

> Full technical documentation, updated 2 November 2026. For a quick start, see [`README.md`](../README.md).

## Table of contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Tech stack and model roles](#3-tech-stack-and-model-roles)
4. [Getting started](#4-getting-started)
5. [Configuration reference](#5-configuration-reference)
6. [Project structure](#6-project-structure)
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

What makes it *agentic* rather than a fixed pipeline:

- **Source choice follows the user.** The intake step records which source the user wants (or `auto`), and that
  preference sets the order in which sources are tried. One fallback step moves to the next source when one falls
  short, and a notice tells the user why.
- **Knowledge gate.** Before answering from its own knowledge, the large model checks whether it can: open questions
  (opinions, philosophy, hypotheticals) pass; factual ones need high confidence; anything time-sensitive or about the
  user's own files goes to another source.
- **Query rewriting** — vague or context-dependent questions are rewritten before searching.
- **Self-checks** — a local cross-encoder judges whether retrieved passages really answer the question, and the large
  model checks finished answers for sufficiency and groundedness.
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
                │  /api/*  (Vite dev proxy → 127.0.0.1:8000, Bearer JWT)
┌───────────────▼──────────────────── FastAPI (:8000) ──────────────────────────┐
│  routes/auth ─ routes/chat ─ routes/documents ─ routes/health                 │
│        │            │   └── memory worker (background) ── tools/memory        │
│  security.py   graph/workflow.py (LangGraph agent)      rag/ingestion.py      │
│        │            │   │   │                                │                │
│        ▼            │   │   └── tools/web_search ──► Tavily / DuckDuckGo / Wikipedia
│  SQLite (app.db)    │   └────── rag/retrieval + rerank ──┐                    │
│  users, user_facts, │                                    ▼                    │
│  documents,         │                    Qdrant: documents (dense + BM25)     │
│  conversations,     │                            user_memories (Voyage)       │
│  messages           ├── Groq: gpt-oss-120b, gpt-oss-20b, qwen3.8-27b          │
│                     ├── Gemini embeddings (documents) · Voyage (memory)       │
│                     └── local cross-encoder (MiniLM)                          │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Request lifecycle (chat):**

1. The frontend POSTs the message to `/api/chat/stream` with the user's JWT.
2. `get_current_user` validates the token. `_prepare` loads or creates the conversation, the last 6 messages, the
   user's remembered facts, up to 3 relevant past-conversation summaries and the user's indexed document names, and
   saves the user message.
3. The agent runs in its own thread (`stream_agent`), with a `UsageTracker` counting tokens. Its events (`status`,
   `step`, `draft`) are relayed to the browser as Server-Sent Events.
4. The final state is saved as an assistant message (sources, notices, trace, evaluation, latency, token usage) and
   sent as the `final` event. Because the agent runs in its own thread, the answer is saved even if the browser
   disconnects.
5. In the background, one job at a time, the memory model updates the user's facts and summarises the exchange; then
   the request's token use is logged.

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
| Reranking | Voyage `rerank-2.5` (hosted); local cross-encoder `ms-marco-MiniLM-L-6-v2` optional | Confidence zones + MMR diversity; LLM check if no reranker can score |
| Memory embeddings | Voyage AI `voyage-4-lite`, 1,024 dims | Conversation summaries only |
| Vector DB | Qdrant — embedded on-disk by default, or a server via `QDRANT_URL` | Cosine distance |
| Web search | Tavily (if key set) → DuckDuckGo → Wikipedia | Results cached for an hour |
| Persistence | SQLite (stdlib `sqlite3`) | Users, facts, documents, conversations, messages |
| Auth | bcrypt password hashing, hand-rolled HS256 JWT (stdlib `hmac`) | No extra JWT dependency |
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
- A **Groq** API key and a **Gemini** API key. A **Voyage** key is needed for reranking (without it, set
  `RERANKER_PROVIDER=local` or the LLM relevance check is used) and conversation memory. Tavily is optional.

### 1) Environment file

Create `.env` in the project root:

```env
GROQ_API_KEY=your-groq-key
GEMINI_API_KEY=your-gemini-key
# Optional
TAVILY_API_KEY=your-tavily-key
VOYAGE_API_KEY=your-voyage-key        # reranking + conversation memory
OPEN_ROUTER_API_KEY=...               # only for scripts/experiments
JWT_SECRET=some-long-random-string
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
Reranking is hosted (Voyage), so nothing heavy loads at startup. With `RERANKER_PROVIDER=local` or
`RERANKER_LOCAL_FALLBACK=true`, the local cross-encoder loads in the background (about 11 s, ~450 MB).

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

1. Open the app and **create an account** (username 3–32 chars `A-Z a-z 0-9 _ . -`, password ≥ 6 chars).
2. Go to **Documents** and upload a PDF/DOCX/TXT/MD file.
3. Start a **New chat** and ask questions. Under each answer, **Sources** and **Steps** show where it came from and
   the path the agent took. The theme switch (System / Light / Dark) is at the bottom of the sidebar.

Runtime data is created automatically:

| Path | Contents |
|---|---|
| `backend/data/app.db` | SQLite database |
| `backend/data/embedding_cache.db` | Cached question embeddings |
| `backend/data/token_usage.jsonl` | One line per request: tokens per model and step, route, path |
| `backend/data/.jwt_secret` | Auto-generated JWT secret (if `JWT_SECRET` unset) |
| `backend/vector_db/qdrant_data/` | Embedded Qdrant storage (documents and memory collections) |
| `backend/documents/<user_id>/` | Uploaded original files |

To reset everything: stop the backend and delete those paths.

---

## 5. Configuration reference

All settings live in `backend/config/settings.py` (`Settings`, a pydantic-settings class). Every field can be
overridden by an environment variable / `.env` entry with the same name (case-insensitive).

| Setting | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` / `GEMINI_API_KEY` | — | **Required.** |
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
| `UPLOAD_DIR` / `MAX_UPLOAD_MB` | `backend/documents` / `20` | Uploads. |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1000` / `200` | Characters per chunk and overlap. |
| `RETRIEVAL_K` | `6` | Passages finally used for an answer. |
| `RETRIEVAL_MODE` | `hybrid` | `hybrid` (dense + BM25 with RRF) or `dense`. |
| `HYBRID_CANDIDATES` / `RRF_K` | `20` / `60` | Results per list before fusion; RRF constant. |
| `BM25_K1` / `BM25_B` / `BM25_AVG_LEN` | `1.2` / `0.75` / `120` | BM25 parameters. |
| `RERANK_ENABLED` | `True` | Use a reranker (otherwise the LLM relevance check). |
| `RERANKER_PROVIDER` | `voyage` | `voyage` (hosted) or `local` (MiniLM cross-encoder). |
| `VOYAGE_RERANK_MODEL` / `VOYAGE_RERANK_TIMEOUT` | `rerank-2.5` / `15` | Hosted reranker; a slow call falls back instead of waiting. |
| `VOYAGE_RELEVANCE_HIGH` / `VOYAGE_RELEVANCE_LOW` | `0.55` / `0.45` | Confidence zones on Voyage's scale (calibrated). |
| `RERANKER_LOCAL_FALLBACK` | `False` | Keep the local model loaded as a backup for Voyage (~450 MB RAM, torch). |
| `RERANKER_MODEL` / `RERANKER_MAX_LENGTH` | MiniLM-L-6-v2 / `256` | Local cross-encoder. |
| `RERANK_CANDIDATES` / `RERANK_TOP_N` / `MMR_LAMBDA` | `20` / `12` / `0.7` | Candidates scored; pool MMR picks from; relevance vs diversity. |
| `RELEVANCE_HIGH` / `RELEVANCE_LOW` | `0.05` / `0.01` | Confidence zones for the local model (MiniLM's score scale). |
| `WEAK_MATCH_RETRIES` | `1` | Extra reworded searches in the middle zone. |
| `MIN_RELEVANCE_SCORE` / `RAG_SKIP_SCORE` | `0.45` / `0.52` | Used only when the reranker is unavailable. |
| `MAX_RETRIEVAL_ATTEMPTS` / `MAX_WEB_ATTEMPTS` | `1` / `2` | Search rounds per source. |
| `CITATION_PRECHECK` | `True` | Check document answers' citations in code first; skip the 120B check when they hold up and the evidence zone is high. |
| `VERIFY_DECISIONS` / `MAX_REVIEW_ROUNDS` / `REVIEW_ONLY_UNCERTAIN` | `True` / `2` / `True` | 120B reviews the 20B's uncertain decisions. |
| `WEB_RESULTS` / `TAVILY_SEARCH_DEPTH` / `WEB_CACHE_TTL_SECONDS` | `5` / `basic` / `3600` | Web search. |
| `HISTORY_TURNS` | `6` | Past messages passed to the agent. |
| `DATABASE_PATH` | `backend/data/app.db` | SQLite file. |
| `JWT_SECRET` / `JWT_EXPIRE_MINUTES` | `""` / `1440` | Token signing key (auto-generated if empty); lifetime. |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Allowed origins (any localhost port is also allowed). |
| `BOT_CONTACT` | `https://github.com/` | Contact in the User-Agent (required by Wikipedia's API). |

Frontend: `VITE_API_URL` (optional) overrides the API base URL; by default the app calls `/api` and the Vite dev
server proxies it to `http://127.0.0.1:8000`.

---

## 6. Project structure

```
day7/
├── .env                        # secrets (not committed)
├── README.md                   # quick start
├── docs/                       # this file, CODE_REFERENCE.md, GRAPH_EXPLAINED.md
├── requirements.txt
├── app.py                      # re-exports backend/app.py (uvicorn app:app from the root)
├── test_rag_pipeline.py        # end-to-end smoke test
├── scripts/experiments/        # one-off model experiments (call the APIs)
├── backend/
│   ├── app.py                  # FastAPI app, lifespan, CORS, router mounting
│   ├── config/
│   │   ├── settings.py         # all configuration
│   │   ├── llm.py              # model clients per role, quota fallback, structured output
│   │   └── usage.py            # token accounting per model and step
│   ├── api/
│   │   ├── security.py, dependencies.py
│   │   └── routes/             # auth.py, chat.py, documents.py, health.py
│   ├── models/
│   │   ├── database.py         # SQLite schema + data access (incl. user_facts)
│   │   └── schemas.py          # pydantic request/response models
│   ├── rag/
│   │   ├── ingestion.py        # load → clean → chunk → embed (dense + BM25) → upsert
│   │   ├── vectorstore.py      # Qdrant client, embeddings, per-user filters
│   │   ├── sparse.py           # BM25 sparse vectors
│   │   ├── embedding_cache.py  # on-disk cache of question embeddings
│   │   ├── retrieval.py        # hybrid multi-query retrieval + RRF
│   │   └── rerank.py           # cross-encoder, confidence zones, MMR
│   ├── tools/
│   │   ├── __init__.py         # shared helpers (history, date, citation cleanup)
│   │   ├── greeting.py         # small talk (delegates to direct_answer.converse)
│   │   ├── direct_answer.py    # knowledge gate + model-knowledge answers + small talk
│   │   ├── rag.py              # grounded answer generation
│   │   ├── web_search.py       # search providers + web answer generation
│   │   └── memory.py           # fact extraction/merge rules + exchange summaries
│   ├── memory/
│   │   ├── embeddings.py       # Voyage client
│   │   └── store.py            # conversation memory in Qdrant
│   ├── evaluation/
│   │   ├── evaluator.py        # LLM passage grading (fallback) + answer evaluation
│   │   ├── citations.py        # answer check in code: cited, citations real, content matches
│   │   └── reviewer.py         # small model proposes, large model reviews
│   ├── graph/
│   │   ├── state.py            # AgentState
│   │   ├── nodes.py            # every graph node + source order + notices
│   │   └── workflow.py         # edges, routing functions, run/stream helpers
│   ├── scripts/migrate_hybrid.py
│   ├── tests/                  # unit tests (no API calls) + reasoning cases
│   ├── data/ documents/ vector_db/   # runtime
├── frontend/
│   ├── index.html, preview.html, vite.config.js, tailwind.config.js, postcss.config.js, package.json
│   ├── public/mark.svg
│   └── src/
│       ├── main.jsx, App.jsx, index.css
│       ├── services/api.jsx            # HTTP + SSE client, token storage
│       ├── state/chatStore.js          # conversations + in-flight answers, outside the pages
│       ├── lib/route.js, format.js, theme.js
│       ├── pages/Login.jsx, Chat.jsx, Documents.jsx, Overview.jsx
│       ├── components/Sidebar.jsx, Message.jsx, InputBox.jsx, Mark.jsx
│       └── dev/fixtures.js, preview.jsx  # design preview only
└── benchmark/                  # retrieval, selection, e2e, perf, router, gate and memory benchmarks
```

---

## 7. The agent workflow

The agent is a LangGraph `StateGraph` defined in `backend/graph/workflow.py`. Nodes live in
`backend/graph/nodes.py`; each node takes the current `AgentState` and returns a partial update.

### 7.1 Graph

```
START → router ─┬─ small talk ──────────────────────────────────────────────► greeting ► END
                ├─ model knowledge / auto ─► direct ─(gate passes)─► finalize ► END
                │                               └─(gate refuses)─► fallback
                ├─ documents ─► [rewrite if vague] ─► retrieve ─► grade (cross-encoder zone)
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
| `evaluate` | code, then 120B | Document answers with a high evidence zone are checked in code first (`evaluation/citations.py`); if every claim is cited and found in its passage, the evaluation is `checked_by: "code"` and the model is not called. Otherwise the 120B returns `sufficient`, `grounded`, `score`, `missing`, checked against only the passages the answer cites (all of them if it cites none); keeps the best candidate. If no model can check the answer, it is kept with `verified: false` in its evaluation and a notice. |
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
4 controls still refused).

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
fallback validated with pydantic covers the rest. Schemas: `Route`, `Rewrite`, `KnowledgeGate`, `DocGrades`,
`AnswerEval`, `Review`, `MemoryExtraction`.

---

## 8. Memory

Two kinds, kept separate on purpose:

| | Facts | Episodes |
|---|---|---|
| What | Specific details about the user: name, location, hobbies, preferences, how they want answers | A one- or two-sentence summary of an answered exchange |
| Stored in | SQLite `user_facts`, one row per value | Qdrant collection `user_memories`, Voyage embeddings |
| Written by | `tools/memory.extract_and_save_memories` | `tools/memory.summarize_exchange` + `memory/store.save_memory` |
| Read | Every request (`db.list_memories`) | Up to 3 closest (score ≥ 0.45), each cut to 800 chars |

**Fact rules.** A cheap pre-check (`_may_contain_memory`: "I", "my", "call me", "from now on", "keep your answers…",
punctuation ignored) skips the model for impersonal messages. Otherwise Qwen reports only what to *remember* or
*forget*; `plan_changes` decides in code how storage changes:

- single-value keys (`preferred_name`, `location`, `occupation`, …, and any `favorite_*`, `preferred_*`, `current_*`) —
  a new value replaces the old;
- other keys (`hobbies`, `liked_foods`, …) — values are collected;
- forget — removes a value, or the whole key for `*`. A correction ("I don't play futsal any more") is forget + remember.

Keys are normalised (snake_case, one spelling of "favourite", aliases such as `city` → `location`). Values compare
case-insensitively. Deleting a conversation also deletes its episodes.

Memory work runs **after** the reply, on a single background worker, so it adds no waiting time and two quick
messages can't update the same keys at once.

The original one-row-per-key `user_memory` table was migrated into `user_facts` once (recorded in `migrations` as
`user_facts_v1`) and kept, unused, as a backup.

---

## 9. Ingestion pipeline

`POST /api/documents/upload` → `routes/documents.py::upload_document` → `rag/ingestion.py::ingest_file`.

1. **Validate** — extension in `{.pdf, .docx, .txt, .md}`; size ≤ `MAX_UPLOAD_MB` (reads at most limit+1 bytes);
   filename sanitised.
2. **Deduplicate** — SHA-256 of the bytes. Same user, already indexed → `status: "duplicate"`. A failed copy is cleaned up and re-ingested.
3. **Record** — a `documents` row with `status="processing"`; the file is saved as `backend/documents/<user_id>/<doc_id><ext>`.
4. **Load** — PDF: one document per page (page numbers kept for citations); DOCX: paragraphs + table rows; TXT/MD as-is.
   `_clean` removes null bytes, re-joins hyphenated line breaks, collapses whitespace. No text → `IngestionError` (422).
5. **Chunk** — 1,000 chars / 200 overlap, splitting on paragraphs → lines → sentences → words; chunks ≤ 20 chars dropped.
6. **Embed** — `embed_paced`: Gemini `RETRIEVAL_DOCUMENT`, batches of 90, waiting when the free tier's per-minute limit is hit.
7. **Upsert** — Qdrant points with ids `uuid5(doc_id:chunk_index)`, a `dense` vector and a `bm25` sparse vector, and
   payload `{page_content, metadata: {user_id, doc_id, source, page, chunk_index, start_index}}`.
8. **Finish** — `indexed` with page/chunk counts, or `failed` with the error (partial vectors removed).

Deleting a document removes its vectors, the stored file and the DB row.

---

## 10. Retrieval

`rag/retrieval.py::retrieve(queries, user_id, k, min_score, mode)`:

1. Deduplicate the query variants; embed them in parallel through the embedding cache.
2. For each variant, run dense search and BM25 search (`HYBRID_CANDIDATES` each), filtered to `metadata.user_id`.
3. Fuse with Reciprocal Rank Fusion: each chunk earns `1/(RRF_K + rank)` per list.
4. Keyword hits keep their place even with low cosine (exact terms are the point of BM25); dense-only hits must pass
   `MIN_RELEVANCE_SCORE`. If embedding fails, keyword search alone is used.

Then `rag/rerank.py` scores each candidate (0–1) with the configured reranker: Voyage `rerank-2.5` by default; the
local cross-encoder if `RERANKER_PROVIDER=local`, or as a backup when `RERANKER_LOCAL_FALLBACK=true`. If none can
score (e.g. Voyage rate-limited, no local model), `score()` returns `None` and the LLM relevance check grades the top
8 candidates. The best score sets the **zone**, using that reranker's thresholds (≥ high → answer; < low → next
source; between → one reworded retry); MMR picks `RETRIEVAL_K` diverse passages from the top `RERANK_TOP_N`.

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
`episode_summary`). It sums input, output and hidden-reasoning tokens per model and per step.

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
Qdrant collection, and starts loading the cross-encoder in the background. CORS: `CORS_ORIGINS` plus any localhost port.

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
| Conversations | `create_conversation`, `get_conversation`, `list_conversations`, `rename_conversation` (unused), `delete_conversation` |
| Messages | `add_message`, `list_messages(conv_id, limit)` |
| Stats | `user_stats` |

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
| `grade_documents(query, docs)` | Relevant subset + review steps | Used only without the cross-encoder |
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

### Passwords
- `_prehash`: SHA-256 → base64 (44 bytes), so passwords longer than bcrypt's 72-byte limit remain fully significant.
- `hash_password`: bcrypt with a random salt (cost 12). Only the hash is stored.
- `verify_password`: `bcrypt.checkpw`; malformed hashes return `False`.

### Tokens (JWT, HS256)
- `create_access_token(user_id, username)` → `header.payload.signature` (base64url, HMAC-SHA256); payload
  `{sub, name, iat, exp}`.
- `decode_access_token(token)` verifies the signature in constant time, requires `alg == HS256`, rejects expired tokens.
- Secret: `JWT_SECRET`, or a random value generated once into `backend/data/.jwt_secret`.

### Request guard
`dependencies.get_current_user` reads the `Authorization: Bearer` header, decodes the token, confirms the user still
exists and returns `{id, username}`. Every failure returns the same `401 Not authenticated`; the frontend then shows
the login screen.

### Data isolation & input safety
- Every SQL query filters by `user_id`; every Qdrant query (documents and memories) filters by the user.
- SQL uses placeholders only; dynamic column names in `update_document` are allow-listed.
- Upload filenames are sanitised and files stored under generated ids. Upload size is enforced while reading.
- Login errors don't reveal whether a username exists.

---

## 14. Data model

### SQLite (`backend/data/app.db`)

```
users ──< documents
  ├──< user_facts
  └──< conversations ──< messages
```

| Table | Columns | Constraints |
|---|---|---|
| `users` | `id`, `username`, `password_hash`, `created_at` | `username` unique, case-insensitive |
| `user_facts` | `id`, `user_id`, `memory_key`, `value`, `created_at`, `updated_at` | `UNIQUE(user_id, memory_key, value)`, `value` case-insensitive |
| `migrations` | `name`, `applied_at` | One-off data migrations applied |
| `user_memory` | `id`, `user_id`, `memory_key`, `memory_value`, … | Pre-migration backup, no longer written |
| `documents` | `id`, `user_id`, `filename`, `file_hash`, `size_bytes`, `pages`, `chunks`, `status`, `error`, `created_at` | `UNIQUE(user_id, file_hash)` |
| `conversations` | `id`, `user_id`, `title`, `created_at`, `updated_at` | cascade on user delete |
| `messages` | `id`, `conversation_id`, `role`, `content`, `tool`, `meta` (JSON), `created_at` | cascade on conversation delete |

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
| `agentic_rag_docs_v2` | `dense` (768, cosine) + `bm25` (sparse, IDF modifier) | `{page_content, metadata: {user_id, doc_id, source, page, chunk_index, start_index}}` |
| `user_memories` | one unnamed vector (1,024, Voyage) | `{user_id, text, kind, conversation_id, updated_at}` |

---

## 15. HTTP API reference

Base URL `http://localhost:8000/api`. All endpoints except `health`, `auth/register` and `auth/login` require
`Authorization: Bearer <token>`. Errors have the shape `{"detail": "message"}`.

| Method & path | Body | Response |
|---|---|---|
| `GET /health` | — | `{"status": "healthy", "llm", "embeddings", "web_search": "tavily → duckduckgo → wikipedia"}` |
| `POST /auth/register` | `{"username", "password"}` | `201` `{"access_token", "token_type", "user"}` — `409` taken, `422` invalid |
| `POST /auth/login` | `{"username", "password"}` | same — `401` on bad credentials |
| `GET /auth/me` | — | `{"id", "username"}` |
| `GET /documents` | — | `[DocumentOut]` newest first |
| `POST /documents/upload` | multipart `file` | `{"status": "success" \| "duplicate", "filename", "chunks_indexed", "document"}` — `400`, `413`, `422`, `500` |
| `DELETE /documents/{id}` | — | `204` |
| `POST /chat` | `{"message", "conversation_id"?}` | `ChatResponse` (below) |
| `POST /chat/stream` | same | SSE (below) |
| `GET /conversations` | — | `[{id, title, created_at, updated_at}]` |
| `GET /conversations/{id}/messages` | — | `[{id, role, content, tool, meta, created_at}]` oldest first |
| `DELETE /conversations/{id}` | — | `204` (messages and remembered episodes removed) |
| `GET /stats` | — | `{documents, chunks, conversations, queries, by_tool, queries_rewritten, answers_evaluated, answers_sufficient, avg_latency_ms}` |

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
   ├── pages/Documents        → getDocuments / uploadDocument / deleteDocument
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
| `services/api.jsx` | Token in `localStorage` (`agentic_rag_token`); `request()` adds the bearer token and handles errors (401 → login); `streamMessage()` reads the SSE stream (`onMeta`, `onStatus`, `onStep`, `onDraft`, `onFinal`). |
| `pages/Login.jsx` | Sign-in / sign-up with the backend's rules, offline notice, and an example answer. |
| `pages/Chat.jsx` | New chat with suggestions based on your documents; otherwise the conversation and composer. |
| `pages/Documents.jsx` | Drop zone + picker, type/size checks, upload progress, status list, delete with inline confirmation. |
| `pages/Overview.jsx` | Key numbers, where answers came from, document library. |
| `components/Sidebar.jsx` | New chat, chats grouped by recency (a dot marks one still answering; inline delete confirmation), navigation, theme switch, user and log out. |
| `components/Message.jsx` | Answer with notices, Markdown and citation marks; a provenance line (source, checked or not, time); **Sources** and **Steps** disclosures; live status/steps/draft while pending. |
| `components/InputBox.jsx` | Growing textarea: **Enter** sends, **Shift+Enter** or **Ctrl+Enter** adds a line; drafts kept per chat. |
| `index.css`, `tailwind.config.js` | Colour tokens as CSS variables for both themes, type scale, component classes, `.markdown` styles. |
| `preview.html`, `src/dev/` | Design preview with sample data (dev server only). |

### Dev proxy
`vite.config.js` proxies `/api` to `http://127.0.0.1:8000`. For a production build, serve `frontend/dist` behind the
same origin as the API, or set `VITE_API_URL`.

---

## 17. Testing and benchmarks

**Unit tests** (no API calls, ~1 s): `python -m unittest discover backend/tests -p "test_*.py"`

| File | Covers |
|---|---|
| `test_routing.py` | Source orders, documents skipped without uploads, single fallback, auto → model knowledge, retry budgets, partial RAG answers, unchecked answers kept, web recency rules, knowledge-gate rules, memory pre-check |
| `test_memory.py` | Key normalisation, merge rules (replace / collect / forget / corrections), storage, one-time migration |
| `test_stream.py` | The stream ends with `final`; the answer is saved after the client disconnects |

**Smoke test** (backend running): `python test_rag_pipeline.py [path/to/file.pdf]` — registers a throwaway user and
asks one question per path.

**Benchmarks** (`benchmark/`, see each script's docstring; retrieval ones need the backend stopped):

| Script | Measures | Latest result |
|---|---|---|
| `run_retrieval.py --stage …` | top-k, MRR, out-of-scope detection | MRR 0.774 → 0.822 (hybrid) → 0.872 (reranked); OOS 100% |
| `run_selection.py --collect / --compare` | Passage-selection methods: coverage, precision, tokens | Keep MMR, 6 passages (§21.8) |
| `run_e2e.py`, `run_perf.py` | Full answers; startup, memory, speed | Local rerank of 20 candidates ≈ 0.7 s; +450 MB |
| `calibrate_reranker.py` | Voyage vs local reranker on saved pools; zone thresholds | 74 questions: top-5 100%, MRR 0.930 (local 0.830) |
| `run_router.py` | 80 routing cases | 73/80 exact, 79/80 acceptable, 0/41 non-document messages sent to RAG |
| `run_direct_gate.py [--gate-only]` | Opinion/general questions answered from model knowledge | Gate: 3/18 → 18/18; controls 4/4; full path 22/22 |
| `run_memory.py` | 18 memory conversations | 18/18; 20/20 facts; 0 stale; 0 false saves |
| `run_checker.py [--fresh] [--rescore]` | Code citation check vs the 120B answer check, plus deliberately broken answers | 43 answers: 0 real problems missed; 187/190 broken answers caught (§21.9) |

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
| Memory not recalled from past chats | `VOYAGE_API_KEY` missing or Voyage unavailable (logged); facts still work. |
| Steps show "chunks judged relevant" instead of "Reranked … with Voyage" | Voyage failed (usually its rate limit: the free tier without a payment method allows 3 requests / 10,000 tokens per minute) and the LLM check was used. Add a payment method on Voyage, or set `RERANKER_LOCAL_FALLBACK=true`. |
| Upload returns 422 "No extractable text" | Scanned/image-only PDF. OCR it first. |
| Web search returns nothing | DuckDuckGo blocking or rate limiting. Add a `TAVILY_API_KEY`. |

---

## 19. Extending the system

**Add a source/tool:** add it to `Route.source_preference` and `ROUTER_PROMPT`; implement it in `backend/tools/`
and a node in `nodes.py` that sets `answer`, `tool`, `trace`; add it to `_source_order`, `SOURCE_NODE`,
`FALLBACK_NOTICES` and `build_graph()`; label it in `Message.jsx` and `lib/format.js`.

**Support a new file type:** extend `SUPPORTED_EXTENSIONS` and `load_file` in `rag/ingestion.py`, and `ACCEPTED`
in `pages/Documents.jsx`.

**Swap models:** change `LLM_MODEL` / `FAST_LLM_MODEL` / `MEMORY_LLM_MODEL`. Keep structured output working for
every schema in 7.8. Changing `RERANKER_MODEL` requires recalibrating `RELEVANCE_HIGH` / `RELEVANCE_LOW`.

**Remember a new kind of fact:** add single-value keys to `SINGLE_VALUE_KEYS` (or use a `favorite_` / `preferred_` /
`current_` prefix) and variants to `KEY_ALIASES` in `tools/memory.py`; add a case to `benchmark/memory_cases.json`.

**Scale out:** point `QDRANT_URL` to a Qdrant server, and replace SQLite with Postgres behind the same
`models/database.py` functions.

---

## 20. Known limitations

- **Voyage rate limits** — without a payment method on the Voyage account, 3 requests / 10,000 tokens per minute
  (one rerank ≈ 3,500 tokens), shared with memory embeddings; beyond that, answers use the slower LLM relevance check.
- **Free-tier limits** — each Groq model allows 8,000 tokens/minute and 200,000/day; Gemini embeddings 1,000/day.
- **Tokens cannot be revoked** — logout only removes the browser's copy; tokens live in `localStorage`.
- **Single-process embedded Qdrant** — use a Qdrant server for multiple workers.
- **Synchronous ingestion** — large files block the upload request until indexed.
- **No OCR** — image-only PDFs cannot be indexed.
- **LLM judges are fallible** — the gate, grading and evaluation reduce, but don't eliminate, wrong routes and
  ungrounded answers.
- **No rate limiting** on login or chat endpoints.

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
code flags, weaker evidence, or web answers still go to the 120B. The code cannot judge completeness or catch a
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
