"""The stage 3 merge script on two tiny source sets (no network: vectors are made up)."""
import hashlib
import json
import sqlite3

import pytest
from qdrant_client import QdrantClient, models

from backend.config.settings import settings
from backend.rag import sparse, vectorstore
from backend.scripts import merge_library

DAY7_DOCUMENTS = """CREATE TABLE documents (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, filename TEXT NOT NULL,
    file_hash TEXT NOT NULL, size_bytes INTEGER NOT NULL, pages INTEGER NOT NULL DEFAULT 0,
    chunks INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL,
    UNIQUE(user_id, file_hash))"""
T0, T1 = "2026-11-01T10:00:00+00:00", "2026-11-01T10:05:00+00:00"


def _store(path, points):
    client = QdrantClient(path=str(path))
    client.create_collection(
        "c", vectors_config={vectorstore.DENSE: models.VectorParams(size=settings.embedding_dim,
                                                                    distance=models.Distance.COSINE)},
        sparse_vectors_config={vectorstore.SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)})
    client.upsert("c", points=points, wait=True)
    client.close()


def _point(pid, user, doc, text):
    return models.PointStruct(id=pid, vector={vectorstore.DENSE: [0.1] * settings.embedding_dim,
                                              vectorstore.SPARSE: sparse.query_vector(text)},
                              payload={"page_content": text, "metadata": {"user_id": user, "doc_id": doc,
                                                                          "source": "x", "chunk_index": 0}})


@pytest.fixture
def sources(tmp_path, monkeypatch):
    day7 = tmp_path / "day7"
    (day7 / "backend" / "data").mkdir(parents=True)
    (day7 / "backend" / "documents" / "u1").mkdir(parents=True)
    (day7 / "benchmark" / "corpus" / "wikipedia").mkdir(parents=True)
    uploaded, corpus = b"Okapi BM25 ranking notes", b"Stemming reduces words to their stems"
    (day7 / "backend" / "documents" / "u1" / "d1.txt").write_bytes(uploaded)
    (day7 / "benchmark" / "corpus" / "wikipedia" / "stemming.md").write_bytes(corpus)
    sha = lambda b: hashlib.sha256(b).hexdigest()

    db7 = sqlite3.connect(day7 / "backend" / "data" / "app.db")
    db7.executescript(f"""
        CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                            password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
        {DAY7_DOCUMENTS};
        CREATE TABLE conversations (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                                    title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
                               content TEXT NOT NULL, tool TEXT, meta TEXT, created_at TEXT NOT NULL);
        INSERT INTO users VALUES ('u1', 'alice', '$2b$x', '{T0}');
        INSERT INTO conversations VALUES ('c1', 'u1', 'hi', '{T0}', '{T0}');""")
    db7.execute("INSERT INTO documents VALUES ('d1','u1','bm25 notes.txt',?,?,1,1,'indexed',NULL,?)",
                (sha(uploaded), len(uploaded), T0))
    db7.execute("INSERT INTO documents VALUES ('d2','u1','stemming.md',?,?,1,1,'indexed',NULL,?)",
                (sha(corpus), len(corpus), T0))
    db7.commit()
    db7.close()
    _store(day7 / "backend" / "vector_db" / "qdrant_data",
           [_point(1, "u1", "d1", "okapi bm25 ranking"), _point(2, "u1", "d2", "stemming words")])

    renamer = tmp_path / "renamer"
    (renamer / "library" / "u2").mkdir(parents=True)
    text = b"Measles fact sheet"
    (renamer / "library" / "u2" / "Measles Fact Sheet.txt").write_bytes(text)
    monkeypatch.setattr(settings, "database_path", str(renamer / "catalog.db"))
    from backend.library import catalog
    catalog.reset_connection()
    with catalog.tx() as c:
        c.executescript("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, "
                        "password_hash TEXT NOT NULL, created_at TEXT NOT NULL); "   # the renamer's own catalog had them
                        "CREATE TABLE IF NOT EXISTS auth_tokens (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
                        "created_at TEXT NOT NULL, expires_at TEXT NOT NULL);")
        c.execute("INSERT INTO users VALUES ('u2', 'bob', 'pbkdf2_sha256$1$aa$bb', ?)", (T0,))
        c.execute("INSERT INTO documents (id, title, original_name, file_name, ext, sha256, original_sha256, "
                  "size_bytes, pages, chunks, owner, status, created_at, updated_at) VALUES "
                  "('r1', 'Measles Fact Sheet', 'who.txt', 'u2/Measles Fact Sheet.txt', '.txt', ?, ?, ?, 1, 1, "
                  "'u2', 'indexed', ?, ?)", (sha(text), sha(text), len(text), T0, T0))
        c.execute("INSERT INTO sessions (id, llm_messages, display, guard, owner, created_at, updated_at) "
                  "VALUES ('s1', '[]', ?, '{}', 'u2', ?, ?)",
                  (json.dumps([{"role": "user", "text": "rename who to Measles Fact Sheet"},
                               {"role": "assistant", "text": "Renamed.", "actions": [{"type": "rename"}]}]), T0, T1))
    catalog.reset_connection()
    monkeypatch.undo()
    _store(renamer / "qdrant", [_point(3, "u2", "r1", "measles fact sheet")])
    # the renamer's collection has its own name
    client = QdrantClient(path=str(renamer / "qdrant"))
    client.create_collection("renamer_docs", vectors_config=client.get_collection("c").config.params.vectors,
                             sparse_vectors_config=client.get_collection("c").config.params.sparse_vectors)
    client.upsert("renamer_docs", points=[_point(3, "u2", "r1", "measles fact sheet")], wait=True)
    client.close()
    monkeypatch.setenv("SERVICES_DATA_DIR", str(tmp_path / "svc"))   # the merge reads and swaps conversation.db there, not the test service's
    monkeypatch.setattr(settings, "collection_name", "c")
    return day7, renamer


