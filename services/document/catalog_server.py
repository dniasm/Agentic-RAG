"""The library catalog's gRPC calls (mixed into DocumentServicer): thin wrappers over services/document/catalog.py."""
import json

import grpc

from agentic_rag_proto import document_pb2 as pb
from services.document import catalog


def _opt(msg, name: str):
    return getattr(msg, name) if msg.HasField(name) else None


def _set_optional(msg, row: dict, names) -> None:
    for name in names:
        if row.get(name) is not None:
            setattr(msg, name, row[name])


def document_pb(d: dict) -> pb.CatalogDocument:
    m = pb.CatalogDocument(
        id=d["id"], title=d["title"], original_name=d["original_name"], file_name=d["file_name"], ext=d["ext"],
        sha256=d["sha256"], size_bytes=d["size_bytes"], summary=d["summary"], owner=d["owner"], status=d["status"],
        keywords_json=json.dumps(d["keywords"], ensure_ascii=False), created_at=d["created_at"],
        updated_at=d["updated_at"])
    _set_optional(m, d, ("original_sha256", "pages", "chunks", "error"))
    return m


def task_pb(t: dict) -> pb.CatalogTask:
    m = pb.CatalogTask(id=t["id"], batch_id=t["batch_id"], position=t["position"], instruction=t["instruction"],
                       status=t["status"], reply=t["reply"], tokens=t["tokens"],
                       actions_json=json.dumps(t["actions"], ensure_ascii=False))
    if t.get("payload") is not None:
        m.payload_json = json.dumps(t["payload"], ensure_ascii=False)
    if t.get("checkpoint") is not None:
        m.checkpoint_json = json.dumps(t["checkpoint"], ensure_ascii=False)
    _set_optional(m, t, ("started_at", "finished_at"))
    return m


def batch_pb(b: dict) -> pb.CatalogBatch:
    return pb.CatalogBatch(id=b["id"], session_id=b["session_id"], owner=b["owner"], created_at=b["created_at"],
                           tasks=[task_pb(t) for t in b["tasks"]])


def edit_pb(e: dict, doc_id: str = "") -> pb.EditRecord:
    return pb.EditRecord(id=e["id"], doc_id=e.get("doc_id", doc_id), operation=e["operation"], detail=e["detail"],
                         backup=e["backup"], at=e["at"])


def undone_pb(u: dict) -> pb.UndoneEdit:
    return pb.UndoneEdit(**{k: u[k] for k in ("id", "doc_id", "operation", "detail", "backup", "redo", "ext", "at",
                                              "undone_at", "undo_order")})


def rename_pb(r: dict) -> pb.RenameRecord:
    return pb.RenameRecord(old_title=r["old_title"], new_title=r["new_title"], reason=r["reason"], at=r["at"])


def _fields(text: str | None) -> dict | None:
    """A JSON object from a request (unset or empty: None); anything else is a bad request."""
    if not text:
        return None
    try:
        fields = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"fields are not JSON: {e}") from e
    if not isinstance(fields, dict):
        raise ValueError("fields must be a JSON object")
    return fields


def _required(*values: str) -> None:
    if any(not v.strip() for v in values):
        raise ValueError("a required id or name is empty")


