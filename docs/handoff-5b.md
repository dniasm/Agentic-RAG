# Phase 5b handoff: library catalog moved to the Document service (temporary, part B deletes this file)

Part A (service side) is done on branch `phase-5b-library-catalog`. Part B rewires `backend/library/catalog.py`'s
callers. Nothing under `backend/library/` was changed.

## Design
- The calls are added to the existing `agentic_rag.v1.DocumentService` (same process, port, stub and servicer as the RAG
  half; the servicer mixes in `services/document/catalog_server.py`; the SQL is `services/document/catalog.py`, the
  tables are in `services/document/db.py`). A second service would only have meant a second stub, registration and client.
- Tables live in `document.db` with the same columns and indexes. The old `ALTER TABLE` upgrades are not carried over
  (a new database has the final schema; `migrate_library_catalog.py` reads app.db after the orchestrator has upgraded it).
- One RPC per atomic operation. The orchestrator-side pieces that are NOT in the service and stay in part B:
  `_version` / `version()` / `_changed()`, `indexed_titles()` cache, `hub.publish(...)` pushes, `init()`,
  `reset_connection()`, `tx()`, `now()`, the "database still has day7's old documents table" check.
- Python helpers for pb <-> dict are not provided on the client side; the server-side ones (`document_pb`, `task_pb`,
  `batch_pb`, ...) in `catalog_server.py` show every rule.
- Errors: `INVALID_ARGUMENT` (bad column name / non-object fields_json / empty required id), `NOT_FOUND` (UpdateTask
  with an unknown task id).

## catalog.py function -> RPC (all take/return the pb messages in document.proto)
| catalog.py | RPC | notes |
|---|---|---|
| add_document(title=, original_name=, file_name=, ext=, sha256=, size_bytes=, owner=) | AddCatalogDocument | owner "" -> "library"; returns the document |
| get_document(id) | GetCatalogDocument | `OptionalCatalogDocument`; check `HasField("document")` |
| find_by_hash(sha, owner) | FindCatalogDocumentByHash | owner-scoped; owner "" -> "library" |
| list_documents(status, owner) | ListCatalogDocuments | `optional` status/owner (unset = no filter, as None) |
| update_document(id, **fields) | UpdateCatalogDocument | `fields_json` object; `null` -> SQL NULL (e.g. `error=None`); `keywords` as a list; optional `rename` does update_document + record_rename in one transaction |
| delete_document | DeleteCatalogDocument | also deletes renames and edits (not undone_edits, as before) |
| record_rename | RecordRename | (UpdateCatalogDocument.rename is the atomic pair) |
| rename_histories() | RenameHistories | repeated `DocumentRenames{doc_id, renames}` -> rebuild `{doc_id: [..]}` |
| rename_history(id) | RenameHistory | |
| used_file_names(exclude_doc) | UsedFileNames | already lower-cased and "/"-normalised; a list -> `set()` |
| record_edit(doc_id, op, detail, backup) | RecordEdit | now returns `edit_id`; optional `document_fields_json` updates the document in the same transaction (the editing.py `update_document` + `record_edit` pair at ~L841/850) |
| last_edit | LastEdit | `OptionalEdit` (keys id, operation, detail, backup, at; the pb also carries doc_id, which the old dict lacked) |
| edit_exists | EditExists | |
| edit_history | EditHistory | operation, detail, at |
| delete_edit | DeleteEdit | |
| move_edit_to_undone(edit, redo, ext) | UndoEdit | `edit` needs id, doc_id, operation, detail, backup, at; optional `document_fields_json` does the `update_document(ext=, file_name=)` of editing.py ~L583/585 atomically |
| last_undone | LastUndone | `OptionalUndoneEdit`: id, doc_id, operation, detail, backup, redo, ext, at, undone_at, undo_order |
| restore_undone(id) | RedoEdit | optional `document_fields_json` (editing.py ~L632/634); an unknown id is a no-op, as before |
| clear_undone | ClearUndone | `dropped` = the full rows (to delete their files) |
| load_session | LoadLibrarySession | unknown id -> empty session, `owner` unset -> None |
| save_session | SaveLibrarySession | upsert; an existing session keeps its owner; owner unset -> "library" |
| add_batch(session_id, items, owner) | AddBatch | items: `BatchItem{instruction, payload_json?}` (old tuple `(text, change)`); returns the full batch |
| get_batch | GetBatch | `OptionalBatch` |
| active_or_recent_batches(hours, owner) | ActiveOrRecentBatches | `hours` double; owner optional |
| list_batches(limit, owner) | ListBatches | |
| tasks_run_for | TasksRunFor | `{actions (decoded), finished_at}` |
| pending_tasks_for | PendingTasksFor | `.count` |
| next_queued_task | NextQueuedTask | peek only |
| (new) next_queued_task + update_task(status=running, started_at=now) | ClaimNextTask | atomic claim; use it in `batch.py` instead of the two calls (~L167) |
| get_task | GetTask | |
| update_task(id, **fields) | UpdateTask | `fields_json` with only status, reply, actions (list), tokens, started_at, finished_at, checkpoint (object or null); reply has `batch_id` and `owner` for `_push_task` (so no extra read) |
| cancel_batch | CancelBatch | `cancelled` count; `owner` unset when the batch is unknown (the old code published a push only if the batch existed) |
| count_queued_tasks(owner) | CountQueuedTasks | |
| tasks_with_status | TasksWithStatus | |
| requeue_running_tasks | RequeueRunningTasks | returns the count (old: None) |
| give_legacy_chats_and_batches_to | GiveLegacyChatsAndBatchesTo | empty user id -> INVALID_ARGUMENT |
| (migration) | ImportCatalog, CountCatalog | used only by `scripts/migrate_library_catalog.py` |

