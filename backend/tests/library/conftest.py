"""Test setup: a throw-away data folder and fake models. No test calls Gemini, Groq or Voyage.

  embeddings   bag-of-words vectors hashed into 768 dimensions (similar words -> similar vectors)
  reranker     share of the query's words found in the passage, on Voyage's 0-1 scale
  title model  returns the first line of the text as the title
"""
import hashlib
import math
import shutil

import pytest

from backend.library import catalog, config, library, titles
from backend.rag import ingestion, rerank, retrieval, sparse, vectorstore  # noqa: F401
from backend.models import database as db

db.init_db()   # day7's tables too (the app's startup does this; TestClient without `with` skips it)


def fake_vector(text: str) -> list[float]:
    v = [0.0] * 768
    for token in sparse.tokenize(text):
        v[int(hashlib.md5(token.encode()).hexdigest(), 16) % 768] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def fake_rerank(query: str, hits: list[dict], priority=None):
    words = set(sparse.tokenize(query))
    for h in hits:
        found = len(words & set(sparse.tokenize(h["content"]))) / (len(words) or 1)
        h["relevance"], h["scorer"] = round(0.3 + 0.7 * found, 4), "voyage"
    return sorted(hits, key=lambda h: h["relevance"], reverse=True)


def fake_title(prompt: str) -> titles.DocProfile:
    text = prompt.split("<<<", 1)[1].strip()
    first = text.splitlines()[0].strip()
    return titles.DocProfile(title=first, summary=" ".join(text.split()[:40]), keywords=[])


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    from backend.config import llm
    llm.key_pool.reset()   # no rate-limited key carried over from another test
    monkeypatch.setattr(ingestion, "embed_paced", lambda texts, title: [fake_vector(t) for t in texts])
    monkeypatch.setattr(retrieval.embedding_cache, "embed_query", fake_vector)
    monkeypatch.setattr(rerank, "score", fake_rerank)
    monkeypatch.setattr(titles, "_ask_model", fake_title)
    # every test starts with no remembered searches
    library._FIND_CACHE.clear()
    yield
    for d in catalog.list_documents():
        library.delete(d["id"])
    with catalog.tx() as c:
        c.execute("DELETE FROM sessions")
        c.execute("DELETE FROM renames")
        c.execute("DELETE FROM batches")
        c.execute("DELETE FROM batch_tasks")
        c.execute("DELETE FROM users")
        c.execute("DELETE FROM auth_tokens")
    for folder in config.LIBRARY_DIR.iterdir() if config.LIBRARY_DIR.exists() else []:
        if folder.is_dir():   # users' own folders
            shutil.rmtree(folder, ignore_errors=True)
    from backend.library import auth
    auth._failures.clear()


@pytest.fixture
def signed_in():
    """A browser signed in as the first account (which takes any documents made before it, like the real owner's)."""
    from fastapi.testclient import TestClient
    from backend.app import app
    client = TestClient(app)
    r = client.post("/api/auth/signup", json={"username": "tester", "password": "correct horse"})
    assert r.status_code == 200, r.text
    client.user = r.json()["user"]
    return client



DOCS = {
    "gdpr_final.txt": "EU General Data Protection Regulation\n"
                      "The regulation protects personal data of people in the European Union. It sets rules for "
                      "consent, data controllers, processors, fines and the right to erasure.",
    "w2v.txt": "Word2vec Neural Word Embeddings\n"
               "Word2vec learns word embeddings with a shallow neural network. Mikolov at Google introduced the "
               "skip-gram and continuous bag of words models for word vectors.",
    "glove.txt": "GloVe Word Embeddings\n"
                 "GloVe learns word embeddings from global co-occurrence counts. Stanford researchers built word "
                 "vectors from a co-occurrence matrix.",
    "svm.txt": "Support Vector Machines\n"
               "A support vector machine is a classifier that finds the maximum margin hyperplane. Kernels map the "
               "inputs into higher dimensional spaces.",
}


@pytest.fixture
def library_docs(tmp_path) -> dict[str, dict]:
    """The four DOCS, ingested. Keyed by their original file name."""
    out = {}
    for name, text in DOCS.items():
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        doc, _ = library.ingest_path(path)
        assert doc["status"] == "indexed", doc["error"]
        out[name] = doc
    return out
