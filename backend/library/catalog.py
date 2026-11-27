"""The library's catalog: documents, their rename and edit history, chat sessions and queued batches.

The Document service owns it (services/document/catalog.py, its own document.db); these functions call it over gRPC
(backend/clients.py) and return the dicts the old SQL did, so callers are unchanged. What stays here is what only
the orchestrator can know: the catalog's version (and the cache of indexed titles built on it) and the live pushes
to open tabs.
"""
import json
import logging
import threading
from datetime import datetime, timezone

import grpc

from agentic_rag_proto import document_pb2 as pb
from backend.clients import get_clients
from backend.library import config
from backend.realtime.hub import hub

log = logging.getLogger(__name__)

_lock = threading.RLock()
_version = 0   # goes up with every change to documents or their names: cached search results are stale after it


def version() -> int:
    return _version


def _changed() -> None:
    """Called after the service committed a change, never before: a reader that sees the new version then also sees
    the new rows, and one that read the rows earlier is left with an old version it will refetch."""
    global _version
    with _lock:
        _version += 1


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _call(rpc_name: str, request):
    clients = get_clients()
    return clients.call(getattr(clients.document, rpc_name), request)


def _opt(message, name: str):
    return getattr(message, name) if message.HasField(name) else None


def _json(value: dict | None) -> str | None:
    return json.dumps(value, ensure_ascii=False) if value is not None else None


def _doc(m: pb.CatalogDocument) -> dict:
    return {"id": m.id, "title": m.title, "original_name": m.original_name, "file_name": m.file_name, "ext": m.ext,
            "sha256": m.sha256, "original_sha256": _opt(m, "original_sha256"), "size_bytes": m.size_bytes,
            "pages": _opt(m, "pages"), "chunks": _opt(m, "chunks"), "summary": m.summary,
            "keywords": json.loads(m.keywords_json or "[]"), "owner": m.owner, "status": m.status,
            "error": _opt(m, "error"), "created_at": m.created_at, "updated_at": m.updated_at}


def _optional_doc(reply: pb.OptionalCatalogDocument) -> dict | None:
    return _doc(reply.document) if reply.HasField("document") else None


# ---------------------------------------------------------------- documents
def add_document(*, title: str, original_name: str, file_name: str, ext: str, sha256: str, size_bytes: int,
                 owner: str = config.OWNER, conversation_id: str | None = None) -> dict:
    request = pb.AddCatalogDocumentRequest(
        title=title, original_name=original_name, file_name=file_name, ext=ext, sha256=sha256,
        size_bytes=size_bytes, owner=owner)
    if conversation_id:
        request.conversation_id = conversation_id
    doc = _doc(_call("AddCatalogDocument", request))
    _changed()
    return doc


def update_document(doc_id: str, rename: tuple[str, str, str] | None = None, **fields) -> None:
    """Change columns (None sets NULL). `rename` = (old title, new title, reason) is recorded in the same transaction."""
    request = pb.UpdateCatalogDocumentRequest(doc_id=doc_id, fields_json=json.dumps(fields, ensure_ascii=False))
    if rename:
        request.rename.CopyFrom(pb.RecordRenameRequest(doc_id=doc_id, old_title=rename[0], new_title=rename[1],
                                                       reason=rename[2]))
    _call("UpdateCatalogDocument", request)
    _changed()


def get_document(doc_id: str) -> dict | None:
    return _optional_doc(_call("GetCatalogDocument", pb.CatalogDocumentRef(doc_id=doc_id)))


def find_by_hash(sha256: str, owner: str = config.OWNER) -> dict | None:
    """The owner's document stored with these bytes, or uploaded with them and edited since. Only the owner's own:
    another user uploading the same file gets their own copy, not a look at someone else's."""
    return _optional_doc(_call("FindCatalogDocumentByHash", pb.FindByHashRequest(sha256=sha256, owner=owner)))


