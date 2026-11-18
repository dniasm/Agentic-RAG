# Merging RenamingPipeline into day7: design

Date: 2026-11-14. Status: draft for approval. No code has moved.

Written from the merge thread's 7-stage outline, checked against both codebases and both databases
(read-only) and filled in where the outline was silent or wrong.

## 1. Goal

One app, one login, one chat window. The chat answers questions (day7's graph) and manages documents
(the renamer's agent). One database, one vector collection, one front end, one test suite.

## 2. Decisions already made

| Decision | Choice |
|---|---|
| Home repo | day7 (`dniasm/Agentic-RAG`), on a branch; the renamer's git history is kept |
| Front end | day7's React + Vite; shared logic as plain ES modules in `src/lib/` |
| Chat routing | One way per message: a message is a question or a document action, never both |
| Method | Staged; every stage ends with all tests passing |

Out of scope: one message doing both (chaining the graph and the agent), the staged retrieval rebuild,
a password-change route (neither project has one).

## 3. Measured facts (2026-11-14)

| | day7 | Renamer |
|---|---|---|
| Users | 16 | 4 |
| Usernames in both | 0 | 0 |
| Documents | 38 (37 indexed, 1 failed) | 41 (all indexed) |
| Chats | 75 conversations | 32 sessions |
| Rename / edit history | none | 68 renames, 5 edits, 2 batches |
| Vector store on disk | 27.7 MB | 6.8 MB |
| Tests | 129 pass (unittest) | 165 pass (pytest) |
| Python | 3.14.5 | 3.14.5 |
| SQLite journal | delete | delete |

What follows from them:

- **No user merging.** No username is in both, so every user keeps their id. No owner or chunk needs
  remapping. If one person has an account in each, they keep two accounts unless the user asks for a
  manual merge.
- **Chunk ids are safe to copy.** They are `uuid5(doc_id:index)`, independent of the user.
- **Document status values already match** (`processing`, `indexed`, `failed`).
- **25 of day7's 38 documents have no stored file.** They were ingested from the repo root (the PDFs and
  `claude_wiki.docx`) and `benchmark/corpus/wikipedia/`, not uploaded. All 25 match a file there by SHA-256,
  so the migration copies them from those paths. Only 13 come from `backend/documents/`.
- **day7's Qdrant folder holds three collections:** `agentic_rag_docs_v2` (in use), `agentic_rag_docs` (the old
  dense-only one) and `user_memories`. Only the first is touched.
- **day7's tests do run today**, with `python -m unittest discover backend/tests -p "test_*.py"`. Only
  pytest is missing from its environment.
- **Three files exist in both libraries** (`sample.pdf`, the Comptine score, the Pre-Visit package), each
  under different users, so none is a duplicate.

## 4. Decisions (answered 2026-11-15)

1. **Licence: AGPL accepted.** Add an AGPL-3.0 `LICENSE` and a source link in the app. Import PyMuPDF only
   inside `pdf_edit.py`, so the rest of the app runs without it.
2. **`frontend/vite.config.js`:** never commit the host change, and no skip-worktree. Stage files by path,
   never `git add -A` or `commit -a`.
3. **Old renamer chats: converted** into conversations. Session id = conversation id, original timestamps,
   title from the first user message.
4. **Titles for day7's 38 documents:** filename without extension, the renamer's collision rule per user.
   Generated titles only if the user asks later.

## 5. Target layout

```
backend/
  library/          the renamer package (agent, catalog, editing, pdf_edit, batch, export, naming, titles)
  library/fonts/    the bundled fonts
  api/routes/       auth, chat, documents (unified), library (queue, limits, preview, export, undo)
  data/             app.db, library/<user_id>/, versions/, font-index/, logs/
  tests/            both suites
frontend/src/lib/   plain ES modules shared with the renamer (api, pdf-viewer, poller, markdown, ...)
```

day7's database and vector store stay where they are. Only the renamer's data moves.

## 6. Stages

