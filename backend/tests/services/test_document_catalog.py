"""The Document service's library catalog (documents, renames, edits and undo, chats, batches) through its real gRPC
server, which the root conftest.py runs in this process, and scripts/migrate_library_catalog.py."""
import importlib.util
import json
import sqlite3
import sys
import threading
from pathlib import Path

import grpc
import pytest

from agentic_rag_proto import document_pb2 as pb
from agentic_rag_proto import document_pb2_grpc as pb_grpc
from backend.config.settings import settings
from services.document import db

ROOT = Path(__file__).resolve().parents[3]
TABLES = ("documents", "renames", "edits", "undone_edits", "batches", "batch_tasks", "sessions")
EMPTY = pb.CatalogOwnerlessRequest()


def _load_migrate():
    spec = importlib.util.spec_from_file_location("migrate_library_catalog", ROOT / "scripts" / "migrate_library_catalog.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module   # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


migrate = _load_migrate()


@pytest.fixture(autouse=True)
def empty_catalog():
    with db.shared().tx() as c:
        for t in TABLES:
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM sqlite_sequence WHERE name IN ('renames', 'edits', 'batch_tasks')")


@pytest.fixture
def stub():
    with grpc.insecure_channel(settings.document_service_addr) as channel:
        yield pb_grpc.DocumentServiceStub(channel)


def add(stub, title="Report", owner="u1", sha="s1", file_name="report.pdf"):
    return stub.AddCatalogDocument(pb.AddCatalogDocumentRequest(
        title=title, original_name="orig.pdf", file_name=file_name, ext=".pdf", sha256=sha, size_bytes=10, owner=owner))


def update(stub, doc_id, rename=None, **fields):
    stub.UpdateCatalogDocument(pb.UpdateCatalogDocumentRequest(doc_id=doc_id, fields_json=json.dumps(fields), rename=rename))


def edit(stub, doc_id, n=1, fields=None):
    return stub.RecordEdit(pb.RecordEditRequest(
        doc_id=doc_id, operation="replace", detail=f"edit {n}", backup=f"b{n}.pdf",
        document_fields_json=json.dumps(fields) if fields else None)).edit_id


def undo(stub, doc_id, fields=None):
    last = stub.LastEdit(pb.CatalogDocumentRef(doc_id=doc_id)).edit
    stub.UndoEdit(pb.UndoEditRequest(edit=last, redo=f"r{last.id}.pdf", ext=".pdf",
                                     document_fields_json=json.dumps(fields) if fields else None))
    return last


def get(stub, doc_id):
    reply = stub.GetCatalogDocument(pb.CatalogDocumentRef(doc_id=doc_id))
    return reply.document if reply.HasField("document") else None


# ---------------------------------------------------------------- documents
def test_add_get_and_defaults(stub):
    d = add(stub)
    assert len(d.id) == 12 and d.status == "processing" and d.sha256 == d.original_sha256 == "s1"
    assert d.owner == "u1" and d.summary == "" and json.loads(d.keywords_json) == []
    assert not d.HasField("pages") and not d.HasField("chunks") and not d.HasField("error")
    assert get(stub, d.id) == d
    assert get(stub, "nope") is None
    assert add(stub, owner="").owner == "library"
    with pytest.raises(grpc.RpcError) as e:
        add(stub, sha="")
    assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_update_fields_null_keywords_and_whitelist(stub):
    d = add(stub)
    update(stub, d.id, status="failed", error="boom", pages=3, keywords=["a", "b"], summary="s")
    got = get(stub, d.id)
    assert (got.status, got.error, got.pages, got.summary) == ("failed", "boom", 3, "s")
    assert json.loads(got.keywords_json) == ["a", "b"]
    update(stub, d.id, status="indexed", error=None, chunks=7)
    got = get(stub, d.id)
    assert not got.HasField("error") and got.chunks == 7 and got.updated_at >= d.updated_at
    for bad in ({"id": "x"}, {"nope": 1}, {}):
        with pytest.raises(grpc.RpcError) as e:
            update(stub, d.id, **bad)
        assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    with pytest.raises(grpc.RpcError):
        stub.UpdateCatalogDocument(pb.UpdateCatalogDocumentRequest(doc_id=d.id, fields_json="[1]"))
    assert get(stub, d.id).id == d.id


def test_update_with_rename_is_atomic(stub):
    d = add(stub)
    update(stub, d.id, rename=pb.RecordRenameRequest(doc_id=d.id, old_title="Report", new_title="Q3", reason="user"),
           title="Q3")
    assert get(stub, d.id).title == "Q3"
    assert [(r.old_title, r.new_title, r.reason) for r in stub.RenameHistory(pb.CatalogDocumentRef(doc_id=d.id)).renames] \
        == [("Report", "Q3", "user")]
    with pytest.raises(grpc.RpcError):   # a bad column: neither the update nor the rename happens
        update(stub, d.id, rename=pb.RecordRenameRequest(doc_id=d.id, old_title="Q3", new_title="Q4", reason="user"),
               bogus=1)
    assert get(stub, d.id).title == "Q3"
    assert len(stub.RenameHistory(pb.CatalogDocumentRef(doc_id=d.id)).renames) == 1


def test_find_by_hash_is_per_owner_and_remembers_the_original(stub):
    a = add(stub, owner="alice", sha="h1")
    add(stub, owner="bob", sha="h1", file_name="b.pdf")
    update(stub, a.id, sha256="h2")     # edited since: the uploaded bytes are still a duplicate
    for sha in ("h1", "h2"):
        found = stub.FindCatalogDocumentByHash(pb.FindByHashRequest(sha256=sha, owner="alice"))
        assert found.document.id == a.id
    assert stub.FindCatalogDocumentByHash(pb.FindByHashRequest(sha256="h1", owner="bob")).document.id != a.id
    assert not stub.FindCatalogDocumentByHash(pb.FindByHashRequest(sha256="h1", owner="carol")).HasField("document")
    assert not stub.FindCatalogDocumentByHash(pb.FindByHashRequest(sha256="zzz", owner="alice")).HasField("document")


def test_list_documents_filters_and_orders(stub):
    a = add(stub, owner="alice", file_name="a.pdf")
    b = add(stub, owner="bob", file_name="b.pdf")
    c = add(stub, owner="alice", file_name="c.pdf")
    update(stub, a.id, status="indexed")
    ids = lambda **kw: [d.id for d in stub.ListCatalogDocuments(pb.ListCatalogDocumentsRequest(**kw)).documents]
    assert sorted(ids()) == sorted([a.id, b.id, c.id])
    assert sorted(ids(owner="alice")) == sorted([a.id, c.id]) and ids(owner="nobody") == []
    assert ids(status="indexed", owner="alice") == [a.id] and ids(status="indexed", owner="bob") == []


def test_delete_document_removes_renames_and_edits_but_not_other_documents(stub):
    a, b = add(stub), add(stub, sha="s2", file_name="b.pdf")
    for d in (a, b):
        stub.RecordRename(pb.RecordRenameRequest(doc_id=d.id, old_title="x", new_title="y", reason="user"))
        edit(stub, d.id)
    stub.DeleteCatalogDocument(pb.CatalogDocumentRef(doc_id=a.id))
    assert get(stub, a.id) is None and get(stub, b.id) is not None
    assert not stub.RenameHistory(pb.CatalogDocumentRef(doc_id=a.id)).renames
    assert not stub.EditHistory(pb.CatalogDocumentRef(doc_id=a.id)).edits
    assert len(stub.RenameHistory(pb.CatalogDocumentRef(doc_id=b.id)).renames) == 1
    assert len(stub.EditHistory(pb.CatalogDocumentRef(doc_id=b.id)).edits) == 1
    stub.DeleteCatalogDocument(pb.CatalogDocumentRef(doc_id="gone"))   # nothing there: still fine


def test_rename_histories_oldest_first_per_document(stub):
    a, b = add(stub), add(stub, sha="s2", file_name="b.pdf")
    for doc, old, new in ((a, "1", "2"), (b, "x", "y"), (a, "2", "3")):
        stub.RecordRename(pb.RecordRenameRequest(doc_id=doc.id, old_title=old, new_title=new, reason="titled"))
    got = {d.doc_id: [(r.old_title, r.new_title) for r in d.renames] for d in stub.RenameHistories(EMPTY).documents}
    assert got == {a.id: [("1", "2"), ("2", "3")], b.id: [("x", "y")]}
    assert stub.RenameHistory(pb.CatalogDocumentRef(doc_id=a.id)).renames[0].at


def test_used_file_names_are_normalised_and_can_exclude_one(stub):
    a = add(stub, file_name="Sub\\Report.PDF")
    add(stub, sha="s2", file_name="b.pdf")
    assert set(stub.UsedFileNames(pb.UsedFileNamesRequest()).file_names) == {"sub/report.pdf", "b.pdf"}
    assert set(stub.UsedFileNames(pb.UsedFileNamesRequest(exclude_doc=a.id)).file_names) == {"b.pdf"}


# ---------------------------------------------------------------- edits and undo
def test_record_edit_history_last_exists_delete(stub):
    d = add(stub)
    ref = pb.CatalogDocumentRef(doc_id=d.id)
    assert not stub.LastEdit(ref).HasField("edit") and not stub.EditHistory(ref).edits
    first, second = edit(stub, d.id, 1), edit(stub, d.id, 2)
    assert second > first
    last = stub.LastEdit(ref).edit
    assert (last.id, last.detail, last.backup, last.operation) == (second, "edit 2", "b2.pdf", "replace") and last.at
    assert [e.detail for e in stub.EditHistory(ref).edits] == ["edit 1", "edit 2"]
    assert stub.EditExists(pb.EditRef(edit_id=first)).exists
    stub.DeleteEdit(pb.EditRef(edit_id=second))
    assert not stub.EditExists(pb.EditRef(edit_id=second)).exists and stub.LastEdit(ref).edit.id == first


def test_record_edit_updates_the_document_in_the_same_transaction(stub):
    d = add(stub)
    edit(stub, d.id, fields={"sha256": "new", "pages": 9})
    assert (get(stub, d.id).sha256, get(stub, d.id).pages) == ("new", 9)
    with pytest.raises(grpc.RpcError):   # a bad update rolls the edit record back too
        edit(stub, d.id, n=2, fields={"bogus": 1})
    assert len(stub.EditHistory(pb.CatalogDocumentRef(doc_id=d.id)).edits) == 1


def test_undo_moves_the_edit_and_updates_the_document_atomically(stub):
    d = add(stub)
    ref = pb.CatalogDocumentRef(doc_id=d.id)
    edit(stub, d.id, 1)
    last = undo(stub, d.id, {"ext": ".md", "file_name": "report.md"})
    assert not stub.EditExists(pb.EditRef(edit_id=last.id)).exists
    u = stub.LastUndone(ref).undone
    assert (u.id, u.doc_id, u.backup, u.redo, u.ext, u.at, u.undo_order) == (last.id, d.id, "b1.pdf", f"r{last.id}.pdf",
                                                                           ".pdf", last.at, 1) and u.undone_at
    assert (get(stub, d.id).ext, get(stub, d.id).file_name) == (".md", "report.md")
    edit(stub, d.id, 2)
    with pytest.raises(grpc.RpcError):   # a failing update leaves the edit in the history, nothing undone
        undo(stub, d.id, {"bogus": 1})
    assert stub.LastEdit(ref).edit.detail == "edit 2" and stub.LastUndone(ref).undone.id == last.id


def test_redo_brings_the_edit_back_under_its_own_id_and_updates_the_document(stub):
    d = add(stub)
    ref = pb.CatalogDocumentRef(doc_id=d.id)
    edit(stub, d.id, 1)
    undone = undo(stub, d.id)
    stub.RedoEdit(pb.RedoEditRequest(undone_id=undone.id, document_fields_json=json.dumps({"ext": ".pdf", "file_name": "r.pdf"})))
    back = stub.LastEdit(ref).edit
    assert (back.id, back.detail, back.at) == (undone.id, undone.detail, undone.at)
    assert not stub.LastUndone(ref).HasField("undone") and get(stub, d.id).file_name == "r.pdf"
    stub.RedoEdit(pb.RedoEditRequest(undone_id=12345))   # nothing to redo: no change, no error
    assert len(stub.EditHistory(ref).edits) == 1


def test_last_undone_is_the_most_recent_undo_not_the_highest_id(stub):
    d = add(stub)
    ref = pb.CatalogDocumentRef(doc_id=d.id)
    first, second = edit(stub, d.id, 1), edit(stub, d.id, 2)
    undo(stub, d.id)    # undoes the newer edit (second) first
    undo(stub, d.id)    # then the older one: a redo brings back the older one first
    assert stub.LastUndone(ref).undone.id == first
    stub.RedoEdit(pb.RedoEditRequest(undone_id=first))
    assert stub.LastUndone(ref).undone.id == second
    assert stub.LastUndone(ref).undone.undo_order == 1


def test_clear_undone_returns_what_it_dropped_for_one_document_only(stub):
    a, b = add(stub), add(stub, sha="s2", file_name="b.pdf")
    for d in (a, b):
        edit(stub, d.id)
        undo(stub, d.id)
    dropped = stub.ClearUndone(pb.CatalogDocumentRef(doc_id=a.id)).dropped
    assert [(u.doc_id, u.redo) for u in dropped] == [(a.id, dropped[0].redo)] and dropped[0].redo.endswith(".pdf")
    assert not stub.LastUndone(pb.CatalogDocumentRef(doc_id=a.id)).HasField("undone")
    assert stub.LastUndone(pb.CatalogDocumentRef(doc_id=b.id)).HasField("undone")
    assert not stub.ClearUndone(pb.CatalogDocumentRef(doc_id=a.id)).dropped


# ---------------------------------------------------------------- sessions
def session(sid="chat1", owner=None, turn=1):
    m = pb.LibrarySession(id=sid, turn=turn, llm_messages_json=json.dumps([{"role": "user", "content": "héllo"}]),
                          display_json=json.dumps([{"text": "hi"}]), guard_json=json.dumps({"renamed": ["a"]}))
    if owner:
        m.owner = owner
    return m


def test_sessions_load_default_save_and_keep_owner(stub):
    fresh = stub.LoadLibrarySession(pb.LibrarySessionRef(session_id="new"))
    assert (fresh.id, fresh.turn, fresh.llm_messages_json, fresh.display_json, fresh.guard_json) == ("new", 0, "[]", "[]", "{}")
    assert not fresh.HasField("owner")
    stub.SaveLibrarySession(session(owner="alice"))
    got = stub.LoadLibrarySession(pb.LibrarySessionRef(session_id="chat1"))
    assert got.owner == "alice" and got.turn == 1 and json.loads(got.llm_messages_json)[0]["content"] == "héllo"
    assert json.loads(got.guard_json) == {"renamed": ["a"]}
    stub.SaveLibrarySession(session(owner="mallory", turn=2))   # an update does not move the chat to someone else
    got = stub.LoadLibrarySession(pb.LibrarySessionRef(session_id="chat1"))
    assert (got.owner, got.turn) == ("alice", 2)
    stub.SaveLibrarySession(session(sid="old"))
    assert stub.LoadLibrarySession(pb.LibrarySessionRef(session_id="old")).owner == "library"
    with pytest.raises(grpc.RpcError):
        stub.SaveLibrarySession(pb.LibrarySession(id=""))


def test_give_legacy_chats_and_batches_moves_only_library_owned_rows(stub):
    stub.SaveLibrarySession(session("legacy"))
    stub.SaveLibrarySession(session("mine", owner="bob"))
    legacy = batch(stub, "legacy", ["x"], owner="")
    bobs = batch(stub, "mine", ["y"], owner="bob")
    stub.GiveLegacyChatsAndBatchesTo(pb.GiveLegacyRequest(user_id="alice"))
    assert stub.LoadLibrarySession(pb.LibrarySessionRef(session_id="legacy")).owner == "alice"
    assert stub.LoadLibrarySession(pb.LibrarySessionRef(session_id="mine")).owner == "bob"
    assert get_batch(stub, legacy.id).owner == "alice" and get_batch(stub, bobs.id).owner == "bob"
    with pytest.raises(grpc.RpcError):
        stub.GiveLegacyChatsAndBatchesTo(pb.GiveLegacyRequest(user_id=" "))


# ---------------------------------------------------------------- batches
def batch(stub, session_id, items, owner="u1"):
    request = pb.AddBatchRequest(session_id=session_id, owner=owner)
    for item in items:
        text, change = item if isinstance(item, tuple) else (item, None)
        request.items.add(instruction=text, **({"payload_json": json.dumps(change)} if change else {}))
    return stub.AddBatch(request)


def get_batch(stub, batch_id):
    reply = stub.GetBatch(pb.BatchRef(batch_id=batch_id))
    return reply.batch if reply.HasField("batch") else None


def task_update(stub, task_id, **fields):
    return stub.UpdateTask(pb.UpdateTaskRequest(task_id=task_id, fields_json=json.dumps(fields)))


def test_add_batch_numbers_tasks_and_keeps_payloads(stub):
    b = batch(stub, "chat1", ["one", ("two", {"op": "rename", "title": "Ünï"}), "three"])
    assert len(b.id) == 10 and b.session_id == "chat1" and b.owner == "u1"
    assert [(t.position, t.instruction, t.status) for t in b.tasks] == [(1, "one", "queued"), (2, "two", "queued"),
                                                                       (3, "three", "queued")]
    assert not b.tasks[0].HasField("payload_json") and json.loads(b.tasks[1].payload_json)["title"] == "Ünï"
    assert b.tasks[0].actions_json == "[]" and b.tasks[0].tokens == 0 and not b.tasks[0].HasField("started_at")
    assert get_batch(stub, b.id) == b and get_batch(stub, "nope") is None
    assert batch(stub, "chat1", ["x"], owner="").owner == "library"
    with pytest.raises(grpc.RpcError):
        batch(stub, "", ["x"])


def test_claim_next_task_is_atomic_and_in_queue_order(stub):
    first = batch(stub, "c1", ["a1", "a2"])
    second = batch(stub, "c2", ["b1"])
    nxt = stub.NextQueuedTask(EMPTY).task
    assert nxt.instruction == "a1" and nxt.status == "queued"   # peeking changes nothing
    assert stub.NextQueuedTask(EMPTY).task.id == nxt.id
    claimed = [stub.ClaimNextTask(EMPTY).task for _ in range(3)]
    assert [t.instruction for t in claimed] == ["a1", "a2", "b1"]
    assert all(t.status == "running" and t.started_at for t in claimed)
    assert not stub.ClaimNextTask(EMPTY).HasField("task") and not stub.NextQueuedTask(EMPTY).HasField("task")
    assert first.id != second.id


def test_concurrent_claims_never_hand_out_a_task_twice(stub):
    batch(stub, "c1", [f"t{i}" for i in range(20)])
    got, lock = [], threading.Lock()

    def worker():
        while (t := stub.ClaimNextTask(EMPTY)).HasField("task"):
            with lock:
                got.append(t.task.id)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(got) == 20 and len(set(got)) == 20


def test_update_task_json_columns_progress_and_owner(stub):
    b = batch(stub, "c1", ["x"], owner="alice")
    t = b.tasks[0]
    who = task_update(stub, t.id, status="running", started_at="2026-01-01T00:00:00+00:00",
                      checkpoint={"doc_id": "d", "sha256": "s", "title": "T"})
    assert (who.batch_id, who.owner) == (b.id, "alice")
    got = stub.GetTask(pb.TaskRef(task_id=t.id)).task
    assert got.status == "running" and json.loads(got.checkpoint_json)["title"] == "T"
    task_update(stub, t.id, status="done", reply="ok", actions=[{"tool": "rename"}], tokens=42,
                finished_at="2026-01-01T00:01:00+00:00")
    got = stub.GetTask(pb.TaskRef(task_id=t.id)).task
    assert (got.reply, got.tokens, json.loads(got.actions_json)) == ("ok", 42, [{"tool": "rename"}])
    task_update(stub, t.id, status="queued", started_at=None, checkpoint=None)   # a requeue clears them
    got = stub.GetTask(pb.TaskRef(task_id=t.id)).task
    assert not got.HasField("started_at") and not got.HasField("checkpoint_json")
    for bad in ({"payload": "x"}, {"id": 5}, {}):
        with pytest.raises(grpc.RpcError) as e:
            task_update(stub, t.id, **bad)
        assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    with pytest.raises(grpc.RpcError) as e:
        task_update(stub, 99999, status="done")
    assert e.value.code() == grpc.StatusCode.NOT_FOUND
    assert not stub.GetTask(pb.TaskRef(task_id=99999)).HasField("task")


def test_tasks_run_and_pending_for_a_session(stub):
    b = batch(stub, "chatA", ["one", "two", "three"])
    other = batch(stub, "chatB", ["x"])
    assert stub.PendingTasksFor(pb.LibrarySessionRef(session_id="chatA")).count == 3
    task_update(stub, b.tasks[0].id, status="running")
    task_update(stub, b.tasks[1].id, status="done", actions=[{"n": 2}], finished_at="2026-01-01T00:00:02+00:00")
    task_update(stub, b.tasks[2].id, status="failed", actions=[{"n": 3}], finished_at="2026-01-01T00:00:01+00:00")
    assert stub.PendingTasksFor(pb.LibrarySessionRef(session_id="chatA")).count == 1
    assert stub.PendingTasksFor(pb.LibrarySessionRef(session_id="chatB")).count == 1
    ran = stub.TasksRunFor(pb.LibrarySessionRef(session_id="chatA")).tasks
    assert [(json.loads(r.actions_json), r.finished_at) for r in ran] == [
        ([{"n": 3}], "2026-01-01T00:00:01+00:00"), ([{"n": 2}], "2026-01-01T00:00:02+00:00")]
    assert not stub.TasksRunFor(pb.LibrarySessionRef(session_id="chatB")).tasks and other.id


def test_cancel_batch_only_cancels_queued_tasks_and_reports_the_owner(stub):
    b = batch(stub, "c1", ["a", "b", "c"], owner="alice")
    task_update(stub, b.tasks[0].id, status="running")
    task_update(stub, b.tasks[1].id, status="done")
    reply = stub.CancelBatch(pb.BatchRef(batch_id=b.id))
    assert (reply.cancelled, reply.owner) == (1, "alice")
    after = get_batch(stub, b.id)
    assert [t.status for t in after.tasks] == ["running", "done", "cancelled"] and after.tasks[2].finished_at
    unknown = stub.CancelBatch(pb.BatchRef(batch_id="nope"))
    assert unknown.cancelled == 0 and not unknown.HasField("owner")


def test_count_queued_and_tasks_with_status_and_requeue_running(stub):
    a = batch(stub, "c1", ["a1", "a2"], owner="alice")
    batch(stub, "c2", ["b1"], owner="bob")
    assert stub.CountQueuedTasks(pb.CountQueuedRequest()).count == 3
    assert stub.CountQueuedTasks(pb.CountQueuedRequest(owner="alice")).count == 2
    assert stub.CountQueuedTasks(pb.CountQueuedRequest(owner="carol")).count == 0
    task_update(stub, a.tasks[0].id, status="running", started_at="2026-01-01T00:00:00+00:00")
    running = stub.TasksWithStatus(pb.TasksWithStatusRequest(status="running")).tasks
    assert [t.instruction for t in running] == ["a1"]
    assert stub.RequeueRunningTasks(EMPTY).count == 1
    again = stub.GetTask(pb.TaskRef(task_id=a.tasks[0].id)).task
    assert again.status == "queued" and not again.HasField("started_at")
    assert stub.CountQueuedTasks(pb.CountQueuedRequest()).count == 3
    assert not stub.TasksWithStatus(pb.TasksWithStatusRequest(status="running")).tasks


def test_list_and_active_or_recent_batches_respect_owner_and_limit(stub):
    old = batch(stub, "c1", ["old"], owner="alice")
    mine = batch(stub, "c2", ["mine"], owner="alice")
    theirs = batch(stub, "c3", ["theirs"], owner="bob")
    with db.shared().tx() as c:   # an old finished batch drops off the queue panel, an old unfinished one stays
        c.execute("UPDATE batches SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (old.id,))
        c.execute("UPDATE batch_tasks SET status = 'done' WHERE batch_id = ?", (old.id,))
    ids = lambda rows: [b.id for b in rows.batches]
    assert ids(stub.ListBatches(pb.ListBatchesRequest(limit=10))) == [theirs.id, mine.id, old.id]
    assert ids(stub.ListBatches(pb.ListBatchesRequest(limit=2))) == [theirs.id, mine.id]
    assert ids(stub.ListBatches(pb.ListBatchesRequest(limit=10, owner="alice"))) == [mine.id, old.id]
    assert ids(stub.ActiveOrRecentBatches(pb.ActiveBatchesRequest(hours=1))) == [theirs.id, mine.id]
    assert ids(stub.ActiveOrRecentBatches(pb.ActiveBatchesRequest(hours=1, owner="alice"))) == [mine.id]
    with db.shared().tx() as c:
        c.execute("UPDATE batch_tasks SET status = 'running' WHERE batch_id = ?", (old.id,))
    assert ids(stub.ActiveOrRecentBatches(pb.ActiveBatchesRequest(hours=0.001, owner="alice"))) == [mine.id, old.id]


# ---------------------------------------------------------------- migration
def make_source(path: Path) -> dict:
    """An app.db as the orchestrator made it, with a bit of everything (and a pre-accounts row)."""
    from backend.library import catalog as old
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(old.SCHEMA)
    conn.executemany(
        "INSERT INTO documents (id, title, original_name, file_name, ext, sha256, original_sha256, size_bytes, pages, "
        "chunks, summary, keywords, owner, status, error, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("d1", "Ünïcode", "o.pdf", "u.pdf", ".pdf", "s1", "o1", 100, 3, 5, "sum", '["k1","k2"]', "alice", "indexed", None,
          "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"),
         ("d2", "Old", "o2.pdf", "o2.pdf", ".pdf", "s2", None, 7, None, None, "", "[]", "library", "failed", "bad",
          "2026-01-03T00:00:00+00:00", "2026-01-03T00:00:00+00:00")])
    conn.execute("INSERT INTO renames (id, doc_id, old_title, new_title, reason, at) VALUES (7, 'd1', 'a', 'b', 'user', 't')")
    conn.execute("INSERT INTO edits (id, doc_id, operation, detail, backup, at) VALUES (4, 'd1', 'replace', 'x', 'b.pdf', 't')")
    conn.execute("INSERT INTO undone_edits (id, doc_id, operation, detail, backup, redo, ext, at, undone_at, undo_order) "
                 "VALUES (3, 'd1', 'append', 'y', 'b0.pdf', 'r.pdf', '.md', 't0', 't1', 5)")
    conn.execute("INSERT INTO batches (id, session_id, owner, created_at) VALUES ('b1', 'chat1', 'alice', 't')")
    conn.execute("INSERT INTO batch_tasks (id, batch_id, position, instruction, payload, checkpoint, status, reply, actions, "
                 "tokens, started_at, finished_at) VALUES (9, 'b1', 1, 'do', '{\"op\": 1}', '{\"doc_id\": \"d1\"}', 'done', "
                 "'ok', '[{\"a\": 1}]', 12, 's', 'f')")
    conn.execute("INSERT INTO batch_tasks (id, batch_id, position, instruction, status) VALUES (10, 'b1', 2, 'next', 'queued')")
    conn.execute("INSERT INTO sessions (id, turn, llm_messages, display, guard, owner, created_at, updated_at) "
                 "VALUES ('chat1', 2, '[{\"role\": \"user\"}]', '[1]', '{\"g\": 1}', 'alice', 'c', 'u')")
    conn.commit()
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    conn.close()
    return counts