def list_documents(status: str | None = None, owner: str | None = None) -> list[dict]:
    """Documents, oldest first: one user's (owner), or everyone's (owner=None, for the server's own work)."""
    request = pb.ListCatalogDocumentsRequest(status=status or None, owner=owner)
    return [_doc(d) for d in _call("ListCatalogDocuments", request).documents]


def conversation_documents(owner: str, conversation_id: str) -> list[dict]:
    """The owner's documents uploaded in this conversation, the most recent upload (a re-upload counts) first."""
    request = pb.ListConversationDocumentsRequest(owner=owner, conversation_id=conversation_id)
    return [_doc(d) for d in _call("ListConversationDocuments", request).documents]


def link_upload(doc_id: str, conversation_id: str) -> None:
    """Record that the file was uploaded (again) in this conversation."""
    _call("LinkUpload", pb.LinkUploadRequest(doc_id=doc_id, conversation_id=conversation_id))


def latest_document(owner: str, status: str | None = "indexed") -> dict | None:
    """The owner's most recently uploaded document (ready ones by default)."""
    docs = list_documents(status=status, owner=owner)
    return docs[-1] if docs else None


_titles: dict[str, tuple[int, list[str]]] = {}   # owner -> (catalog version, titles of indexed documents)


def indexed_titles(owner: str) -> list[str]:
    """Titles of the owner's indexed documents. day7's chat reads them on every message; they are read again
    only after the catalog changed (version())."""
    seen = _version   # read before the rows: a change that lands meanwhile leaves the cache one version behind
    cached = _titles.get(owner)
    if not cached or cached[0] != seen:
        cached = _titles[owner] = (seen, [d["title"] for d in list_documents(status="indexed", owner=owner)])
    return list(cached[1])


def delete_document(doc_id: str) -> None:
    _call("DeleteCatalogDocument", pb.CatalogDocumentRef(doc_id=doc_id))
    _changed()


def record_rename(doc_id: str, old_title: str, new_title: str, reason: str) -> None:
    _call("RecordRename", pb.RecordRenameRequest(doc_id=doc_id, old_title=old_title, new_title=new_title, reason=reason))
    _changed()


def _renames(records) -> list[dict]:
    return [{"old_title": r.old_title, "new_title": r.new_title, "reason": r.reason, "at": r.at} for r in records]


def rename_histories() -> dict[str, list[dict]]:
    """Every document's rename history in one call (doc_id -> oldest first), for searches and the library list."""
    return {d.doc_id: _renames(d.renames) for d in _call("RenameHistories", pb.CatalogOwnerlessRequest()).documents}


def rename_history(doc_id: str) -> list[dict]:
    return _renames(_call("RenameHistory", pb.CatalogDocumentRef(doc_id=doc_id)).renames)


def record_edit(doc_id: str, operation: str, detail: str, backup: str, document_fields: dict | None = None) -> int:
    """Returns the edit's id. `document_fields` updates the document in the same transaction."""
    request = pb.RecordEditRequest(doc_id=doc_id, operation=operation, detail=detail, backup=backup,
                                   document_fields_json=_json(document_fields))
    edit_id = _call("RecordEdit", request).edit_id
    if document_fields:
        _changed()
    return edit_id


def last_edit(doc_id: str) -> dict | None:
    reply = _call("LastEdit", pb.CatalogDocumentRef(doc_id=doc_id))
    if not reply.HasField("edit"):
        return None
    e = reply.edit
    return {"id": e.id, "operation": e.operation, "detail": e.detail, "backup": e.backup, "at": e.at}


def edit_exists(edit_id: int) -> bool:
    """False once the edit was undone (undo deletes its record)."""
    return _call("EditExists", pb.EditRef(edit_id=edit_id)).exists


def _undone(u: pb.UndoneEdit) -> dict:
    return {k: getattr(u, k) for k in ("id", "doc_id", "operation", "detail", "backup", "redo", "ext", "at",
                                       "undone_at", "undo_order")}