Each stage is one or more commits on the branch and ends with its "Done when" check.

### Stage 0: safety

- Stop both servers. The embedded vector store allows one process per folder.
- Copy `backend/data/`, `backend/vector_db/`, `backend/documents/` and the renamer's `data/` to a dated
  backup folder outside both repos.
- Settle the licence question (section 4.1).

Done when: the backup opens (both databases pass `PRAGMA integrity_check`).

### Stage 1: one environment

- Add `pymupdf`, `fpdf2` and `pyphen` to day7's requirements, pinned to the versions the renamer's
  environment has now. `groq`, `pypdf` and `python-docx` are already pinned in day7; day7's pins win on any
  package both use. The renamer lists `langchain-openai`; nothing in `backend/` imports it, only
  `scripts/experiments/nemotronsmoketest.py`, so it moves to `requirements-dev.txt`.
- `requirements-dev.txt` for pytest and `langchain-openai` (not in the main requirements).
- Run day7's 129 tests under pytest (unittest classes run unchanged).

Done when: 129 pass under pytest in day7's environment, and the renamer's 165 still pass in its own.

### Stage 2: move the code in

- Subtree-merge the renamer as `backend/library/`, with tests, fonts and test corpus.
- Delete `renamer/day7.py`; import `backend.*` directly.
- `config.py` values become `Settings` fields prefixed `library_`. `.env` keys are renamed
  (`RENAMER_SIGNUP_CODE` becomes `SIGNUP_CODE`, and so on); list the keys to move, never print values.
- **Route clashes.** Both apps define `/api/documents`, `/api/documents/upload`, `/api/chat`,
  `/api/auth/login` and `/api/health`. The renamer's app becomes a router mounted at `/api/library/*`
  until stages 4 to 6 unify each one.
- **Import-time side effect.** `api.py` re-queues unfinished files when imported (`_resume_unfinished()`).
  Move that into day7's startup next to `batch.start()`, which is already in the renamer's lifespan and moves
  there too.
- **Test isolation.** The renamer's `conftest.py` sets temporary folders before importing day7. With one
  shared `settings` object this moves to a root `conftest.py` that runs before any `backend` import.
- **Model calls.** The agent already rotates keys through the public `call_with_keys` (`agent._on_keys`); only
  its client factories read the private `llm._key`. Add a public key accessor to `backend/config/llm.py` (or
  let `call_with_keys` build the client). Done in stage 2: `llm.api_key(index)`. Recording the agent's tokens in
  `UsageTracker` under a `manage` step moves to stage 5, where the agent joins day7's chat request.

The merged app is not run on real data between stages 2 and 3: without the bridge the renamer code reads
day7's store and would show an empty library. Stage 2 is verified by tests only.

Done when: all 294 tests pass in one `pytest` run, and nothing imports `renamer`.

### Stage 3: one database and one collection

Schema, in `app.db`:

- The renamer's `documents` table replaces day7's. `owner` is renamed `user_id` with the foreign key.
- Add `renames`, `edits`, `batches`, `batch_tasks`, `auth_tokens`, and `agent_sessions` (the renamer's
  `sessions` without `display`).
- The two `users` tables have identical columns; rows are appended.

Migration script, dry-run by default, recorded in the `migrations` table so it runs once:

1. **day7 documents.** For each of the 38: title = filename without extension, `original_name` =
   filename, `sha256` and `original_sha256` = the stored hash, empty summary and keywords. Copy the file
   to `data/library/<user_id>/<file name>` with the renamer's collision rule. The source is
   `backend/documents/<user_id>/<doc_id><ext>` for 13; for the other 25 it is the repo-root or
   `benchmark/corpus/wikipedia/` file with the same SHA-256 (copied, never moved: they are the repo's own inputs; the .md and .docx are tracked,
   the PDFs are git-ignored by `*.pdf`). The one `failed` row is copied as failed
   (re-upload retries it). A row with no matching file stops the run.
