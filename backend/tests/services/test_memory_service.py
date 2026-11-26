"""The Memory service through its real gRPC server (in this process, its own database and Qdrant folder in a temporary
folder, Voyage stubbed by the root conftest.py), the orchestrator's functions on top of it, and scripts/migrate_memory.py."""
import importlib.util
import logging
import sqlite3
import sys
import time
from pathlib import Path

import grpc
import pytest

from agentic_rag_proto import memory_pb2 as pb
from agentic_rag_proto import memory_pb2_grpc as pb_grpc
from backend.config.settings import settings
from services.common.inprocess import RunningService
from services.common.log import REQUEST_ID_HEADER, get_request_id, request_id_scope
from services.memory import db as store
from services.memory.server import build_service
from services.memory.vectors import EpisodeStore

ROOT = Path(__file__).resolve().parents[3]
DIMS = settings.voyage_memory_dimensions


def _load_migrate():
    spec = importlib.util.spec_from_file_location("migrate_memory", ROOT / "scripts" / "migrate_memory.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module   # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


migrate = _load_migrate()


@pytest.fixture
def running(tmp_path):
    service = RunningService(lambda: build_service(store.open_db(tmp_path / "memory.db"),
                                                   EpisodeStore(tmp_path / "qdrant"))).start()
    yield service
    service.stop()


@pytest.fixture
def stub(running):
    with grpc.insecure_channel(running.address) as channel:
        yield pb_grpc.MemoryServiceStub(channel)


def facts(stub, user):
    return {g.memory_key: list(g.values) for g in stub.ListFacts(pb.ListFactsRequest(user_id=user), timeout=20).groups}


def add(stub, user, key, value):
    stub.AddFact(pb.FactRequest(user_id=user, memory_key=key, value=value), timeout=20)


def code_of(call, request):
    with pytest.raises(grpc.RpcError) as e:
        call(request, timeout=20)
    return e.value.code()


def episode(stub, user, text, kind="episodic", conversation=None):
    request = pb.StoreEpisodeRequest(user_id=user, text=text, kind=kind)
    if conversation:
        request.conversation_id = conversation
    return stub.StoreEpisode(request, timeout=20).id


def search(stub, user, query, limit=5):
    return stub.SearchEpisodes(pb.SearchEpisodesRequest(user_id=user, query=query, limit=limit), timeout=20).episodes


# ------------------------------------------------------------------------------------------------------------ facts
def test_facts_add_set_delete_and_multi_value_keys(stub):
    add(stub, "u1", "hobbies", "trekking")
    add(stub, "u1", "hobbies", "Futsal")
    add(stub, "u1", "hobbies", "futsal")   # a duplicate, ignoring case
    stub.SetFact(pb.FactRequest(user_id="u1", memory_key="location", value="Pokhara"), timeout=20)
    stub.SetFact(pb.FactRequest(user_id="u1", memory_key="location", value="Kathmandu"), timeout=20)
    assert facts(stub, "u1") == {"hobbies": ["trekking", "Futsal"], "location": ["Kathmandu"]}
    one = stub.DeleteFact(pb.DeleteFactRequest(user_id="u1", memory_key="hobbies", value="FUTSAL"), timeout=20)
    whole = stub.DeleteFact(pb.DeleteFactRequest(user_id="u1", memory_key="location"), timeout=20)
    assert (one.deleted, whole.deleted) == (1, 1)
    assert facts(stub, "u1") == {"hobbies": ["trekking"]}


def test_most_recently_updated_key_comes_first(stub):
    add(stub, "u1", "a", "1")
    time.sleep(0.01)
    add(stub, "u1", "b", "2")
    time.sleep(0.01)
    add(stub, "u1", "a", "1")   # refreshed
    assert list(facts(stub, "u1")) == ["a", "b"]


def test_one_persons_facts_are_never_another_persons(stub):
    add(stub, "u1", "preferred_name", "Ash")
    add(stub, "u2", "preferred_name", "Bo")
    assert facts(stub, "u1") == {"preferred_name": ["Ash"]} and facts(stub, "u2") == {"preferred_name": ["Bo"]}
    stub.DeleteFact(pb.DeleteFactRequest(user_id="u2", memory_key="preferred_name"), timeout=20)
    assert facts(stub, "u1") == {"preferred_name": ["Ash"]} and facts(stub, "u2") == {}


def test_empty_input_is_invalid_argument(stub):
    assert code_of(stub.ListFacts, pb.ListFactsRequest(user_id="")) == grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(stub.AddFact, pb.FactRequest(user_id="u", memory_key="k", value=" ")) == grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(stub.StoreEpisode, pb.StoreEpisodeRequest(user_id="u", text="")) == grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(stub.SearchEpisodes, pb.SearchEpisodesRequest(user_id="", query="q")) == grpc.StatusCode.INVALID_ARGUMENT


# --------------------------------------------------------------------------------------------------------- episodes
def test_the_collection_is_created_when_the_service_starts(tmp_path):
    episodes = EpisodeStore(tmp_path / "q1")
    try:
        assert episodes.client.collection_exists(settings.voyage_memory_collection)
    finally:
        episodes.close()


def test_a_collection_of_another_vector_size_stops_the_start(tmp_path):
    from qdrant_client import QdrantClient, models
    client = QdrantClient(path=str(tmp_path / "q2"))
    client.create_collection(settings.voyage_memory_collection,
                             vectors_config=models.VectorParams(size=8, distance=models.Distance.COSINE))
    client.close()
    with pytest.raises(ValueError, match="vector size 8"):
        EpisodeStore(tmp_path / "q2")


def test_store_and_search_find_the_closest_episode_of_that_person(stub):
    episode(stub, "u1", "we planned a trekking route around Annapurna", conversation="c1")
    episode(stub, "u1", "the invoice for the printer was overdue", kind="summary", conversation="c2")
    episode(stub, "u2", "trekking route notes belonging to someone else")
    hits = search(stub, "u1", "trekking route Annapurna")
    assert [h.text for h in hits][0].startswith("we planned a trekking") and len(hits) == 2
    assert hits[0].score > hits[1].score and hits[0].conversation_id == "c1" and hits[0].kind == "episodic"
    assert hits[1].kind == "summary"
    assert all("someone else" not in h.text for h in hits)
    assert search(stub, "nobody", "trekking") == []
    assert search(stub, "u1", "trekking", limit=1)[0].text.startswith("we planned")


def test_storing_the_same_text_twice_keeps_one_episode(stub):
    first = episode(stub, "u1", "same words")
    second = episode(stub, "u1", "same words")
    other_user = episode(stub, "u2", "same words")
    assert first == second != other_user
    assert len(search(stub, "u1", "same words")) == 1
    assert stub.CountMemory(pb.CountMemoryRequest(), timeout=20).episodes == 2


def test_a_conversations_episodes_are_deleted_for_that_person_only(stub):
    episode(stub, "u1", "alpha beta", conversation="c1")
    episode(stub, "u1", "gamma delta", conversation="c2")
    episode(stub, "u2", "alpha beta other", conversation="c1")
    stub.DeleteConversationEpisodes(pb.DeleteConversationEpisodesRequest(user_id="u1", conversation_id="c1"), timeout=20)
    assert [h.text for h in search(stub, "u1", "alpha gamma")] == ["gamma delta"]
    assert len(search(stub, "u2", "alpha")) == 1


def test_a_score_threshold_drops_weak_matches(stub):
    episode(stub, "u1", "completely unrelated words here")
    request = pb.SearchEpisodesRequest(user_id="u1", query="zebra", limit=5, score_threshold=0.5)
    assert list(stub.SearchEpisodes(request, timeout=20).episodes) == []


def test_a_busy_voyage_limit_is_resource_exhausted(stub, monkeypatch):
    from backend.config import voyage
    one_slot = voyage.RateLimiter(1, 0)
    monkeypatch.setattr(voyage, "limiter", lambda: one_slot)
    monkeypatch.setattr(settings, "voyage_save_max_wait", 0.0)
    episode(stub, "u1", "takes the only slot")
    assert code_of(stub.StoreEpisode, pb.StoreEpisodeRequest(user_id="u1", text="no slot left")) \
        == grpc.StatusCode.RESOURCE_EXHAUSTED


# ---------------------------------------------------------------------------------------------------- request id
def test_the_request_id_reaches_the_services_log_lines(stub):
    seen = []

    class Capture(logging.Handler):
        def emit(self, record):
            seen.append((record.getMessage(), get_request_id()))

    handler, root = Capture(level=logging.DEBUG), logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        stub.ListFacts(pb.ListFactsRequest(user_id="u1"), timeout=5, metadata=((REQUEST_ID_HEADER, "trace-mem-1"),))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not any("ListFacts ->" in m for m, _ in seen):
            time.sleep(0.05)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    assert [rid for m, rid in seen if "ListFacts ->" in m] == ["trace-mem-1"]


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
        with request_id_scope("orch-mem-77"):
            db.list_memories("trace-user")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not any("ListFacts ->" in m for m, _ in seen):
            time.sleep(0.05)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    assert [rid for m, rid in seen if "ListFacts ->" in m] == ["orch-mem-77"]


# ----------------------------------------------------------------------- the orchestrator's functions on the service
def test_orchestrator_functions_keep_their_dict_shapes():
    from backend.memory import store as episodes
    from backend.models import database as db
    uid = db.create_user("shape_user", "x")["id"]
    db.add_fact(uid, "hobbies", "chess")
    db.add_fact(uid, "hobbies", "go")
    (group,) = db.list_memories(uid)
    assert group["memory_key"] == "hobbies" and group["values"] == ["chess", "go"]
    assert group["memory_value"] == "chess; go" and group["updated_at"]
    assert db.delete_fact(uid, "hobbies", "go") == 1 and db.delete_fact(uid, "hobbies") == 1

    point = episodes.save_memory(uid, "talked about the orchestrator memory split", conversation_id="conv-1")
    (hit,) = episodes.search_memories(uid, "orchestrator memory split")
    assert hit["id"] == point and hit["conversation_id"] == "conv-1" and hit["kind"] == "episodic"
    assert set(hit) == {"id", "text", "kind", "conversation_id", "updated_at", "score"}
    assert episodes.search_memories(uid, "  ") == []
    episodes.delete_conversation_memories(uid, "conv-1")
    assert episodes.search_memories(uid, "orchestrator memory split") == []


def test_the_orchestrator_no_longer_opens_the_memory_collection():
    from services.document import vectorstore
    names = {c.name for c in vectorstore.get_client().get_collections().collections}
    assert settings.voyage_memory_collection not in names


# --------------------------------------------------------------------------------------------------------- migration
def _vector(i):
    return [1.0 if n == i else 0.0 for n in range(DIMS)]


def make_source(tmp_path, with_old_memory=False):
    """An app.db with facts and a Qdrant folder with a memory collection, as the orchestrator left them."""
    from qdrant_client import QdrantClient, models
    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE user_facts (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, memory_key TEXT NOT NULL,
            value TEXT NOT NULL COLLATE NOCASE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(user_id, memory_key, value));
        CREATE TABLE migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
        INSERT INTO user_facts VALUES ('f1','u1','hobbies','trekking','2026-01-01','2026-01-02'),
                                      ('f2','u1','hobbies','futsal','2026-01-03','2026-01-04'),
                                      ('f3','u2','preferred_name','Bo','2026-01-05','2026-01-05');
    """)
    if with_old_memory:   # a database from before user_facts, never converted
        con.executescript("""
            CREATE TABLE user_memory (id TEXT PRIMARY KEY, user_id TEXT, memory_key TEXT, memory_value TEXT,
                created_at TEXT, updated_at TEXT);
            INSERT INTO user_memory VALUES ('m1','u3','pets','["cat","dog"]','t1','t2'), ('m2','u3','city','Pokhara','t1','t2');
        """)
    else:
        con.execute("INSERT INTO migrations VALUES ('user_facts_v1', 't')")
    con.commit()
    con.close()
    qdrant = tmp_path / "orch_qdrant"
    client = QdrantClient(path=str(qdrant))
    client.create_collection(settings.voyage_memory_collection,
                             vectors_config=models.VectorParams(size=DIMS, distance=models.Distance.COSINE))
    client.upsert(settings.voyage_memory_collection, points=[
        models.PointStruct(id="00000000-0000-0000-0000-000000000001", vector=_vector(1),
                           payload={"user_id": "u1", "text": "first episode", "kind": "episodic",
                                    "conversation_id": "c1", "updated_at": "2026-02-01"}),
        models.PointStruct(id="00000000-0000-0000-0000-000000000002", vector=_vector(2),
                           payload={"user_id": "u2", "text": "second episode", "kind": "summary",
                                    "conversation_id": None, "updated_at": "2026-02-02"})])
    client.close()
    return db, qdrant


def test_migration_copies_facts_and_episodes_with_their_vectors_and_is_safe_to_repeat(running, stub, tmp_path):
    db, qdrant = make_source(tmp_path)
    backups = tmp_path / "backups"
    report = migrate.migrate(db, qdrant, running.address, backup_dest=backups)
    assert report.ok, report.checks
    assert (report.facts, report.episodes, report.created) == (3, 2, {"facts": 3, "episodes": 2})
    assert (report.backup / "app.db").is_file() and (report.backup / "qdrant_data").is_dir()
    assert facts(stub, "u1") == {"hobbies": ["trekking", "futsal"]} and facts(stub, "u2") == {"preferred_name": ["Bo"]}
    assert stub.CountMemory(pb.CountMemoryRequest(), timeout=20).episodes == 2
    second = migrate.migrate(db, qdrant, running.address, backup_dest=None)
    assert second.ok and second.created == {"facts": 0, "episodes": 0}
    assert stub.CountMemory(pb.CountMemoryRequest(), timeout=20).facts == 3
    # the sources are untouched
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM user_facts").fetchone()[0] == 3


def test_migrated_episodes_keep_their_vector_payload_and_are_searchable(running, tmp_path):
    db, qdrant = make_source(tmp_path)
    migrate.migrate(db, qdrant, running.address, backup_dest=None)
    with grpc.insecure_channel(running.address) as channel:
        found = pb_grpc.MemoryServiceStub(channel).SearchEpisodes(
            pb.SearchEpisodesRequest(user_id="u1", query="first episode", limit=5), timeout=20).episodes
    assert [e.text for e in found] == ["first episode"] and found[0].conversation_id == "c1"
    assert found[0].updated_at == "2026-02-01"


def test_migration_converts_the_older_user_memory_table_once(running, stub, tmp_path):
    db, qdrant = make_source(tmp_path, with_old_memory=True)
    report = migrate.migrate(db, qdrant, running.address, backup_dest=None)
    assert report.ok and report.facts == 3 + 3
    assert facts(stub, "u3") == {"pets": ["cat", "dog"], "city": ["Pokhara"]}
    assert migrate.migrate(db, qdrant, running.address, backup_dest=None).created == {"facts": 0, "episodes": 0}


def test_migration_refuses_what_it_cannot_use(running, tmp_path):
    with pytest.raises(migrate.MigrationError, match="no database"):
        migrate.migrate(tmp_path / "missing.db", tmp_path, running.address, backup_dest=None)
    db, qdrant = make_source(tmp_path)
    with pytest.raises(migrate.MigrationError, match="failed"):
        migrate.migrate(db, qdrant, "localhost:1", backup_dest=None)


def test_a_backup_never_overwrites_one(tmp_path):
    db, qdrant = make_source(tmp_path)
    from datetime import datetime
    now = datetime(2026, 1, 2, 3, 4, 5)
    migrate.backup_source(db, qdrant, tmp_path / "b", now)
    with pytest.raises(migrate.MigrationError, match="exists already"):
        migrate.backup_source(db, qdrant, tmp_path / "b", now)
