# Agentic RAG

> 📘 Full technical documentation: [`docs/DOCUMENTATION.md`](docs/DOCUMENTATION.md)

A full-stack agentic RAG app. Users log in, upload documents into a per-user vector store, and chat
with an agent that decides for each message whether to **chat**, **answer from their documents (RAG)**,
**search the web**, or **answer from its own knowledge**. It rewrites and expands weak queries and
checks its own answers before replying.

| Layer | Tech |
|---|---|
| Frontend | React 18 + Vite + Tailwind, `react-markdown` |
| API | FastAPI, SSE streaming, JWT auth (bcrypt), SQLite |
| Agent | LangGraph state machine |
| LLMs | Groq: `openai/gpt-oss-120b` (answers), `openai/gpt-oss-20b` (router / grader / rewriter / evaluator) |
| Embeddings | Gemini `gemini-embedding-001` (768-d) |
| Vector DB | Qdrant (embedded on-disk by default, or a server via `QDRANT_URL`) |
| Web search | Tavily if `TAVILY_API_KEY` is set, else DuckDuckGo + page fetching |

## Agent workflow

```
START → router ─┬─ greeting ─────────────────────────────────────────────► END
                ├─ direct ──► evaluate ──(insufficient / beyond cutoff)──► web_search
                ├─ rag ──(vague?)──► rewrite ──► retrieve ──► grade ──► generate ──► evaluate ──► finalize ► END
                │                      ▲                        │           │           │
                │                      └──── no relevant chunks ┘           │           │
                │                      └──── docs don't answer ─────────────┘           │
                │                      └──── answer insufficient ───────────────────────┘
                │                   (after MAX_RETRIEVAL_ATTEMPTS → "No matches found")
                └─ web_search ──► generate_web ──► evaluate ──(insufficient)──► rewrite ──► web_search …
```

- **Router** classifies intent, flags vague queries, and rewrites follow-ups into standalone questions using chat history.
- **Rewrite / expand**: fixes vague queries *before* retrieval; on retries it uses evaluator feedback, broadens with
  step-back queries, and splits the question into sub-queries. It never repeats a query it already tried.
- **Retrieve**: multi-query dense search in Qdrant, filtered to the current user, merged and deduplicated.
- **Grade**: an LLM judge drops irrelevant chunks. If nothing relevant is left, the agent rewrites and retries.
- **Generate**: answers only from the graded chunks, with `[n]` citations. It signals when the chunks don't contain the answer.
- **Evaluate**: an LLM-as-judge checks that the answer is sufficient and grounded. If not, the agent rewrites and retries
  within budget, then returns the best-scoring candidate it produced.
- Each node emits a trace step. The UI streams these live and shows them under each answer ("Agent steps").

## Running it

`.env` in the project root:

```
GROQ_API_KEY=...
GEMINI_API_KEY=...
# optional
TAVILY_API_KEY=...
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

Create an account on the login screen, upload files on **Documents**, then chat.

Smoke test (backend must be running): `python test_rag_pipeline.py [path/to/file.pdf]`

> The embedded Qdrant store allows only one process at a time. `--reload` is fine, but don't run
> several workers against the same `backend/vector_db/qdrant_data`. Set `QDRANT_URL` to use a Qdrant server instead.

## Benchmark

Scripts in `benchmark/` measure retrieval, passage selection, end-to-end answers and performance
(see each script's docstring). Run them with the backend **stopped**.

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
| GET | `/api/stats` | dashboard metrics |

Tuning knobs (chunk size, `RETRIEVAL_K`, `MIN_RELEVANCE_SCORE`, `MAX_RETRIEVAL_ATTEMPTS`, models, …) live in
`backend/config/settings.py`. Each one can be overridden from `.env`.