2. **day7 chunks.** Add `title` and `file_name` with a payload update. No re-embedding.
3. **Renamer rows.** Copy documents, renames, edits, batches and tasks as they are. Copy files and
   `versions/`.
4. **Renamer chunks.** Open the renamer's store with a second client, scroll every point with its dense
   and sparse vectors, upsert into `agentic_rag_docs_v2` in batches of 128. No re-embedding.
5. **Renamer sessions.** `llm_messages` and `guard` go to `agent_sessions`. If section 4.3 is approved,
   `display` becomes a conversation with messages marked `tool = "manage_documents"`.
6. **Login.** Both login paths accept both hash formats by prefix (see stage 4), so every user can log in
   as soon as the tables are joined.

Checks the script prints, in dry run and after the real run:

- users 20, documents 79, with a file on disk for every row and a matching SHA-256;
- for every document, chunks in the collection equal the `chunks` column;
- one search per user returns only that user's documents;
- the total point count (this is the number the Qdrant note in section 7 depends on).

Also in this stage:

- **Unfinished uploads.** day7 marks them failed at startup, the renamer resumes them. Keep resume.
- **One ingestion queue** with one worker (Gemini allows 100 embedding requests a minute).
- **WAL mode** on `app.db` (see section 7).

Done when: the checks pass on the real data, all tests pass, and both old data folders are untouched
(the script builds new files and swaps).

### Stage 4: one login

Keep the renamer's login. It is better where it matters:

| | day7 | Renamer |
|---|---|---|
| Where the login lives | Token in `localStorage`, readable by any script | HttpOnly cookie |
| Logging out | Token stays valid up to 24 hours | Token deleted on the server |
| A copied database | Signing secret sits beside it | Only token hashes |
| Sign-up | Open | Invite code after the first account |
| Wrong passwords | No limit | 5 failures in 15 minutes locks the username |

The password hashes are equal in strength (bcrypt, and PBKDF2 at 600,000 rounds). So:

- **No re-hashing.** Verify by prefix, leave existing hashes alone, hash new passwords with PBKDF2.

Steps:

1. `get_current_user` reads the cookie on every route, the chat stream included. Delete the JWT code and
   the `.jwt_secret` file.
2. Front end: stop storing a token. The Vite proxy already makes API calls same-origin, so the cookie
   travels without changes. A 401 opens the login page.
3. Invite code and throttle apply to every sign-up and login. Rename the cookie from `renamer_login`.
4. Add a per-address login limit beside the per-username lock, so knowing a username isn't enough to
   keep its owner locked out.
5. **Secure flag.** It is set only when the request looks like https. Behind the tunnel the server may
   see plain http. Check once with the tunnel running; if the flag is missing, run uvicorn with
   `--proxy-headers` or set it from a setting.

One-time effect: the 16 day7 users log in again with the same password.

Done when: tests cover a bcrypt user logging in, logout killing the token, an expired token refused, and
one user's cookie unable to read another's chats, documents, queue or previews.

### Stage 5: one chat

- **Route.** `Route.message_type` gains `document_action`, with its own section in `ROUTER_PROMPT`. The
  graph gains a `manage` node: router to `manage` to END. It skips the answer checker.
- **Pending questions go to the agent first.** The agent asks "rename these?" or "which one do you
  mean?" and waits; the reply is "yes" or "the second one", which the router would call small talk. When
  the conversation's guard has `pending` renames or a `needs_user` entry from the last turn, code sends
  the message to `manage` before the router runs. `_prepare` loads that flag into the state.
- **State.** The agent's tool-call history and guard live in `agent_sessions`, keyed by conversation id.
  Queued tasks have no conversation and keep their own session ids. What the user sees is day7's
  `messages` table only.
- **"Rename that one" after a question.** The document ids cited in the previous answer are passed to
  the agent as already identified, so it does not search again.
- **Streaming.** The agent returns one blocking reply. Emit a step event per tool call through the
  existing stream, and put `actions` in the final payload and in the message's `meta`, so Preview and
  Undo buttons survive a reload.
