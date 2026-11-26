"""The Conversation service through its real gRPC server (in this process, its own database in a temporary folder),
the orchestrator's wrappers over it, and scripts/migrate_conversations.py."""
import importlib.util
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

import grpc
import pytest

from agentic_rag_proto import conversation_pb2 as pb
from agentic_rag_proto import conversation_pb2_grpc as pb_grpc
from services.common.inprocess import RunningService
from services.common.log import REQUEST_ID_HEADER, get_request_id, request_id_scope
from services.conversation import db as store
from services.conversation.server import build_service

ROOT = Path(__file__).resolve().parents[3]


def _load_migrate():
    spec = importlib.util.spec_from_file_location("migrate_conversations", ROOT / "scripts" / "migrate_conversations.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module   # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


migrate = _load_migrate()


@pytest.fixture
def running(tmp_path):
    service = RunningService(lambda: build_service(store.open_db(tmp_path / "conversation.db"))).start()
    yield service
    service.stop()


@pytest.fixture
def stub(running):
    with grpc.insecure_channel(running.address) as channel:
        yield pb_grpc.ConversationServiceStub(channel)


def new_chat(stub, user="u1", title="what is bm25?"):
    return stub.CreateConversation(pb.CreateConversationRequest(user_id=user, title=title), timeout=5)


def say(stub, conv_id, role="user", content="hi", tool=None, meta=None):
    request = pb.AddMessageRequest(conversation_id=conv_id, role=role, content=content, meta_json=json.dumps(meta or {}))
    if tool is not None:
        request.tool = tool
    return stub.AddMessage(request, timeout=5)


def code_of(call, *args):
    with pytest.raises(grpc.RpcError) as e:
        call(*args, timeout=5)
    return e.value.code()


def test_create_get_list_and_the_title_rules(stub):
    a = new_chat(stub, title="  " + "x" * 80)
    assert a.title == "x" * 60 and a.user_id == "u1" and a.created_at == a.updated_at
    assert new_chat(stub, title="   ").title == "New chat"
    b = new_chat(stub, title="second")
    got = stub.GetConversation(pb.ConversationRef(user_id="u1", conversation_id=a.id), timeout=5)
    assert got == a
    ids = [c.id for c in stub.ListConversations(pb.ListConversationsRequest(user_id="u1"), timeout=5).conversations]
    assert ids[0] == b.id and set(ids) >= {a.id, b.id}
    say(stub, a.id)   # activity moves a chat to the top
    ids = [c.id for c in stub.ListConversations(pb.ListConversationsRequest(user_id="u1"), timeout=5).conversations]
    assert ids[0] == a.id


def test_rename_keeps_updated_at_and_trims(stub):
    a = new_chat(stub)
    renamed = stub.RenameConversation(
        pb.RenameConversationRequest(user_id="u1", conversation_id=a.id, title="  BM25 notes  "), timeout=5)
    assert renamed.title == "BM25 notes" and renamed.updated_at == a.updated_at


def test_another_users_conversation_is_not_found_everywhere(stub):
    mine = new_chat(stub, user="alice", title="private")
    ref = pb.ConversationRef(user_id="mallory", conversation_id=mine.id)
    assert code_of(stub.GetConversation, ref) == grpc.StatusCode.NOT_FOUND
    assert code_of(stub.RenameConversation, pb.RenameConversationRequest(
        user_id="mallory", conversation_id=mine.id, title="mine")) == grpc.StatusCode.NOT_FOUND
    assert code_of(stub.DeleteConversation, ref) == grpc.StatusCode.NOT_FOUND
    assert code_of(stub.GetConversation, pb.ConversationRef(user_id="alice", conversation_id="nope")) \
        == grpc.StatusCode.NOT_FOUND
    assert not stub.ListConversations(pb.ListConversationsRequest(user_id="mallory"), timeout=5).conversations
    still = stub.GetConversation(pb.ConversationRef(user_id="alice", conversation_id=mine.id), timeout=5)
    assert still.title == "private"
    assert stub.GetStats(pb.GetStatsRequest(user_id="mallory"), timeout=5).conversations == 0


def test_delete_takes_the_messages_with_it(stub, running, tmp_path):
    a = new_chat(stub)
    say(stub, a.id)
    stub.DeleteConversation(pb.ConversationRef(user_id="u1", conversation_id=a.id), timeout=5)
    assert code_of(stub.GetConversation, pb.ConversationRef(user_id="u1", conversation_id=a.id)) \
        == grpc.StatusCode.NOT_FOUND
    assert not stub.ListMessages(pb.ListMessagesRequest(conversation_id=a.id), timeout=5).messages
    assert code_of(stub.DeleteConversation, pb.ConversationRef(user_id="u1", conversation_id=a.id)) \
        == grpc.StatusCode.NOT_FOUND
    with sqlite3.connect(tmp_path / "conversation.db") as con:
        assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_meta_round_trips_and_stat_columns_are_filled(stub):
    a = new_chat(stub)
    meta = {"sources": [{"title": "Ünïcode ✓", "page": 3}], "latency_ms": 1200, "rewritten_queries": ["q2"],
            "evaluation": {"sufficient": True}, "nested": {"a": [1, 2, {"b": None}]}}
    sent = say(stub, a.id, "assistant", "answer", "rag", meta)
    assert json.loads(sent.meta_json) == meta
    assert (sent.latency_ms, sent.rewritten, sent.sufficient, sent.tool) == (1200, True, True, "rag")
    plain = say(stub, a.id, "user", "question")
    assert not plain.HasField("tool") and not plain.HasField("latency_ms") and not plain.HasField("sufficient")
    assert plain.meta_json == "{}"
    got = stub.ListMessages(pb.ListMessagesRequest(conversation_id=a.id), timeout=5).messages
    assert [m.content for m in got] == ["answer", "question"]
    assert json.loads(got[0].meta_json) == meta


def test_list_messages_limit_keeps_the_last_ones_oldest_first(stub):
    a = new_chat(stub)
    for i in range(5):
        say(stub, a.id, "user", f"m{i}")
    last = stub.ListMessages(pb.ListMessagesRequest(conversation_id=a.id, limit=3), timeout=5).messages
    assert [m.content for m in last] == ["m2", "m3", "m4"]
    assert len(stub.ListMessages(pb.ListMessagesRequest(conversation_id=a.id), timeout=5).messages) == 5


def test_list_tool_messages(stub):
    a = new_chat(stub)
    say(stub, a.id, "assistant", "x", "manage_documents", {"actions": [1]})
    say(stub, a.id, "assistant", "y", "rag", {})
    say(stub, a.id, "assistant", "z", "manage_documents", {"actions": [2]})
    got = stub.ListToolMessages(pb.ListToolMessagesRequest(conversation_id=a.id, tool="manage_documents"),
                                timeout=5).messages
    assert [json.loads(m.meta_json) for m in got] == [{"actions": [1]}, {"actions": [2]}]


def test_bad_input_and_missing_conversation(stub):
    a = new_chat(stub)
    assert code_of(stub.AddMessage, pb.AddMessageRequest(
        conversation_id="ghost", role="user", content="x")) == grpc.StatusCode.NOT_FOUND
    assert code_of(stub.AddMessage, pb.AddMessageRequest(
        conversation_id=a.id, role="user", content="x", meta_json="not json")) == grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(stub.AddMessage, pb.AddMessageRequest(
        conversation_id=a.id, role="user", content="x", meta_json="[1]")) == grpc.StatusCode.INVALID_ARGUMENT


def test_stats_count_only_the_users_answers(stub):
    a, other = new_chat(stub, user="s1"), new_chat(stub, user="s2")
    say(stub, a.id, "user", "q")
    say(stub, a.id, "assistant", "a1", "rag", {"latency_ms": 1000, "rewritten_queries": ["q"], "evaluation": {"sufficient": True}})
    say(stub, a.id, "assistant", "a2", "rag", {"latency_ms": 3000, "evaluation": {"sufficient": False}})
    say(stub, a.id, "assistant", "a3", None, {})
    say(stub, other.id, "assistant", "b", "rag", {"latency_ms": 9})
    s = stub.GetStats(pb.GetStatsRequest(user_id="s1"), timeout=5)
    assert (s.conversations, s.queries, dict(s.by_tool), s.queries_rewritten, s.answers_evaluated,
            s.answers_sufficient, s.avg_latency_ms) == (1, 3, {"rag": 2, "unknown": 1}, 1, 2, 1, 2000)


def test_the_request_id_reaches_the_services_log_lines(stub):
    seen = []

    class Capture(logging.Handler):
        def emit(self, record):   # runs in the logging call's own context: the id the service had at that moment
            seen.append((record.name, record.getMessage(), get_request_id()))

    handler, root = Capture(level=logging.DEBUG), logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        stub.CreateConversation(pb.CreateConversationRequest(user_id="u1", title="traced"), timeout=5,
                                metadata=((REQUEST_ID_HEADER, "trace-abc123"),))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not any("CreateConversation ->" in m for _, m, _ in seen):
            time.sleep(0.05)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    lines = {m.split()[0].rsplit("/", 1)[-1]: rid for _, m, rid in seen}
    assert lines.get("conversation_created") == "trace-abc123"   # the audit line
    assert lines.get("CreateConversation") == "trace-abc123"     # the access line


def test_deleting_is_audited(stub):
    seen = []

    class Capture(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    handler = Capture(level=logging.INFO)
    audit_log = logging.getLogger("audit")
    old_level = audit_log.level
    audit_log.addHandler(handler)
    audit_log.setLevel(logging.INFO)
    try:
        a = new_chat(stub)
        stub.DeleteConversation(pb.ConversationRef(user_id="u1", conversation_id=a.id), timeout=5)
    finally:
        audit_log.removeHandler(handler)
        audit_log.setLevel(old_level)
    assert any(m.startswith("conversation_created") and a.id in m for m in seen)
    assert any(m.startswith("conversation_deleted") and a.id in m for m in seen)


# ---------------------------------------------------------------- the orchestrator's side (the session's service)
def test_orchestrator_functions_keep_their_shapes_and_none_for_not_found():
    from backend.models import database as db
    conv = db.create_conversation("shape-user", "hello there")
    assert set(conv) == {"id", "user_id", "title", "created_at", "updated_at"}
    assert db.get_conversation("shape-user", conv["id"]) == conv
    assert db.get_conversation("someone-else", conv["id"]) is None
    assert set(db.list_conversations("shape-user")[0]) == {"id", "title", "created_at", "updated_at"}
    msg = db.add_message(conv["id"], "assistant", "ans", "rag", {"latency_ms": 5})
    assert set(msg) == {"id", "conversation_id", "role", "content", "tool", "meta", "created_at"} and msg["meta"] == {"latency_ms": 5}
    row = db.list_messages(conv["id"])[0]
    assert set(row) == {"id", "conversation_id", "role", "content", "tool", "meta", "created_at", "latency_ms",
                        "rewritten", "sufficient"}
    assert (row["meta"], row["latency_ms"], row["rewritten"], row["sufficient"]) == ({"latency_ms": 5}, 5, 0, None)
    db.rename_conversation("someone-else", conv["id"], "hijacked")   # no-ops, as before
    db.delete_conversation("someone-else", conv["id"])
    assert db.get_conversation("shape-user", conv["id"])["title"] == "hello there"
    with pytest.raises(LookupError):
        db.add_message("ghost", "user", "x")
    db.delete_conversation("shape-user", conv["id"])
    assert db.get_conversation("shape-user", conv["id"]) is None


def test_orchestrator_calls_carry_the_request_id_to_the_service():
    from backend.models import database as db
    seen = []

    class Capture(logging.Handler):
        def emit(self, record):
            seen.append((record.getMessage(), get_request_id()))

    handler, root = Capture(level=logging.INFO), logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        with request_id_scope("orch-req-77"):
            conv = db.create_conversation("trace-user", "t")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not any(m.startswith("conversation_created") for m, _ in seen):
            time.sleep(0.05)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    assert ("conversation_created" in " ".join(m for m, _ in seen))
    assert all(rid == "orch-req-77" for m, rid in seen if m.startswith("conversation_created") and conv["id"] in m)
    db.delete_conversation("trace-user", conv["id"])


def test_user_stats_split_keeps_the_documents_part_local():
    from backend.models import database as db
    db.init_db()   # the documents table is the catalog's: this test must not rely on another test having made it
    uid = db.create_user("stats_split", "x")["id"]
    conv = db.create_conversation(uid, "c")
    db.add_message(conv["id"], "assistant", "a", "rag", {"latency_ms": 10})
    stats = db.user_stats(uid)
    assert set(stats) == {"documents", "chunks", "conversations", "queries", "by_tool", "queries_rewritten",
                          "answers_evaluated", "answers_sufficient", "avg_latency_ms"}
    assert (stats["documents"], stats["chunks"], stats["conversations"], stats["queries"], stats["by_tool"],
            stats["avg_latency_ms"]) == (0, 0, 1, 1, {"rag": 1}, 10)


# ---------------------------------------------------------------- scripts/migrate_conversations.py
def make_source(path: Path, with_stat_columns: bool) -> Path:
    con = sqlite3.connect(path)
    extra = ", latency_ms INTEGER, rewritten INTEGER NOT NULL DEFAULT 0, sufficient INTEGER" if with_stat_columns else ""
    con.executescript(f"""
        CREATE TABLE conversations (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL,
                                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
                               content TEXT NOT NULL, tool TEXT, meta TEXT, created_at TEXT NOT NULL{extra});
        INSERT INTO conversations VALUES ('c1', 'u1', 'first', '2026-01-01T00:00:00', '2026-01-01T00:00:05');
        INSERT INTO conversations VALUES ('c2', 'u2', 'second', '2026-01-02T00:00:00', '2026-01-02T00:00:00');
    """)
    answer_meta = json.dumps({"latency_ms": 800, "rewritten_queries": ["q"], "evaluation": {"sufficient": True}})
    rows = [("m1", "c1", "user", "hello", None, None, "2026-01-01T00:00:01"),
            ("m2", "c1", "assistant", "hi there", "rag", answer_meta, "2026-01-01T00:00:02"),
            ("m3", "c1", "assistant", "again", "direct", "{}", "2026-01-01T00:00:03"),
            ("orphan", "gone", "user", "lost", None, None, "2026-01-01T00:00:04")]
    for r in rows:
        if with_stat_columns:
            stats = (800, 1, 1) if r[0] == "m2" else (None, 0, None)
            con.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (*r, *stats))
        else:
            con.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?)", r)
    con.commit()
    con.close()
    return path