def move_edit_to_undone(edit: dict, redo: str, ext: str, document_fields: dict | None = None) -> None:
    """An undone edit leaves the edit history but is kept, with the edited file (`redo`), until a new edit.
    `document_fields` updates the edit's document in the same transaction."""
    record = pb.EditRecord(**{k: edit[k] for k in ("id", "doc_id", "operation", "detail", "backup", "at")})
    _call("UndoEdit", pb.UndoEditRequest(edit=record, redo=redo, ext=ext, document_fields_json=_json(document_fields)))
    if document_fields:
        _changed()


def last_undone(doc_id: str) -> dict | None:
    """The edit a redo would bring back: the one undone most recently."""
    reply = _call("LastUndone", pb.CatalogDocumentRef(doc_id=doc_id))
    return _undone(reply.undone) if reply.HasField("undone") else None


def restore_undone(undone_id: int, document_fields: dict | None = None) -> None:
    """A redo: the edit is back in the history under its own id (so its Undo button applies again)."""
    _call("RedoEdit", pb.RedoEditRequest(undone_id=undone_id, document_fields_json=_json(document_fields)))
    if document_fields:
        _changed()


def clear_undone(doc_id: str) -> list[dict]:
    """A new edit ends the redo history (as a new page does a browser's Forward). Returns what was dropped, so its
    files can be deleted."""
    return [_undone(u) for u in _call("ClearUndone", pb.CatalogDocumentRef(doc_id=doc_id)).dropped]


def delete_edit(edit_id: int) -> None:
    _call("DeleteEdit", pb.EditRef(edit_id=edit_id))


def edit_history(doc_id: str) -> list[dict]:
    return [{"operation": e.operation, "detail": e.detail, "at": e.at}
            for e in _call("EditHistory", pb.CatalogDocumentRef(doc_id=doc_id)).edits]


def used_file_names(exclude_doc: str | None = None) -> set[str]:
    """Every stored file's path relative to the library folder, lower case."""
    return set(_call("UsedFileNames", pb.UsedFileNamesRequest(exclude_doc=exclude_doc)).file_names)


# ---------------------------------------------------------------- sessions
def load_session(session_id: str) -> dict:
    s = _call("LoadLibrarySession", pb.LibrarySessionRef(session_id=session_id))
    return {"id": s.id, "turn": s.turn, "llm_messages": json.loads(s.llm_messages_json or "[]"),
            "display": json.loads(s.display_json or "[]"), "guard": json.loads(s.guard_json or "{}"),
            "owner": _opt(s, "owner")}


def save_session(session: dict) -> None:
    _call("SaveLibrarySession", pb.LibrarySession(
        id=session["id"], turn=session["turn"], llm_messages_json=json.dumps(session["llm_messages"], ensure_ascii=False),
        display_json=json.dumps(session["display"], ensure_ascii=False), guard_json=json.dumps(session["guard"]),
        owner=session.get("owner") or config.OWNER))


# ---------------------------------------------------------------- batches
def _task(t: pb.CatalogTask) -> dict:
    payload, checkpoint = _opt(t, "payload_json"), _opt(t, "checkpoint_json")
    return {"id": t.id, "batch_id": t.batch_id, "position": t.position, "instruction": t.instruction,
            "payload": json.loads(payload) if payload else None,
            "checkpoint": json.loads(checkpoint) if checkpoint else None, "status": t.status, "reply": t.reply,
            "actions": json.loads(t.actions_json or "[]"), "tokens": t.tokens,
            "started_at": _opt(t, "started_at"), "finished_at": _opt(t, "finished_at")}


def _batch(b: pb.CatalogBatch) -> dict:
    return {"id": b.id, "session_id": b.session_id, "owner": b.owner, "created_at": b.created_at,
            "tasks": [_task(t) for t in b.tasks]}


def _optional_task(reply: pb.OptionalTask) -> dict | None:
    return _task(reply.task) if reply.HasField("task") else None