- **One message at a time per conversation.** The renamer has this lock; day7 gets it for every route.
- **Memory.** `manage` messages keep the free `_may_contain_memory` check and skip the episode summary.
- **Code routing, kept narrow.** Only pending questions, a bare "undo", and queue status skip the
  router. "Rename" alone does not: "how do I rename a file in Linux?" is a question.

Done when: the 80 cases in `benchmark/router_cases.json` score no worse than the last run
(`router_eval_prompt_final.json`: 73 correct, 79 acceptable), at least 30 new cases pass (actions, questions that mention
documents, and borderline ones such as "summarise X" against "fix the typo in X"), with the borderline
ones run 5 times each; the renamer's agent tests pass through the new route; the backend is stopped
while benchmarks run.

### Stage 6: one front end

- `Documents.jsx` becomes the Library page: upload, rename, undo, export, preview, queue.
- The renamer's `web/js/lib/` modules move to `src/lib/` unchanged; React components wrap them thinly.
- Chat messages render Preview and Undo from `meta.actions`.
- Production: FastAPI serves `frontend/dist`. `index.html` is sent `no-cache`; hashed assets cache long.

Done when: `npm run build` passes and each Library action is exercised in a real browser, not only
through the API.

### Stage 7: clean up

- Remove `/api/library/chat` and the renamer's auth routes, the old `web/` folder, and unused settings.
- Update the README and `docs/DOCUMENTATION.md`.

Done when: all tests pass and no route, setting or file refers to the old layout.

## 7. Shared limits and performance

**One of each limiter.** Both projects pace the same free allowances separately today.

- **Groq.** The renamer's `TokenWindow` paces batches from its own calls only. In one process it must
  count day7's chat tokens too: record every call from `call_with_keys` into the window. Otherwise a
  batch starves the chat.
- **Voyage (3 calls a minute).** The agent's find already goes through day7's `RateLimiter`
  (`rerank.score` calls `voyage.call(RERANK, ...)`); the renamer's own `_VOYAGE` counter in `library.py` is
  only a pre-check that skips to cosine scores instead of waiting. Drop that counter, add a `FIND` priority to
  `voyage.py` between `RERANK` and `RECALL` with a zero wait (busy means cosine at once), so the order is
  answer reranking, the agent's find, memory recall, episode saves. FIND respects the rerank reserve like
  RECALL does. The limiter's waiting list is indexed by priority number, so it grows with the new level.

**Optimizations, in order of value:**

1. **WAL mode** (`journal_mode=WAL`, `synchronous=NORMAL`). The batch worker writes while chats read;
   with rollback journals they block each other.
2. **Vector search scans every user's chunks.** Embedded Qdrant ignores payload indexes and does a full
   scan. It is fine at today's size (about 35 MB on disk). qdrant-client warns at 20,000 points in local
   mode; treat that as the point to move to a Qdrant server (`QDRANT_URL` already supports it).
3. **Cache the document list per user** on the catalog's version counter. `_prepare` reads it on every
   message; the renamer's find cache already invalidates on the same counter.
4. **Limits polling.** The renamer's page polls every second. Poll only while the Library or queue is
   visible, and stop when the tab is hidden.
5. **Lazy-load pdf.js and KaTeX** so the chat page does not download them until needed.
6. **Cap the agent's history** as day7 caps the previous answer (the agent already cuts older messages
   to 800 characters; apply day7's cap to the latest one).

## 8. Risks

| Risk | Guard |
|---|---|
| Data loss in migration | Stage 0 backup; dry run; new files then swap; per-document checks |
| Router regressions from the new route | 80 existing (baseline 73/79) + 30 new cases, borderline ones 5 times |
| A wrong document renamed | The guard's identification and confirmation rules are unchanged; undo exists |
| Groq quota during testing | Benchmarks with the backend stopped, spread over days |
| Two test suites sharing one settings object | Root `conftest.py` sets folders before any import |