@pytest.mark.parametrize("with_stat_columns", [True, False])
def test_migration_copies_verifies_and_is_safe_to_repeat(running, stub, tmp_path, with_stat_columns):
    source = make_source(tmp_path / "app.db", with_stat_columns)
    report = migrate.migrate(source, running.address, tmp_path / "backups")
    assert report.ok, report.checks
    assert (report.conversations, report.messages, report.skipped_messages) == (2, 4, 1)
    assert (report.conversations_created, report.messages_created) == (2, 3)
    assert (report.backup / "app.db").is_file()
    got = stub.ListMessages(pb.ListMessagesRequest(conversation_id="c1"), timeout=5).messages
    assert [m.id for m in got] == ["m1", "m2", "m3"] and got[1].created_at == "2026-01-01T00:00:02"
    assert (got[1].latency_ms, got[1].rewritten, got[1].sufficient) == (800, True, True)   # also from meta alone
    assert not got[0].HasField("tool") and got[0].meta_json == "{}"
    conv = stub.GetConversation(pb.ConversationRef(user_id="u1", conversation_id="c1"), timeout=5)
    assert (conv.title, conv.created_at, conv.updated_at) == ("first", "2026-01-01T00:00:00", "2026-01-01T00:00:05")
    assert stub.GetStats(pb.GetStatsRequest(user_id="u1"), timeout=5).avg_latency_ms == 800

    again = migrate.migrate(source, running.address, tmp_path / "backups2")   # a second run copies nothing
    assert again.ok and (again.conversations_created, again.messages_created) == (0, 0)
    assert len(stub.ListMessages(pb.ListMessagesRequest(conversation_id="c1"), timeout=5).messages) == 3

    with sqlite3.connect(source) as con:   # the source is never touched
        assert con.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 4
    assert any("conversations" in line for line in report.left_behind) and any("messages" in line for line in report.left_behind)