35 catalog functions -> 35 RPCs, plus ClaimNextTask, ImportCatalog, CountCatalog = 38 new RPCs.
Not mapped (stay in the orchestrator): version, now, init, reset_connection, tx, indexed_titles, _changed, _push_task.

## Dict shapes each orchestrator function must keep
- Document dict: id, title, original_name, file_name, ext, sha256, original_sha256 (None), size_bytes, pages (None),
  chunks (None), summary, keywords (list, decode `keywords_json`), owner, status, error (None), created_at, updated_at.
  Unset `optional` fields must become `None`, not 0/"".
- Task dict: id, batch_id, position, instruction, payload (decoded object or None), checkpoint (decoded or None), status,
  reply, actions (list), tokens, started_at (None), finished_at (None). Batch dict: id, session_id, owner, created_at, tasks.
- Session dict: id, turn, llm_messages, display, guard (decoded), owner (None when the session is new).
- `last_edit` -> {id, operation, detail, backup, at} or None; `edit_history` -> [{operation, detail, at}];
  `rename_history` -> [{old_title, new_title, reason, at}].

## Gotchas
- Wherever the orchestrator did `update_document(...)` followed by `record_rename` / `record_edit` /
  `move_edit_to_undone` / `restore_undone`, fold them into one call with the optional fields (they are separate commits
  in the old code, so a crash between them left a half state; now they cannot).
- `_version` / `indexed_titles`: bump the orchestrator's version after every mutating call it makes (add/update/delete
  document, record_rename); the service does not know about that cache. Rows only change through the orchestrator, so
  this is enough. A service restart does not matter (version is orchestrator-side).
- `hub.publish(owner, "queue", ...)`: still orchestrator-side. `add_batch` published "changed" after the insert;
  `cancel_batch` after the cancel (use the returned owner); `update_task` -> "task" push uses UpdateTask's reply.
- The Document service must be running before the orchestrator starts using the library (startup `init()` can no longer
  fail on a bad database; failures are now gRPC `UNAVAILABLE`). `requeue_running_tasks()` at startup needs the service.
- document.db now holds real data (before, only caches): `scripts/backup*.py` already copies each service's database,
  but the "losing the file costs requests, never data" line in docs about the Document service no longer holds for it.
- Files on disk (library folder, `versions/<doc_id>/`) are still in the orchestrator's LIBRARY_DATA_DIR; only their
  catalog rows moved. `file_name`, `backup`, `redo` stay paths relative to that folder.
- The `migrations` bookkeeping table in app.db is not copied (nothing in the catalog reads it); the ALTER-style upgrades
  in the old `_db()` are dropped (see Design).
- The services test session shares one document.db; tests that touch the catalog must start from empty tables (see the
  `empty_catalog` fixture in `backend/tests/services/test_document_catalog.py`) or use unique owners/ids.
- Migration: `python scripts/migrate_library_catalog.py` (stop the backend first so app.db is quiet; the Document service
  must be running). It backs up app.db under D:\onboarding_tasks\backups, is idempotent, verifies counts, leaves app.db untouched.
