# Agentic RAG

> 📘 Full technical documentation: [`docs/DOCUMENTATION.md`](docs/DOCUMENTATION.md) (architecture, agent workflow, memory, API, configuration,
> and a [guide to every folder and file](docs/DOCUMENTATION.md#6-project-structure-and-file-guide)).

A full-stack agentic RAG app. Users log in, upload documents into a per-user vector store, and chat
with an agent that decides for each message whether to **chat**, **answer from their documents (RAG)**,
**search the web**, or **answer from its own knowledge**. It rewrites and expands weak queries and
checks its own answers before replying.

The same chat also manages the documents: **rename**, **edit** (PDFs in place, keeping their fonts and layout),
**undo** and **redo**, and queues longer jobs ("rename every report by its date") to run in the background.
The **Documents** page does the same with buttons. See [Document library](#document-library).

| Layer | Tech |
|---|---|
| Frontend | React 18 + Vite + Tailwind, `react-markdown` |
| API | FastAPI, SSE streaming, cookie login (PBKDF2, server-side tokens), SQLite |
| Agent | LangGraph state machine |
| LLMs (Groq) | `openai/gpt-oss-120b`: every reply (answers and small talk), the knowledge gate, answer evaluation, decision reviews. `openai/gpt-oss-20b`: routing and query rewrites. `qwen/qwen3.8-27b`: memory extraction and conversation summaries (own rate limit, no hidden reasoning) |
| Embeddings | Gemini `gemini-embedding-001` (768-d) for documents; Voyage `voyage-4-lite` for conversation memory |
| Reranking | Voyage `rerank-2.5` (hosted); if it can't score, an LLM relevance check is used |
| Vector DB | Qdrant (embedded on-disk by default, or a server via `QDRANT_URL`) |
| Web search | Tavily if `TAVILY_API_KEY` is set, then DuckDuckGo, then Wikipedia |
| Document library | PyMuPDF + fontTools (in-place PDF edits), python-docx, fpdf2 (PDF export), pyphen (hyphenation) |

## Agent workflow

```
START → router ─┬─ greeting (small talk) ──────────────────────────────────────────────► END
                ├─ direct ──(knowledge gate refuses)──────────────► fallback
                │     └──(gate passes)───────────────────────────► finalize ► END
                ├─ rag ──(vague?)► rewrite ► retrieve ► grade ──(high)► generate_rag ► evaluate ► finalize ► END
                │                     ▲                    │(mid: one reworded retry)       │(insufficient)
                │                     └────────────────────┘                                ▼
                │                              (low / failed) ──────────────────────────► fallback
                └─ web_search ► generate_web ► evaluate ──(insufficient)► rewrite ► web_search …
   fallback ──► next untried source for the user's preference ──► … ──(all tried)► best answer or "no results"
```

- **Router** (20B) classifies small talk vs. information request, picks the preferred source (documents, web,
  model knowledge, or auto), flags vague queries, and rewrites follow-ups into standalone questions.
  The 120B reviews the decision only when the 20B isn't confident.
- **Source order** follows the preference: documents → RAG, model knowledge, web; web → web, RAG, model knowledge;
  auto starts with model knowledge, and the knowledge gate sends questions about the user's own files to RAG.
- **Direct answers** pass a knowledge gate first (120B): time-sensitive, uncertain, or file-specific questions are
  refused and move on to the next source.
- **Retrieve**: hybrid search (dense + BM25, fused with Reciprocal Rank Fusion), filtered to the current user.
- **Grade**: a reranker (Voyage, hosted) scores the candidates. High → answer, mid → one reworded search, low → next source.
  MMR picks diverse passages. If the reranker is unavailable, an LLM relevance check takes over.
- **Generate**: answers only from the selected passages, with `[n]` citations.
- **Evaluate**: document answers on strong evidence, and web answers, are checked in code first (every claim cited, every citation
  real, the cited passage contains the claim). If that passes, no model check is needed; otherwise the 120B checks
  that the answer is sufficient and grounded. If the check itself can't run, the answer is kept and marked unverified.
- **Memory**: facts about the user (name, preferences, …) are stored as key-value rows in SQLite; past exchanges
  are stored as short summaries in a Voyage-embedded Qdrant collection and recalled when relevant.
- **Tokens**: every model call is counted per model and step; each request is logged to `backend/data/token_usage.jsonl`.
- Each node emits a trace step. The UI streams these live and shows them under each answer (**Steps**, next to **Sources**).

## Document library

`backend/library/` (merged in from the renaming pipeline) owns the documents: the catalog, the stored files, indexing,
and every change to them.

- **Routing**: messages about documents ("rename the GDPR one", "fix the typo in my resume", "undo") go to the
  `manage` node, which runs the document agent (120B with tools). Short follow-ups to its last edit or question
  ("regenerate it", "yes", a misspelled "undo") stay with it.
- **Rename**: titles are cleaned and checked; a name another document has is asked about first. The stored file is
  renamed to match, and the search index is updated without re-embedding.
- **Edit** TXT, MD and DOCX directly. **PDFs are edited in place**: the paragraph is re-wrapped in the document's own
  fonts, kerning and justification; content below moves down, onto a new page when needed (headers, footers and page
  numbers follow). Every edit is checked letter by letter, by its whole text and pixel by pixel before it is saved.
  Fonts a PDF lacks are fetched on demand from pinned URLs, verified against `backend/library/fonts/manifest.json`
  (SHA-256), and cached in `backend/data/fonts/`. Quotes typed on a keyboard become the typographic ones when the
  font only has those (TeX's fonts). When the layout can't be kept, the user is asked whether to convert the PDF to a
  Markdown copy, but only when the passage would be found in it.
- **On the website**: a document's preview has **Edit a passage** (select it on the page, or type it) and, for TXT and
  Markdown, **Edit text** (the whole file; refused if it changed meanwhile). Text selected in the preview is matched
  across line-end hyphens, Markdown marks and either kind of quote. The Documents page gives each document one "⋯"
  menu (Rename, Undo / Redo, Download, Remove), each change asking first.
- **Undo / Redo** step back and forward through a document's edits like a browser's Back and Forward. The buttons
  and a misspelled "undo" ask first.
- **Queue**: jobs over many documents run one task at a time, paced to the Groq per-minute limit; the Documents page
  shows them, with Cancel.

### Using the site (for invited users)

1. Open the link you were given and choose **Create account**: a username, a password of 8 characters or more, and the
   invite code you were given.
2. **Documents** → **Upload documents** (PDF, Word, TXT or Markdown, up to 20 MB). Each is titled and indexed; it can
   be asked about once it shows **Ready**.
3. **Chat**: ask about your documents, or ask for changes ("rename the GDPR one", "change 2024 to 2025 in my CV").
   Every change has **Undo** and **Redo** under it.
4. To change a document yourself, open it (its name on the Documents page) and use **Edit a passage**. Each
   document's "⋯" menu has Rename, Undo, Redo, Download and Remove.

Your documents and chats are visible only to you. On a phone, Back closes an open editor, then the preview.

## Measured improvements

Old vs current, from the benchmarks and the token log (details: [`docs/DOCUMENTATION.md` §21](docs/DOCUMENTATION.md#21-measurements-before-and-after)):

| | Old | Current |
|---|---|---|
| Tokens per message (same 9-message test) | 6,645 | 4,344 (−35%); small model −73% |
| Answer prompt trims (8 test questions) | 16,560 tokens | 12,384 (−25%) |
| Relevance checker: right passage in top 5 (74 questions) | 92% (local MiniLM) | 100% (Voyage rerank-2.5) |
| Answerable questions answered from the documents | 65/67 | 67/67 |
| Reranker memory footprint | ~450 MB + torch | none (hosted) |
| Opinion/philosophy questions answered directly | 3/18 | 18/18 |
| Personal scenarios answered as advice plans | 2/2 | 0/10 |
| Memory benchmark (18 conversations) | 18/18 cases, 20,998 tokens | 17/18, 15,984 tokens (−24%); 0 false saves both |
| Memory model called on 80 router test messages | 10 | 2 |
| Memory saves when Qwen misnames its tool (8 tries) | 5/8 | 8/8 |
| Direct-answer prompt, factual questions and small talk | 1,022 tokens | 480 (−53%) |
| Router tokens per message (80 cases) | 962 | 875 (−9%); 73/80 exact either way |
| Knowledge gate prompt per call (31 questions, all correct either way) | ~750 tokens | ~605 (−19%) |
| Web answers calling search results "the sources you provided" (6 repeats) | 1/6 | 0/6; output tokens −11%, same facts |
| Document answers checked without the 120B (43 answers) | 0/43 | 37/43; 0 real problems missed; 187/190 broken answers caught |
| Web answers checked without the 120B (32 answers) | 0/32 | 17/32; 0 real problems missed; 124/128 broken answers caught |
| Identity probes naming the underlying model or provider (33) | 27/33 | 0/33; questions about AI models still answered |
| Persona "Hroptatyr": name given when asked / says "the Wise" | — | 9/9 / 0 |
| Agent tokens, same 8 messages (old code vs new) | 25,825 | 20,583 (−20%); greetings −64%, document requests −39% |
| Router calls on 80 router test messages | 80 | 70 (greetings and first-message document requests decided in code) |
| Memory benchmark, batched extraction (same day) | 17/18, 14,375 tokens | 17/18, 12,180 (−15%); 0 false saves |
| Memory benchmark with one retry on an empty result | 17/18, 19/20 facts (batched 17/18, 16/20) | 18/18, 20/20 (batched 18/18, 20/20); 16,224 tokens (+13%, mostly run-to-run variance; the retry fired once) |

## Repository layout

| Folder | Holds |
|---|---|
| `backend/api/` | HTTP routes (auth, chat + SSE streaming, documents, health) and the login dependency |
| `backend/library/` | The document library: catalog, files, indexing queue, document agent, rename/edit/undo, PDF editing, accounts, batch queue, its own API at `/api/library/*` |
| `backend/config/` | Settings, model clients per role, token accounting |
| `backend/models/` | SQLite data access and API schemas |
| `backend/graph/` | The LangGraph agent: state, nodes, edges |
| `backend/tools/` | Answer writers (direct, documents, web), small talk, memory extraction |
| `backend/rag/` | Ingestion, Qdrant vector store, hybrid search, reranking |
| `backend/memory/` | Conversation memory (Voyage embeddings in Qdrant) |
| `backend/evaluation/` | Code citation check, LLM answer check, decision reviews |
| `backend/tests/` | Unit tests (no API calls) |
| `frontend/` | React app: pages, components, API client, chat store; `backend/web.py` serves its build in production |
| `benchmark/` | Benchmark scripts, question sets, corpus, saved results |
| `scripts/experiments/` | One-off model experiments |
| `docs/` | Documentation |

What every file does: [`docs/DOCUMENTATION.md` §6](docs/DOCUMENTATION.md#6-project-structure-and-file-guide).

## Running it

`.env` in the project root:

```
GROQ_API_KEY=...
GEMINI_API_KEY=...
# optional
GROQ_API_KEY_2=...         # up to GROQ_API_KEY_4: keys from other Groq accounts, used when a model's limit runs out
TAVILY_API_KEY=...
VOYAGE_API_KEY=...         # reranking and conversation memory
SIGNUP_CODE=...            # invite code for every sign-up after the first (empty: sign-ups closed)
QDRANT_URL=http://localhost:6333
```

Backend (from the project root):

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --reload      # works from the project root or from backend/
```

Frontend:

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

Design preview (no backend or login needed): with `npm run dev` running, open
`http://localhost:5173/preview.html#/chat/c1` (also `#/chat`, `#/documents`, `#/overview`; add `?state=empty`
for a new account or `?state=offline` for the login screen with the server down). It renders the real app with
sample data from `frontend/src/dev/fixtures.js` and is not part of the production build.

Create an account on the login screen (the first one needs no invite code), upload files on **Documents**, then chat.

Production: `cd frontend && npm run build`, then start the backend: it serves the app at `http://localhost:8000`
(`index.html` uncached, the hashed assets cached for a year, scripts, styles and JSON gzipped; pdf.js is bundled, so
nothing loads from a CDN). Making it public over HTTPS: [`docs/DOCUMENTATION.md` §13](docs/DOCUMENTATION.md#13-authentication--security).

Tests (no API calls; the PDF layout ones take a few minutes): `pip install -r requirements-dev.txt`, then
`python -m pytest` from the project root.

Model experiments (they call the APIs) live in `scripts/experiments/`; run them from the project root,
e.g. `python -m scripts.experiments.test_gate`.

> The embedded Qdrant store allows only one process at a time. `--reload` is fine, but don't run
> several workers against the same `backend/vector_db/qdrant_data`. Set `QDRANT_URL` to use a Qdrant server instead.

## Benchmark

Scripts in `benchmark/` measure retrieval, passage selection, reranker calibration, end-to-end answers and
performance, plus routing (`run_router.py`), the knowledge gate (`run_direct_gate.py`), memory (`run_memory.py`),
the code citation check (`run_checker.py`, `run_web_checker.py`) and the answer prompts (`run_direct_prompt.py`, `run_web_prompt.py`);
see each script's docstring.
Run the retrieval ones with the backend **stopped**; the routing, gate and memory ones don't use Qdrant.

PDFs are git-ignored (third-party documents), so the benchmark's PDF inputs are not in the repo.
Place these files in the project root before running it:

| File | Document |
|---|---|
| `CCS_using_SVM.pdf` | TU BSc CSIT final-year project report, "ISP Complaint Classification and Management System Using SVM" (37 pp.) |
| `nlp.pdf` | Malla Reddy College B.Tech NLP lecture notes, R20A6609, 2024-25 (95 pp.; also used by `run_perf.py`) |
| `nlp_research_gate.pdf` | "Natural Language Processing" slides by M. Mohana, from ResearchGate (87 pp.) |
| `CELEX_32016R0679_EN_TXT.pdf` | GDPR, Regulation (EU) 2016/679 ([EUR-Lex](https://eur-lex.europa.eu/eli/reg/2016/679/oj)) |
| `World Bank Annual Report 2024.pdf` | World Bank Annual Report 2024 |
| `9789240061262-eng.pdf` | WHO primary health care report on Nepal (ISBN 978-92-4-006126-2) |

The Wikipedia articles (`benchmark/corpus/wikipedia/*.md`, CC BY-SA 4.0, source URL inside each file)
and `claude_wiki.docx` are committed. `python benchmark/build_wiki_corpus.py` re-fetches the articles.

## API

| Method | Path | |
|---|---|---|
| GET | `/api/auth/state` | signed in or not; whether sign-up is open (`first`, `invite`, `closed`) |
| POST | `/api/auth/signup`, `/api/auth/login`, `/api/auth/logout` | sets / clears the HttpOnly `login` cookie |
| GET | `/api/auth/me` | |
| GET / POST / DELETE | `/api/documents`, `/api/documents/upload`, `/api/documents/{id}` | PDF, DOCX, TXT, MD; deduplicated by SHA-256; indexed in the background (`status: "processing"`) |
| POST | `/api/chat` | full JSON response (answer, tool, sources, trace, evaluation) |
| POST | `/api/chat/stream` | SSE stream: `meta` → `step`… → `final` |
| GET / PATCH / DELETE | `/api/conversations`, `/api/conversations/{id}/messages`, `/api/conversations/{id}` | PATCH `{"title"}` renames a chat |
| GET | `/api/conversations/{id}/documents`, `/api/conversations/{id}/pending` | documents a chat changed (with the edit Undo / Redo apply to); its queued tasks still to run |
| GET | `/api/stats` | Overview page metrics |
| POST | `/api/library/documents/{id}/rename` | `{"title", "confirmed"?}`; `409` with `conflicts` when another document has the name |
| POST | `/api/library/documents/{id}/undo`, `/redo` | `{"edit_id"?}`: refused (`409`) unless that edit is still the one to undo / redo |
| GET | `/api/library/documents/{id}/file`, `/pdf`, `/preview` | download (`?inline=true` to view), as a PDF, text for the preview |
| GET / DELETE | `/api/library/queue`, `/api/library/batches/{id}` | queued tasks of the last hour; cancel a batch's waiting tasks |

Tuning knobs (chunk size, `RETRIEVAL_K`, `VOYAGE_RELEVANCE_HIGH` / `VOYAGE_RELEVANCE_LOW`, `MAX_RETRIEVAL_ATTEMPTS`, models, …) live in
`backend/config/settings.py`. Each one can be overridden from `.env`.