def add_batch(session_id: str, items: list, owner: str = config.OWNER) -> dict:
    """items: instruction strings (run by the agent) or (instruction, change dict) pairs (run in code)."""
    batch_items = []
    for item in items:
        text, change = item if isinstance(item, tuple) else (item, None)
        batch_items.append(pb.BatchItem(instruction=text, payload_json=_json(change) if change else None))
    batch = _batch(_call("AddBatch", pb.AddBatchRequest(session_id=session_id, items=batch_items, owner=owner)))
    hub.publish(owner, "queue", "changed", {})
    return batch


def tasks_run_for(session_id: str) -> list[dict]:
    """The tasks a chat queued that have finished, with what each did ({"actions", "finished_at"})."""
    reply = _call("TasksRunFor", pb.LibrarySessionRef(session_id=session_id))
    return [{"actions": json.loads(t.actions_json or "[]"), "finished_at": t.finished_at} for t in reply.tasks]


def pending_tasks_for(session_id: str) -> int:
    """How many of the tasks a chat queued are still to run (queued or running)."""
    return _call("PendingTasksFor", pb.LibrarySessionRef(session_id=session_id)).count


def get_batch(batch_id: str) -> dict | None:
    reply = _call("GetBatch", pb.BatchRef(batch_id=batch_id))
    return _batch(reply.batch) if reply.HasField("batch") else None


def active_or_recent_batches(hours: float = 1.0, owner: str | None = None) -> list[dict]:
    """What the queue panel shows: batches with tasks still to run, and batches queued in the last `hours`."""
    return [_batch(b) for b in _call("ActiveOrRecentBatches", pb.ActiveBatchesRequest(hours=hours, owner=owner)).batches]


def list_batches(limit: int = 10, owner: str | None = None) -> list[dict]:
    return [_batch(b) for b in _call("ListBatches", pb.ListBatchesRequest(limit=limit, owner=owner)).batches]


def next_queued_task() -> dict | None:
    return _optional_task(_call("NextQueuedTask", pb.CatalogOwnerlessRequest()))


def get_task(task_id: int) -> dict | None:
    return _optional_task(_call("GetTask", pb.TaskRef(task_id=task_id)))


def update_task(task_id: int, **fields) -> None:
    try:
        owner = _call("UpdateTask", pb.UpdateTaskRequest(task_id=task_id, fields_json=json.dumps(fields, ensure_ascii=False)))
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.NOT_FOUND:   # a task that is gone changes nothing, as before
            return
        raise
    if "status" in fields:
        _push_task(owner, task_id, fields["status"], fields.get("reply"))


def _push_task(owner: pb.TaskOwner, task_id: int, status: str, reply: str | None) -> None:
    """Tell the task's owner's open tabs (the queue panel) that it changed state."""
    try:
        hub.publish(owner.owner, "queue", "task",
                    {"batch_id": owner.batch_id, "task_id": task_id, "status": status, "reply": reply})
    except Exception:
        log.warning("Couldn't push task %s", task_id, exc_info=True)


def cancel_batch(batch_id: str) -> int:
    reply = _call("CancelBatch", pb.BatchRef(batch_id=batch_id))
    if reply.HasField("owner"):
        hub.publish(reply.owner, "queue", "changed", {})
    return reply.cancelled


def count_queued_tasks(owner: str | None = None) -> int:
    return _call("CountQueuedTasks", pb.CountQueuedRequest(owner=owner)).count


def tasks_with_status(status: str) -> list[dict]:
    return [_task(t) for t in _call("TasksWithStatus", pb.TasksWithStatusRequest(status=status)).tasks]


def requeue_running_tasks() -> None:
    """Tasks that were running when the server stopped are queued again."""
    _call("RequeueRunningTasks", pb.CatalogOwnerlessRequest())


def give_legacy_chats_and_batches_to(user_id: str) -> None:
    """Chats and batches made before accounts existed become the first user's (their documents move separately)."""
    _call("GiveLegacyChatsAndBatchesTo", pb.GiveLegacyRequest(user_id=user_id))