def test_migration_copies_every_table_keeps_ids_and_is_idempotent(stub, tmp_path):
    source = tmp_path / "app.db"
    counts = make_source(source)
    before = source.read_bytes()
    report = migrate.migrate(source, settings.document_service_addr, backup_dest=tmp_path / "backups")
    assert report.ok, report.checks
    assert report.source == counts and report.created == counts and report.backup.joinpath("app.db").is_file()
    d1 = get(stub, "d1")
    assert (d1.title, d1.pages, d1.chunks, d1.original_sha256, d1.owner) == ("Ünïcode", 3, 5, "o1", "alice")
    assert json.loads(d1.keywords_json) == ["k1", "k2"] and not d1.HasField("error")
    assert not get(stub, "d2").HasField("pages") and get(stub, "d2").error == "bad"
    assert stub.RenameHistory(pb.CatalogDocumentRef(doc_id="d1")).renames[0].new_title == "b"
    assert stub.LastEdit(pb.CatalogDocumentRef(doc_id="d1")).edit.id == 4
    u = stub.LastUndone(pb.CatalogDocumentRef(doc_id="d1")).undone
    assert (u.id, u.redo, u.ext, u.undo_order) == (3, "r.pdf", ".md", 5)
    b = get_batch(stub, "b1")
    assert [(t.id, t.status) for t in b.tasks] == [(9, "done"), (10, "queued")]
    assert json.loads(b.tasks[0].payload_json) == {"op": 1} and json.loads(b.tasks[0].checkpoint_json) == {"doc_id": "d1"}
    assert json.loads(b.tasks[0].actions_json) == [{"a": 1}] and not b.tasks[1].HasField("payload_json")
    s = stub.LoadLibrarySession(pb.LibrarySessionRef(session_id="chat1"))
    assert (s.owner, s.turn, json.loads(s.guard_json)) == ("alice", 2, {"g": 1})
    assert stub.ClaimNextTask(EMPTY).task.id == 10   # the queue carries on after the move
    assert source.read_bytes() == before            # the source is untouched
    again = migrate.migrate(source, settings.document_service_addr, backup_dest=None)
    assert again.ok and sum(again.created.values()) == 0


def test_migration_of_an_empty_or_missing_database(tmp_path):
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    report = migrate.migrate(empty, settings.document_service_addr, backup_dest=None)
    assert report.ok and sum(report.source.values()) == 0
    with pytest.raises(migrate.MigrationError, match="no database"):
        migrate.migrate(tmp_path / "none.db", "localhost:1", backup_dest=None)
    source = tmp_path / "app.db"
    make_source(source)
    with pytest.raises(migrate.MigrationError, match="failed"):
        migrate.migrate(source, "localhost:1", backup_dest=None)
