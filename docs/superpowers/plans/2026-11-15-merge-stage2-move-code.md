# Merge stage 2: move the code in. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The renamer lives in day7 as `backend.library`, with its history, imports `backend.*` directly, and all 294 tests pass in one `pytest` run.

**Architecture:** An unrelated-history merge of RenamingPipeline `main`, read into a temporary prefix and then `git mv`'d into place, so `git log --follow` reaches the renamer's commits. The bridge `renamer/day7.py` is deleted. The renamer's FastAPI app becomes an `APIRouter` mounted at `/api/library`. Its startup work joins day7's lifespan.

**Tech Stack:** git, FastAPI, pydantic-settings, pytest 9.2.0.

**Spec:** `docs/superpowers/specs/2026-11-14-merge-renamer-design.md` (stage 2)

## Global Constraints

- Never stage `frontend/vite.config.js`. Stage by path only.
- No commit without the user's word. The merge stays in progress (MERGE_HEAD) until the user says to commit.
- Not run on real data in this stage; verified by tests only.
- Nothing imports `renamer` at the end (`grep -rn "renamer\b" backend --include=*.py` shows only logger and cookie names, which stages 4 and 7 rename).

## Layout (my calls)

| From (RenamingPipeline) | To (day7) |
|---|---|
| `renamer/*.py` (not `day7.py`, `__main__.py`) | `backend/library/*.py` |
| `renamer/fonts/` | `backend/library/fonts/` |
| `tests/*.py` | `backend/tests/library/` (its fixtures stay in its own `conftest.py`) |
| `web/` | `frontend/renamer-web/` (source for stage 6, deleted in stage 7) |
| `scripts/fetch_test_corpus.py`, `test_corpus/` | `scripts/`, `test_corpus/` |
| `README.md` | `docs/LIBRARY.md` |
| `requirements.txt`, `.env.example`, `.gitignore`, `__main__.py` | dropped (`.gitignore` lines merged into day7's) |

`config.py` stays a module, so the ~150 `config.X` uses and the tests that patch them don't change. Its env-driven values come from new `Settings` fields; the pure constants stay as module constants (my call: they are not deployment settings).

## Review Focus

- A day7 test that relied on day7's real store or database now gets the root conftest's temporary folders: day7 tests still give 129 under pytest and under unittest (unittest doesn't load conftest).
- A string target in a mock or a logger name still says `renamer`: the renamer's 165 tests pass, including the logs test.
- Route clash: `/api/documents` must still be day7's. A test checks that `/api/library/documents` reaches the renamer's list.

---

### Task 1: Bring the code in with history

- [ ] `git fetch ../RenamingPipeline main:renamer-import`, then `git merge --allow-unrelated-histories -s ours --no-commit renamer-import`, then `git read-tree --prefix=_renamer/ -u renamer-import`.
- [ ] `git mv` each row of the layout table; `git rm` the dropped files; `_renamer/` ends empty.
- [ ] `git log --follow backend/library/pdf_edit.py` after the merge commit (checked once committed) lists `016e088`.

### Task 2: Settings, imports, bridge gone

**Files:** `backend/config/settings.py`, `backend/config/llm.py`, `backend/library/config.py`, every `backend/library/*.py`.

- [ ] `Settings` gains: `library_data_dir: str = str(BACKEND_DIR / "data")`, `signup_code: str = ""`, `library_agent_model: str = "openai/gpt-oss-120b"`, `library_backup_model: str = "openai/gpt-oss-20b"`, `library_agent_reasoning: str = "low"`, `library_tpm_limit: int = 8000`, `library_backup_tpm_limit: int = 8000`, `library_voyage_calls_per_minute: int = 3`.
- [ ] `config.py`: `DATA_DIR = Path(settings.library_data_dir).resolve()`; `LIBRARY_DIR`, `CATALOG_PATH` from it; `PROJECT_ROOT` = day7's root; drop `DAY7_DIR`, `QDRANT_PATH` and `COLLECTION_NAME` (day7's vectorstore settings apply); the env values read from `settings`.
- [ ] `llm.py`: public `api_key(index: int) -> str`, and `_key` stays as an alias for day7's own callers. The agent's two factories call `llm.api_key`.
- [ ] Imports: `from renamer import X` becomes `from backend.library import X`; `from renamer.day7 import ingestion|rerank|retrieval|sparse|vectorstore` becomes `from backend.rag import ...`; `llm` comes from `backend.config`; `settings` from `backend.config.settings`. Logger names `renamer` become `backend.library`.
- [ ] `atexit.register(vectorstore.close_client)` moves to `backend/library/__init__.py`? No: day7's lifespan already closes it. Tests: root conftest registers it.

### Task 3: Router, startup, tests

**Files:** `backend/library/api.py`, `backend/app.py`, `conftest.py` (root), `backend/tests/library/conftest.py`, `backend/tests/library/test_edge_cases.py`.

- [ ] `api.py`: `app = FastAPI(...)` becomes `router = APIRouter(prefix="/library")`, paths lose `/api`, and the static mount and `always_fresh_page` middleware go. `_resume_unfinished()` becomes `startup()`, which calls `logs.setup()`, `catalog` init, `_resume_unfinished()` and `batch.start()`. `shutdown()` calls `batch.stop()`.
- [ ] `backend/app.py`: include `library_api.router` at `/api`; the lifespan calls `library_api.startup()` after `init_db()` and `library_api.shutdown()` before `close_client()`.
- [ ] Root `conftest.py`: before any import, it sets `QDRANT_URL=""`, `QDRANT_PATH`, `DATABASE_PATH`, `UPLOAD_DIR` and `LIBRARY_DATA_DIR` to one temp folder, and registers `atexit(vectorstore.close_client)`.
- [ ] The renamer's conftest loses its env lines and its `renamer.day7` import.
- [ ] New test `backend/tests/library/test_mount.py::test_library_routes_mounted_beside_day7`: the app's routes include `/api/library/documents` and `/api/library/chat`, and `/api/documents` is still served by `backend.api.routes.documents`.
- [ ] Run: `.venv\Scripts\python -m pytest -q` gives 295 passed (294 + 1). `python -m unittest discover backend/tests -p "test_*.py"` still gives 129 OK (the library tests are pytest-only; run it with `-s backend/tests -t .` against the top-level test files only if discovery recurses).
- [ ] `grep -rn "from renamer\|import renamer" backend scripts` is empty.