def test_the_merge_keeps_every_id_and_passes_its_checks(sources, tmp_path):
    day7, renamer = sources
    report = merge_library.merge(day7, renamer, tmp_path / "staging")
    assert report.ok, report.checks
    merged = sqlite3.connect(tmp_path / "staging" / "app.db")
    docs = dict(merged.execute("SELECT id, title FROM documents").fetchall())
    assert docs == {"d1": "bm25 notes", "d2": "stemming", "r1": "Measles Fact Sheet"}
    assert merged.execute("SELECT owner, file_name FROM documents WHERE id = 'd2'").fetchone() == ("u1", "u1/stemming.md")
    merged.close()
    merged = sqlite3.connect(tmp_path / "staging" / "conversation.db")   # the chats are the Conversation service's file
    assert merged.execute("SELECT title FROM conversations WHERE id = 's1'").fetchone() == ("rename who to Measles Fact Sheet",)
    tools = merged.execute("SELECT role, tool, meta FROM messages WHERE conversation_id = 's1' ORDER BY created_at").fetchall()
    assert tools == [("user", None, None), ("assistant", "manage_documents", '{"actions": [{"type": "rename"}]}')]
    merged.close()
    merged = sqlite3.connect(tmp_path / "staging" / "app.db")
    assert {r[0] for r in merged.execute("SELECT id FROM users")} == {"u1", "u2"}
    assert (tmp_path / "staging" / "library" / "u1" / "stemming.md").read_bytes() == b"Stemming reduces words to their stems"
    merged.close()
    client = QdrantClient(path=str(tmp_path / "staging" / "qdrant_data"))
    point = client.retrieve("c", ids=[2], with_payload=True)[0]
    assert point.payload["metadata"]["title"] == "stemming" and client.count("c").count == 3
    client.close()


def test_a_document_without_a_file_stops_the_merge(sources, tmp_path):
    day7, renamer = sources
    (day7 / "benchmark" / "corpus" / "wikipedia" / "stemming.md").unlink()
    with pytest.raises(merge_library.MergeError, match="no file for day7 document d2"):
        merge_library.merge(day7, renamer, tmp_path / "staging")


def test_applying_twice_is_refused(sources, tmp_path):
    day7, renamer = sources
    report = merge_library.merge(day7, renamer, tmp_path / "staging")
    merge_library.apply(tmp_path / "staging", day7, renamer)
    assert report.ok and (day7 / "backend" / "data" / "app.db.before-merge").exists()
    assert (day7 / "backend" / "data" / "library" / "u1" / "stemming.md").exists()
    with pytest.raises(merge_library.MergeError, match="already merged"):
        merge_library.merge(day7, renamer, tmp_path / "staging2")


def test_a_failed_swap_is_undone(sources, tmp_path, monkeypatch):
    day7, renamer = sources
    merge_library.merge(day7, renamer, tmp_path / "staging")
    real_rename = type(tmp_path).rename

    def rename(self, target):
        if self.name == "qdrant_data" and "staging" in str(self):   # the 4th move: staging's store into place
            raise PermissionError("in use")
        return real_rename(self, target)
    monkeypatch.setattr(type(tmp_path), "rename", rename)
    with pytest.raises(merge_library.MergeError, match="swap undone"):
        merge_library.apply(tmp_path / "staging", day7, renamer)
    data = day7 / "backend" / "data"
    assert (data / "app.db").exists() and not (data / "app.db.before-merge").exists()
    assert (day7 / "backend" / "vector_db" / "qdrant_data").exists()
    assert (tmp_path / "staging" / "app.db").exists()
    with sqlite3.connect(data / "app.db") as c:   # still day7's own, unmerged
        assert "file_hash" in {r[1] for r in c.execute("PRAGMA table_info(documents)")}
