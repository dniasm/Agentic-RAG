# Merge stage 3: one database and one collection. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The library's tables live in day7's `app.db`, its chunks in `agentic_rag_docs_v2`, its files in `backend/data/library/`. day7's documents become library documents, and the real data is migrated with checks.

**Architecture:** `catalog` opens `settings.database_path`. The catalog owns `documents`, and day7's document functions and its second ingest worker go. day7's `/api/documents` routes run on the library (`add_file`, `process`, `delete`) through one queue. A one-time script builds the merged database, store and files in a staging folder, checks them, and only then swaps them in, keeping the originals beside them.

**Spec:** `docs/superpowers/specs/2026-11-14-merge-renamer-design.md` (stage 3)

## Global Constraints

- Every user, document and chunk keeps its id. No re-embedding. No model calls.
- day7's 38 documents: title = filename without extension, the library's collision rule (`library.stored_name`). The 25 without a stored file are copied from the repo root / `benchmark/corpus/wikipedia/` by SHA-256. A row with no file stops the run.
- Old renamer chats become conversations: session id = conversation id, original timestamps, title from the first user message, assistant messages with `tool = "manage_documents"` and `meta = {"actions": [...]}`.
- `RENAMER_SIGNUP_CODE` is copied to `SIGNUP_CODE` in day7's `.env`. The value is never printed.
- Dry run by default; `--apply` swaps. Recorded in `migrations` (`library_merge_v1`) so it runs once.
- Never stage `frontend/vite.config.js`.

## My calls (deviations from the spec, flagged to the reviewer)

- **The `owner` column keeps its name, with no foreign key.** It is used ~130 times across documents, batches and sessions. The tests use the legacy owner `library`, which has no user row. A rename buys nothing until stage 7.
- **The `sessions` table keeps its name (and `display`) until stage 5**, which replaces the library chat. No table in day7 is called `sessions`.
- The catalog refuses to open an `app.db` whose `documents` table has day7's old shape, and names the script.

## Review Focus

- A user from one side logs in through the other side's login: a bcrypt user through `/api/library/auth/login`, a PBKDF2 user through `/api/auth/login`.
- An upload through day7's `/api/documents/upload` is titled, stored under `library/<user_id>/` and indexed by the one worker; a re-upload is a duplicate.
- The script is run twice: the second `--apply` does nothing.
- One user's search returns only their own documents after the merge (a check in the script).

---

### Task 1: One database in code

**Files:** `backend/library/config.py`, `backend/library/catalog.py`, `backend/library/library.py`, `backend/library/api.py`, `backend/library/auth.py`, `backend/models/database.py`, `backend/api/security.py`, `backend/api/routes/documents.py`, `backend/api/routes/chat.py`, `backend/models/schemas.py`, `backend/app.py`, tests.

- [ ] `config.CATALOG_PATH = Path(settings.database_path)`. Both connections use `timeout=30`, `journal_mode=WAL` and `synchronous=NORMAL`.
- [ ] day7's `SCHEMA` loses `documents`. `add_document`, `update_document`, `fail_unfinished_documents`, `find_document_by_hash`, `list_documents`, `get_document` and `delete_document` are deleted from `database.py`. `init_db()` also opens the catalog (`catalog.init()`).
- [ ] `library.queue(doc_id: str) -> None` submits `process` to the one worker. `api.py` and day7's route use it. `library.stop_queue()` runs at shutdown.
- [ ] `routes/documents.py`: list → `catalog.list_documents(owner=user["id"])`; upload → `library.add_file(name, data, owner=user["id"])`, then `library.queue` if new; delete → owner check, then `library.delete`. `DocumentOut` gains `title`; `filename` = `original_name`.
- [ ] `chat._prepare` doc names = titles of the user's indexed documents.
- [ ] `security.verify_password` and `auth.verify_password` both accept `$2…` (bcrypt) and `pbkdf2_sha256$…`.
- [ ] Tests: `test_documents.py` is rewritten for the library-backed route (upload → titled + indexed, re-upload duplicate, delete removes chunks and file, another user's doc 404). New `backend/tests/library/test_shared_login.py` covers both directions of login.
- [ ] `pytest -q` passes. `unittest discover` passes.

### Task 2: The migration script

**Files:** `backend/scripts/merge_library.py`, `backend/tests/library/test_merge_library.py`.

- [ ] `merge(day7_root: Path, renamer_data: Path, staging: Path) -> Report` builds `staging/app.db`, `staging/qdrant_data`, `staging/library`, `staging/versions` and `staging/font-index` from copies. `Report.checks: list[tuple[str, bool, str]]`.
- [ ] Checks: user count = day7 + renamer; document count = sum; file on disk + SHA-256 per row; chunks per document = `chunks` column (indexed rows); one search per user (by a word of their first document's title) returns only their documents; total point count printed.
- [ ] `main()`: dry run prints the report; `--apply` refuses if any check failed or `migrations` has `library_merge_v1`, moves `backend/data/app.db` to `app.db.before-merge` and `backend/vector_db/qdrant_data` to `qdrant_data.before-merge`, moves staging in, and appends `SIGNUP_CODE`.
- [ ] Test: two tiny source sets (one day7 user + doc with stored file, one day7 doc copied from a corpus path, one renamer user + doc + session) give all checks True. A second `merge` against the merged output refuses.

### Task 3: Real run

- [ ] Dry run against the real folders; all checks pass.
- [ ] After the reviewer approves: `--apply`, then the checks again, then the full test suite.
