"""The Document service (RAG half) through its real gRPC server, which the root conftest.py runs for the whole test
session in this process (its own Qdrant folder and document.db in the temporary data folder, Gemini and Voyage stubbed),
the orchestrator's client on top of it, and scripts/migrate_documents_rag.py."""
import hashlib
import importlib.util
import logging
import math
import sys
import time
import uuid
from pathlib import Path

import grpc
import pytest

from agentic_rag_proto import document_pb2 as pb
from agentic_rag_proto import document_pb2_grpc as pb_grpc
from backend.config.settings import settings
from backend.documents import rag
from services.common.log import REQUEST_ID_HEADER, get_request_id, request_id_scope
from services.common.tokens import tokenize
from services.document import embedding_cache, ingestion, rerank

ROOT = Path(__file__).resolve().parents[3]


def _load_migrate():
    spec = importlib.util.spec_from_file_location("migrate_documents_rag", ROOT / "scripts" / "migrate_documents_rag.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module   # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


migrate = _load_migrate()


def fake_vector(text: str) -> list[float]:
    v = [0.0] * settings.embedding_dim
    for token in tokenize(text):
        v[int(hashlib.md5(token.encode()).hexdigest(), 16) % len(v)] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def fake_rerank(query, hits, priority=None):
    words = set(tokenize(query))
    for h in hits:
        found = len(words & set(tokenize(h["content"]))) / (len(words) or 1)
        h["relevance"], h["scorer"] = round(0.3 + 0.7 * found, 4), "voyage"
    return sorted(hits, key=lambda h: h["relevance"], reverse=True)


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    """No Gemini or Voyage: bag-of-words vectors for passages and questions; the reranker is off unless a test turns it on."""
    monkeypatch.setattr(ingestion, "embed_paced", lambda texts, title: [fake_vector(t) for t in texts])
    monkeypatch.setattr(embedding_cache, "embed_query", fake_vector)


@pytest.fixture
def stub():
    from backend.clients import get_clients
    return get_clients().document


@pytest.fixture
def ids():
    """Fresh user and document ids; their passages are removed afterwards."""
    made = []

    def new(prefix="u"):
        made.append(f"{prefix}-{uuid.uuid4().hex[:8]}")
        return made[-1]

    yield new


def write(tmp_path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def index(tmp_path, user, doc, text, name="doc.txt", title="Doc"):
    return rag.ingest(write(tmp_path, name, text), user_id=user, doc_id=doc, filename=title)


def search(user, query, **kw):
    return rag.search([query], user, **kw)


def code_of(call, request):
    with pytest.raises(grpc.RpcError) as e:
        call(request, timeout=20)
    return e.value.code()


GDPR = "The regulation protects personal data of people in the European Union. Fines apply to data controllers."
SVM = "A support vector machine finds the maximum margin hyperplane. Kernels map inputs to higher dimensions."


# ----------------------------------------------------------------------------------------------------- ingest
def test_ingest_streams_progress_then_the_final_counts(tmp_path, ids):
    user, doc = ids(), ids("d")
    seen = []
    result = rag.ingest(write(tmp_path, "gdpr.txt", GDPR), user_id=user, doc_id=doc, filename="GDPR",
                        on_progress=lambda *a: seen.append(a))
    assert result == {"pages": 1, "chunks": 1, "embedded": 1}
    stages = [s for s, _d, _t in seen]
    assert stages == ["reading", "chunked", "embedding", "stored"]
    assert ("embedding", 1, 1) in seen and ("stored", 1, 1) in seen
    assert rag.count_passages(user, doc) == 1


def test_a_file_larger_than_grpcs_message_limit_is_sent_in_chunks(tmp_path, ids):
    user, doc = ids(), ids("d")
    paragraph = ("Every passage of this large file repeats one sentence about the regulation. " * 12 + "\n\n")
    path = write(tmp_path, "big.txt", paragraph * (4_600_000 // len(paragraph)))
    assert path.stat().st_size > 4 * 1024 * 1024
    result = rag.ingest(path, user_id=user, doc_id=doc, filename="Big")
    assert result["chunks"] > 1000
    assert result["embedded"] < 5   # the same passage is embedded once
    assert rag.count_passages(user, doc) == result["chunks"]


def test_reindexing_keeps_the_vectors_of_unchanged_passages_and_drops_the_tail(tmp_path, ids):
    user, doc = ids(), ids("d")
    text = "\n\n".join(f"Paragraph number {i} talks about subject{i} " + "word " * 120 for i in range(6))
    first = index(tmp_path, user, doc, text)
    assert first["embedded"] == first["chunks"] >= 5
    again = index(tmp_path, user, doc, text)
    assert again["embedded"] == 0 and again["chunks"] == first["chunks"]
    shorter = index(tmp_path, user, doc, text.split("\n\n")[0])
    assert shorter["embedded"] == 0 and shorter["chunks"] < first["chunks"]
    assert rag.count_passages(user, doc) == shorter["chunks"]


def test_a_file_with_no_text_fails_with_a_reason_and_nothing_stored(tmp_path, ids):
    user, doc = ids(), ids("d")
    with pytest.raises(rag.IngestionError, match="No extractable text"):
        index(tmp_path, user, doc, "   \n\n  ")
    assert rag.count_passages(user, doc) == 0


def test_ingest_refuses_the_whole_file_when_it_needs_more_passages_than_allowed(stub, tmp_path, ids):
    user, doc = ids(), ids("d")
    path = write(tmp_path, "a.txt", GDPR)

    def messages(allowed):
        yield pb.IngestRequest(start=pb.IngestStart(user_id=user, doc_id=doc, filename="A", ext=".txt", max_passages=allowed))
        yield pb.IngestRequest(chunk=path.read_bytes())
    events = list(stub.Ingest(messages(0), timeout=30))
    failure = events[-1].failure
    assert (failure.kind, failure.needed) == ("budget", 1)
    assert rag.count_passages(user, doc) == 0


def test_the_orchestrator_checks_the_allowance_first_and_charges_what_was_embedded(tmp_path, ids, user_db, monkeypatch):
    from backend.library import embed_budget
    user, doc = ids(), ids("d")
    charged = []
    monkeypatch.setattr(embed_budget, "charge", lambda u, n: charged.append((u, n)))
    index(tmp_path, user, doc, GDPR)
    assert charged == [(user, 1)]
    monkeypatch.setattr(settings, "embed_per_user_day", 1)   # the account may embed one passage a day
    monkeypatch.setattr(embed_budget, "used", lambda user_id=None, day=None: 1)
    with pytest.raises(embed_budget.EmbedBudgetExceeded, match="needs 1 passages indexed"):
        index(tmp_path, user, ids("d"), SVM)
    assert charged == [(user, 1)]   # a refused file is not charged


def test_a_bad_request_is_invalid_argument(stub):
    assert code_of(stub.DeleteDocumentVectors, pb.DocumentRef(user_id="", doc_id="d")) == grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(stub.Search, pb.SearchRequest(queries=["x"], user_id=" ")) == grpc.StatusCode.INVALID_ARGUMENT


def test_a_stream_that_starts_with_bytes_is_invalid_argument(stub):
    with pytest.raises(grpc.RpcError) as e:
        list(stub.Ingest(iter([pb.IngestRequest(chunk=b"abc")]), timeout=10))
    assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT


# ----------------------------------------------------------------------------------------------------- search
def test_hybrid_search_finds_by_meaning_and_by_exact_words_and_filters_by_document(tmp_path, ids):
    user, d1, d2 = ids(), ids("d"), ids("d")
    index(tmp_path, user, d1, GDPR, "gdpr.txt", "GDPR")
    index(tmp_path, user, d2, SVM, "svm.txt", "SVM")
    found = search(user, "data controllers fines")
    top = found["passages"][0]
    assert top["doc_id"] == d1 and top["source"] == "GDPR" and top["bm25_rank"] is not None and top["dense_rank"] is not None
    assert found["reranked"] is False and found["zone"] == ""   # no reranker here: the caller must judge relevance
    only = search(user, "maximum margin hyperplane", doc_id=d2)
    assert {h["doc_id"] for h in only["passages"]} == {d2}
    assert not any(h["doc_id"] == d1 for h in search(user, "regulation", doc_id=d2)["passages"])


def test_search_never_returns_another_users_passages(tmp_path, ids):
    alice, bob = ids(), ids()
    index(tmp_path, alice, ids("d"), GDPR)
    assert search(bob, "data controllers")["passages"] == []
    assert search(alice, "data controllers")["passages"]


def test_with_a_reranker_the_passages_come_back_scored_with_the_diverse_ones_selected(tmp_path, ids, monkeypatch):
    monkeypatch.setattr(rerank, "score", fake_rerank)
    user = ids()
    index(tmp_path, user, ids("d"), GDPR, "a.txt")
    index(tmp_path, user, ids("d"), SVM, "b.txt")
    found = search(user, "maximum margin hyperplane", rerank_query="maximum margin hyperplane", mmr=True, min_score=0.0)
    assert found["reranked"] is True and found["zone"] == "high"
    first = found["passages"][0]
    assert first["relevance"] > 0.9 and first["scorer"] == "voyage" and first["selected"] == 1
    assert found["best_relevance"] == first["relevance"]
    unrelated = search(user, "zebra", rerank_query="zebra", mmr=True, min_score=0.0)
    assert unrelated["zone"] == "low" and not any(h.get("selected") for h in unrelated["passages"])


def test_reranked_is_false_when_voyage_is_unavailable_and_the_orchestrator_falls_back_to_the_llm_check(tmp_path, ids,
                                                                                                   monkeypatch):
    from backend.graph import nodes
    monkeypatch.setattr(settings, "voyage_api_key", "dummy")     # the reranker is "on" but Voyage fails (conftest's stub raises)
    user = ids()
    index(tmp_path, user, ids("d"), GDPR)
    found = search(user, "data controllers", rerank_query="data controllers", mmr=True, min_score=0.0)
    assert found["reranked"] is False and found["passages"] and "relevance" not in found["passages"][0]

    graded = []
    monkeypatch.setattr(nodes, "grade_documents", lambda q, docs: (graded.append(len(docs)) or docs, []))
    update = nodes.grade_node({"standalone_query": "data controllers", "documents": found["passages"]})
    assert graded == [1] and update["match_zone"] == "" and len(update["documents"]) == 1


def test_rerank_scores_arbitrary_texts_or_says_it_could_not(monkeypatch):
    assert rag.rerank_texts("q", ["a", "b"]) is None   # no Voyage key in tests
    monkeypatch.setattr(rerank, "score", fake_rerank)
    monkeypatch.setattr(settings, "voyage_api_key", "dummy")
    scores = rag.rerank_texts("margin hyperplane", ["unrelated words", "the margin hyperplane"], priority="find")
    assert scores[1] > scores[0]


# --------------------------------------------------------------------------------------------- delete, fields
def test_delete_removes_one_documents_passages_for_that_user_only(tmp_path, ids):
    alice, bob, doc = ids(), ids(), ids("d")
    index(tmp_path, alice, doc, GDPR)
    index(tmp_path, bob, doc, GDPR)     # the same document id under another account
    rag.delete_document_vectors(alice, doc)
    assert rag.count_passages(alice, doc) == 0 and rag.count_passages(bob, doc) == 1


def test_set_chunk_fields_changes_the_metadata_without_embedding(tmp_path, ids, monkeypatch):
    user, doc = ids(), ids("d")
    index(tmp_path, user, doc, GDPR)
    monkeypatch.setattr(ingestion, "embed_paced", lambda *a: pytest.fail("nothing is embedded"))
    rag.set_chunk_fields(user, doc, {"title": "New title", "file_name": "new.txt"})
    assert search(user, "data controllers")["passages"][0]["doc_id"] == doc
    from services.document import vectorstore
    point = vectorstore.get_client().scroll(settings.collection_name, scroll_filter=vectorstore.user_filter(user, doc),
                                            limit=1, with_payload=True)[0][0]
    assert point.payload["metadata"]["title"] == "New title" and point.payload["metadata"]["file_name"] == "new.txt"


def test_dense_vectors_of_stored_passages(tmp_path, ids):
    user = ids()
    index(tmp_path, user, ids("d"), GDPR)
    hit = search(user, "data controllers")["passages"][0]
    vector, missing = rag.dense_vectors([hit["id"], str(uuid.uuid4())])
    assert len(vector) == settings.embedding_dim and missing is None


def test_read_pages_returns_the_text_a_file_is_indexed_from(tmp_path):
    pages, count = rag.read_pages(write(tmp_path, "r.txt", "Some  text\n\n\n\nhere"))
    assert count == 1 and pages == [{"page": None, "text": "Some text\n\nhere", "ocr": False}]


def test_the_key_pool_state_is_served_for_the_admin_page():
    states = rag.key_states()
    assert [s["index"] for s in states] == list(range(len(states)))
    assert all(set(s) == {"index", "out_for_day", "rejected", "shares_quota", "cooldown_seconds"} for s in states)


# ----------------------------------------------------------------------------------------------------- request id
def test_the_request_id_reaches_the_services_log_lines(stub, tmp_path, ids):
    seen = []

    class Capture(logging.Handler):
        def emit(self, record):
            seen.append((record.getMessage(), get_request_id()))

    handler, root = Capture(level=logging.DEBUG), logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        with request_id_scope("orch-doc-9"):
            index(tmp_path, ids(), ids("d"), GDPR)
            search(ids(), "anything")
        stub.CountPassages(pb.CountPassagesRequest(), timeout=5, metadata=((REQUEST_ID_HEADER, "trace-doc-1"),))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not any("CountPassages ->" in m for m, _ in seen):
            time.sleep(0.05)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    assert [rid for m, rid in seen if "DocumentService/Ingest ->" in m] == ["orch-doc-9"]
    assert [rid for m, rid in seen if "DocumentService/Search ->" in m] == ["orch-doc-9"]
    assert [rid for m, rid in seen if "CountPassages ->" in m] == ["trace-doc-1"]


# ---------------------------------------------------------------------------------------------------- migration
def make_source(tmp_path, points: int = 3):
    """An orchestrator-style embedded Qdrant folder with hybrid points, and its two cache files."""
    import json
    import sqlite3
    from qdrant_client import QdrantClient, models
    from services.document import sparse, vectorstore
    folder = tmp_path / "old" / "qdrant_data"
    client = QdrantClient(path=str(folder))
    client.create_collection(settings.collection_name,
                             vectors_config={vectorstore.DENSE: models.VectorParams(size=settings.embedding_dim,
                                                                                   distance=models.Distance.COSINE)},
                             sparse_vectors_config={vectorstore.SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)})
    user = f"mig-{uuid.uuid4().hex[:8]}"
    texts = [f"migrated passage {i} about quokkas{i} and wombats" for i in range(points)]
    client.upsert(settings.collection_name, wait=True, points=[models.PointStruct(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{user}:{i}")),
        vector={vectorstore.DENSE: fake_vector(t), vectorstore.SPARSE: sparse.document_vector(t)},
        payload={"page_content": t, "metadata": {"user_id": user, "doc_id": "migdoc", "source": "Old", "page": None,
                                                 "chunk_index": i}}) for i, t in enumerate(texts)])
    client.close()
    data = tmp_path / "old"
    with sqlite3.connect(data / "ocr_cache.db") as c:
        c.execute("CREATE TABLE page_text (key TEXT PRIMARY KEY, text TEXT NOT NULL)")
        c.execute("INSERT INTO page_text VALUES (?, ?)", (f"page-{user}", "scanned words"))
    with sqlite3.connect(data / "embedding_cache.db") as c:
        c.execute("CREATE TABLE query_embeddings (key TEXT PRIMARY KEY, vector TEXT NOT NULL)")
        c.execute("INSERT INTO query_embeddings VALUES (?, ?)", (f"q-{user}", json.dumps(fake_vector("old question"))))
    return folder, data, user, texts


def test_migration_copies_passages_and_caches_verifies_and_can_be_repeated(tmp_path):
    from backend.clients import get_clients
    qdrant, data, user, texts = make_source(tmp_path)
    address = get_clients().addresses["document"]
    before = rag.count_passages()
    report = migrate.migrate(qdrant, data, address, backup_dest=tmp_path / "backups")
    assert report.ok, report.checks
    assert report.created == {"passages": 3, "caches": 2} and (report.passages, report.page_texts) == (3, 1)
    assert rag.count_passages() == before + 3
    assert (report.backup / "qdrant_data").is_dir() and (report.backup / "ocr_cache.db").is_file()
    again = migrate.migrate(qdrant, data, address, backup_dest=None)
    assert again.ok and again.created == {"passages": 0, "caches": 0}
    assert rag.count_passages() == before + 3
    found = search(user, "quokkas1 wombats")["passages"]     # searchable: dense and keyword vectors both arrived
    assert found[0]["content"] == texts[1] and found[0]["bm25_rank"] is not None and found[0]["dense_rank"] is not None
    from services.document import embedding_cache as cache
    assert cache.lookup(f"q-{user}") is not None
    assert qdrant.is_dir()   # the source is untouched


def test_migration_refuses_when_there_is_nothing_to_copy(tmp_path):
    with pytest.raises(migrate.MigrationError, match="nothing to copy"):
        migrate.migrate(tmp_path / "none", tmp_path, "localhost:1", backup_dest=None)