class CatalogMixin:
    # ---------------------------------------------------------------- documents
    async def AddCatalogDocument(self, request, context):
        def add():
            _required(request.file_name, request.sha256)
            return catalog.add_document(
                title=request.title, original_name=request.original_name, file_name=request.file_name, ext=request.ext,
                sha256=request.sha256, size_bytes=request.size_bytes, owner=request.owner or catalog.OWNER,
                conversation_id=request.conversation_id if request.HasField("conversation_id") else None)
        return document_pb(await self._run(context, add))

    async def LinkUpload(self, request, context):
        await self._run(context, catalog.link_upload, request.doc_id, request.conversation_id)
        return pb.DeleteReply()

    async def ListConversationDocuments(self, request, context):
        docs = await self._run(context, catalog.conversation_documents, request.owner or catalog.OWNER,
                               request.conversation_id)
        return pb.CatalogDocumentList(documents=[document_pb(d) for d in docs])

    async def GetCatalogDocument(self, request, context):
        d = await self._run(context, catalog.get_document, request.doc_id)
        return pb.OptionalCatalogDocument(document=document_pb(d) if d else None)

    async def FindCatalogDocumentByHash(self, request, context):
        d = await self._run(context, catalog.find_by_hash, request.sha256, request.owner or catalog.OWNER)
        return pb.OptionalCatalogDocument(document=document_pb(d) if d else None)

    async def ListCatalogDocuments(self, request, context):
        docs = await self._run(context, catalog.list_documents, _opt(request, "status"), _opt(request, "owner"))
        return pb.CatalogDocumentList(documents=[document_pb(d) for d in docs])

    async def UpdateCatalogDocument(self, request, context):
        def update():
            r = request.rename if request.HasField("rename") else None
            catalog.update_document(request.doc_id, _fields(request.fields_json) or {},
                                    (r.old_title, r.new_title, r.reason) if r else None)
        await self._run(context, update)
        return pb.DeleteReply()

    async def DeleteCatalogDocument(self, request, context):
        await self._run(context, catalog.delete_document, request.doc_id)
        return pb.DeleteReply()

    async def RecordRename(self, request, context):
        await self._run(context, catalog.record_rename, request.doc_id, request.old_title, request.new_title,
                        request.reason)
        return pb.DeleteReply()

    async def RenameHistories(self, request, context):
        found = await self._run(context, catalog.rename_histories)
        return pb.RenameHistoriesReply(documents=[
            pb.DocumentRenames(doc_id=doc_id, renames=[rename_pb(r) for r in rows]) for doc_id, rows in found.items()])

    async def RenameHistory(self, request, context):
        rows = await self._run(context, catalog.rename_history, request.doc_id)
        return pb.RenameHistoryReply(renames=[rename_pb(r) for r in rows])

    async def UsedFileNames(self, request, context):
        names = await self._run(context, catalog.used_file_names, _opt(request, "exclude_doc"))
        return pb.UsedFileNamesReply(file_names=sorted(names))

    # ---------------------------------------------------------------- edits and undo
    async def RecordEdit(self, request, context):
        edit_id = await self._run(context, lambda: catalog.record_edit(
            request.doc_id, request.operation, request.detail, request.backup,
            _fields(_opt(request, "document_fields_json"))))
        return pb.RecordEditReply(edit_id=edit_id)

    async def LastEdit(self, request, context):
        e = await self._run(context, catalog.last_edit, request.doc_id)
        return pb.OptionalEdit(edit=edit_pb(e, request.doc_id) if e else None)

    async def EditExists(self, request, context):
        return pb.EditExistsReply(exists=await self._run(context, catalog.edit_exists, request.edit_id))

    async def EditHistory(self, request, context):
        rows = await self._run(context, catalog.edit_history, request.doc_id)
        return pb.EditHistoryReply(edits=[pb.EditSummary(**r) for r in rows])

    async def DeleteEdit(self, request, context):
        await self._run(context, catalog.delete_edit, request.edit_id)
        return pb.DeleteReply()

    async def UndoEdit(self, request, context):
        e = request.edit
        await self._run(context, lambda: catalog.move_edit_to_undone(
            {"id": e.id, "doc_id": e.doc_id, "operation": e.operation, "detail": e.detail, "backup": e.backup,
             "at": e.at}, request.redo, request.ext, _fields(_opt(request, "document_fields_json"))))
        return pb.DeleteReply()

    async def LastUndone(self, request, context):
        u = await self._run(context, catalog.last_undone, request.doc_id)
        return pb.OptionalUndoneEdit(undone=undone_pb(u) if u else None)

    async def RedoEdit(self, request, context):
        await self._run(context, lambda: catalog.restore_undone(
            request.undone_id, _fields(_opt(request, "document_fields_json"))))
        return pb.DeleteReply()

    async def ClearUndone(self, request, context):
        rows = await self._run(context, catalog.clear_undone, request.doc_id)
        return pb.ClearUndoneReply(dropped=[undone_pb(r) for r in rows])

    # ---------------------------------------------------------------- sessions
    async def LoadLibrarySession(self, request, context):
        s = await self._run(context, catalog.load_session, request.session_id)
        m = pb.LibrarySession(id=s["id"], turn=s["turn"], llm_messages_json=json.dumps(s["llm_messages"], ensure_ascii=False),
                              display_json=json.dumps(s["display"], ensure_ascii=False), guard_json=json.dumps(s["guard"]))
        if s["owner"] is not None:
            m.owner = s["owner"]
        return m

    async def SaveLibrarySession(self, request, context):
        def save():
            _required(request.id)
            catalog.save_session({"id": request.id, "turn": request.turn, "owner": _opt(request, "owner"),
                                  "llm_messages": json.loads(request.llm_messages_json or "[]"),
                                  "display": json.loads(request.display_json or "[]"),
                                  "guard": json.loads(request.guard_json or "{}")})
        await self._run(context, save)
        return pb.DeleteReply()

    # ---------------------------------------------------------------- batches
    async def AddBatch(self, request, context):
        def add():
            _required(request.session_id)
            items = [(i.instruction, json.loads(i.payload_json) if i.HasField("payload_json") else None)
                     for i in request.items]
            return catalog.add_batch(request.session_id, items, request.owner or catalog.OWNER)
        return batch_pb(await self._run(context, add))

    async def GetBatch(self, request, context):
        b = await self._run(context, catalog.get_batch, request.batch_id)
        return pb.OptionalBatch(batch=batch_pb(b) if b else None)

    async def ActiveOrRecentBatches(self, request, context):
        rows = await self._run(context, catalog.active_or_recent_batches, request.hours, _opt(request, "owner"))
        return pb.BatchList(batches=[batch_pb(b) for b in rows])

    async def ListBatches(self, request, context):
        rows = await self._run(context, catalog.list_batches, request.limit, _opt(request, "owner"))
        return pb.BatchList(batches=[batch_pb(b) for b in rows])

    async def TasksRunFor(self, request, context):
        rows = await self._run(context, catalog.tasks_run_for, request.session_id)
        return pb.TasksRunReply(tasks=[pb.TaskRun(actions_json=json.dumps(r["actions"], ensure_ascii=False),
                                                  finished_at=r["finished_at"]) for r in rows])

    async def PendingTasksFor(self, request, context):
        return pb.CountReply(count=await self._run(context, catalog.pending_tasks_for, request.session_id))

    async def NextQueuedTask(self, request, context):
        t = await self._run(context, catalog.next_queued_task)
        return pb.OptionalTask(task=task_pb(t) if t else None)

    async def ClaimNextTask(self, request, context):
        t = await self._run(context, catalog.claim_next_task)
        return pb.OptionalTask(task=task_pb(t) if t else None)

    async def GetTask(self, request, context):
        t = await self._run(context, catalog.get_task, request.task_id)
        return pb.OptionalTask(task=task_pb(t) if t else None)

    async def UpdateTask(self, request, context):
        owner = await self._run(context, lambda: catalog.update_task(request.task_id, _fields(request.fields_json) or {}))
        if owner is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"no task {request.task_id}")
        return pb.TaskOwner(batch_id=owner["batch_id"], owner=owner["owner"])

    async def CancelBatch(self, request, context):
        n, owner = await self._run(context, catalog.cancel_batch, request.batch_id)
        reply = pb.CancelBatchReply(cancelled=n)
        if owner is not None:
            reply.owner = owner
        return reply

    async def CountQueuedTasks(self, request, context):
        return pb.CountReply(count=await self._run(context, catalog.count_queued_tasks, _opt(request, "owner")))

    async def TasksWithStatus(self, request, context):
        rows = await self._run(context, catalog.tasks_with_status, request.status)
        return pb.TaskList(tasks=[task_pb(t) for t in rows])

    async def RequeueRunningTasks(self, request, context):
        return pb.CountReply(count=await self._run(context, catalog.requeue_running_tasks))

    async def GiveLegacyChatsAndBatchesTo(self, request, context):
        def give():
            _required(request.user_id)
            catalog.give_legacy_chats_and_batches_to(request.user_id)
        await self._run(context, give)
        return pb.DeleteReply()

    # ---------------------------------------------------------------- migration
    async def ImportCatalog(self, request, context):
        d = request.dump

        def rows():
            def doc(m):
                row = {"id": m.id, "title": m.title, "original_name": m.original_name, "file_name": m.file_name,
                       "ext": m.ext, "sha256": m.sha256, "original_sha256": _opt(m, "original_sha256"),
                       "size_bytes": m.size_bytes, "pages": _opt(m, "pages"), "chunks": _opt(m, "chunks"),
                       "summary": m.summary, "keywords": m.keywords_json or "[]", "owner": m.owner, "status": m.status,
                       "error": _opt(m, "error"), "created_at": m.created_at, "updated_at": m.updated_at}
                return row

            def task(m):
                return {"id": m.id, "batch_id": m.batch_id, "position": m.position, "instruction": m.instruction,
                        "payload": _opt(m, "payload_json"), "checkpoint": _opt(m, "checkpoint_json"),
                        "status": m.status, "reply": m.reply, "actions": m.actions_json or "[]", "tokens": m.tokens,
                        "started_at": _opt(m, "started_at"), "finished_at": _opt(m, "finished_at")}

            return {
                "documents": [doc(m) for m in d.documents],
                "renames": [{"id": m.id, "doc_id": m.doc_id, "old_title": m.old_title, "new_title": m.new_title,
                             "reason": m.reason, "at": m.at} for m in d.renames],
                "edits": [{"id": m.id, "doc_id": m.doc_id, "operation": m.operation, "detail": m.detail,
                           "backup": m.backup, "at": m.at} for m in d.edits],
                "undone_edits": [{k: getattr(m, k) for k in ("id", "doc_id", "operation", "detail", "backup", "redo",
                                                             "ext", "at", "undone_at", "undo_order")}
                                 for m in d.undone_edits],
                "batches": [{"id": m.id, "session_id": m.session_id, "owner": m.owner, "created_at": m.created_at}
                            for m in d.batches],
                "batch_tasks": [task(t) for b in d.batches for t in b.tasks],
                "sessions": [{"id": m.session.id, "turn": m.session.turn, "llm_messages": m.session.llm_messages_json,
                              "display": m.session.display_json, "guard": m.session.guard_json,
                              "owner": _opt(m.session, "owner") or catalog.OWNER, "created_at": m.created_at,
                              "updated_at": m.updated_at} for m in d.sessions],
            }
        made = await self._run(context, lambda: catalog.import_rows(rows()))
        return pb.ImportCatalogReply(created=pb.CatalogCounts(**made))

    async def CountCatalog(self, request, context):
        return pb.CountCatalogReply(held=pb.CatalogCounts(**await self._run(context, catalog.counts)))