def test_migration_resumes_after_a_partial_copy(running, stub, tmp_path):
    source = make_source(tmp_path / "app.db", True)
    stub.ImportConversation(pb.ImportConversationRequest(
        conversation=pb.Conversation(id="c1", user_id="u1", title="first", created_at="2026-01-01T00:00:00",
                                     updated_at="2026-01-01T00:00:05"),
        messages=[pb.Message(id="m1", conversation_id="c1", role="user", content="hello", meta_json="{}",
                             created_at="2026-01-01T00:00:01")]), timeout=5)
    report = migrate.migrate(source, running.address, None)
    assert report.ok and (report.conversations_created, report.messages_created) == (1, 2)
    assert len(stub.ListMessages(pb.ListMessagesRequest(conversation_id="c1"), timeout=5).messages) == 3


def test_migration_refuses_what_it_cannot_use(running, tmp_path):
    with pytest.raises(migrate.MigrationError, match="no database"):
        migrate.migrate(tmp_path / "missing.db", running.address, None)
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    with pytest.raises(migrate.MigrationError, match="nothing to migrate"):
        migrate.migrate(empty, running.address, None)
    source = make_source(tmp_path / "app.db", True)
    with pytest.raises(migrate.MigrationError, match="Conversation service"):
        migrate.migrate(source, "localhost:1", None)
