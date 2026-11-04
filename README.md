# Agentic RAG

> 📘 Full technical documentation: [`docs/DOCUMENTATION.md`](docs/DOCUMENTATION.md) (architecture, agent workflow, memory, API, configuration,
> and a [guide to every folder and file](docs/DOCUMENTATION.md#6-project-structure-and-file-guide)).
> `docs/CODE_REFERENCE.md` and `docs/GRAPH_EXPLAINED.md` explain the first version line by line.

A full-stack agentic RAG app. Users log in, upload documents into a per-user vector store, and chat
with an agent that decides for each message whether to **chat**, **answer from their documents (RAG)**,
**search the web**, or **answer from its own knowledge**. It rewrites and expands weak queries and
checks its own answers before replying.

| Layer | Tech |
|---|---|
| Frontend | React 18 + Vite + Tailwind, `react-markdown` |
| API | FastAPI, SSE streaming, JWT auth (bcrypt), SQLite |
| Agent | LangGraph state machine |
| LLMs (Groq) | `openai/gpt-oss-120b`: every reply (answers and small talk), the knowledge gate, answer evaluation, decision reviews. `openai/gpt-oss-20b`: routing and query rewrites. `qwen/qwen3.8-27b`: memory extraction and conversation summaries (own rate limit, no hidden reasoning) |
| Embeddings | Gemini `gemini-embedding-001` (768-d) for documents; Voyage `voyage-4-lite` for conversation memory |
| Reranking | Voyage `rerank-2.5` (hosted); the local cross-encoder `ms-marco-MiniLM-L-6-v2` is optional (`RERANKER_PROVIDER=local` or `RERANKER_LOCAL_FALLBACK=true`) |
| Vector DB | Qdrant (embedded on-disk by default, or a server via `QDRANT_URL`) |
| Web search | Tavily if `TAVILY_API_KEY` is set, then DuckDuckGo, then Wikipedia |

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

## Repository layout

| Folder | Holds |
|---|---|
| `backend/api/` | HTTP routes (auth, chat + SSE streaming, documents, health), JWT and password security |
| `backend/config/` | Settings, model clients per role, token accounting |
| `backend/models/` | SQLite data access and API schemas |
| `backend/graph/` | The LangGraph agent: state, nodes, edges |
| `backend/tools/` | Answer writers (direct, documents, web), small talk, memory extraction |
| `backend/rag/` | Ingestion, Qdrant vector store, hybrid search, reranking |
| `backend/memory/` | Conversation memory (Voyage embeddings in Qdrant) |
| `backend/evaluation/` | Code citation check, LLM answer check, decision reviews |
| `backend/tests/` | Unit tests (no API calls) |
| `frontend/` | React app: pages, components, API client, chat store |
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
TAVILY_API_KEY=...
VOYAGE_API_KEY=...         # reranking and conversation memory
OPEN_ROUTER_API_KEY=...    # only for the Nemotron experiments
JWT_SECRET=...            # auto-generated into backend/data/.jwt_secret if omitted
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

Create an account on the login screen, upload files on **Documents**, then chat.

Smoke test (backend must be running): `python test_rag_pipeline.py [path/to/file.pdf]`

Unit tests (70, no API calls): `python -m unittest discover backend/tests -p "test_*.py"` (routing, memory, streaming,
citation check, reranker fallbacks)

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
| POST | `/api/auth/register`, `/api/auth/login` | returns a bearer token |
| GET | `/api/auth/me` | |
| GET / POST / DELETE | `/api/documents`, `/api/documents/upload`, `/api/documents/{id}` | PDF, DOCX, TXT, MD; deduplicated by SHA-256 |
| POST | `/api/chat` | full JSON response (answer, tool, sources, trace, evaluation) |
| POST | `/api/chat/stream` | SSE stream: `meta` → `step`… → `final` |
| GET / DELETE | `/api/conversations`, `/api/conversations/{id}/messages`, `/api/conversations/{id}` | |
| GET | `/api/stats` | Overview page metrics |

Tuning knobs (chunk size, `RETRIEVAL_K`, `VOYAGE_RELEVANCE_HIGH` / `VOYAGE_RELEVANCE_LOW`, `MAX_RETRIEVAL_ATTEMPTS`, models, …) live in
`backend/config/settings.py`. Each one can be overridden from `.env`.
