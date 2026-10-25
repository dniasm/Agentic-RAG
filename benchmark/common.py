"""Shared benchmark helpers: the benchmark user, corpus ingestion, and relevance matching.

The benchmark indexes the test corpus for a dedicated user ("benchmark_bot") through the app's real
ingestion pipeline, so results reflect what users get. Run with the backend STOPPED: the embedded Qdrant
store can only be opened by one process at a time.
"""
import json
import re
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.api.security import hash_password  # noqa: E402
from backend.models import database as db  # noqa: E402
from backend.rag import vectorstore  # noqa: E402
from backend.rag.ingestion import file_sha256, ingest_file  # noqa: E402

BENCH_DIR = Path(__file__).resolve().parent
BENCH_USER = "benchmark_bot"
CORPUS_FILES = [ROOT / "CCS_using_SVM.pdf", ROOT / "nlp_research_gate.pdf", ROOT / "nlp.pdf",
                *sorted((BENCH_DIR / "corpus" / "wikipedia").glob("*.md")),
                # Added 2026-10-02: other subjects and document types (law, finance/tables, health, .docx)
                ROOT / "CELEX_32016R0679_EN_TXT.pdf", ROOT / "World Bank Annual Report 2024.pdf",
                ROOT / "9789240061262-eng.pdf", ROOT / "claude_wiki.docx"]


def load_questions() -> list[dict]:
    return json.loads((BENCH_DIR / "questions.json").read_text(encoding="utf-8"))["questions"]


def bench_user() -> dict:
    db.init_db()
    user = db.get_user_by_username(BENCH_USER)
    if not user:  # random password: this account is never logged into
        user = db.create_user(BENCH_USER, hash_password(secrets.token_urlsafe(24)))
    return {"id": user["id"], "username": user["username"]}


def ensure_corpus(user: dict, reindex: bool = False) -> list[str]:
    """Index every corpus file for the benchmark user (skips files already indexed unless reindex=True)."""
    names = []
    for path in CORPUS_FILES:
        data = path.read_bytes()
        digest = file_sha256(data)
        existing = db.find_document_by_hash(user["id"], digest)
        if existing and existing["status"] == "indexed" and not reindex:
            names.append(existing["filename"])
            continue
        if existing:
            vectorstore.delete_document_vectors(user["id"], existing["id"])
            db.delete_document(user["id"], existing["id"])
        doc = db.add_document(user["id"], path.name, digest, len(data))
        for attempt in range(4):  # the free embedding tier rate-limits big batches: back off and retry
            try:
                result = ingest_file(path, user_id=user["id"], doc_id=doc["id"], filename=path.name)
                break
            except Exception as e:
                if attempt == 3:
                    raise
                print(f"   ingest {path.name} failed ({str(e)[:80]}), retrying in 60s")
                time.sleep(60)
        db.update_document(doc["id"], status="indexed", pages=result["pages"], chunks=result["chunks"])
        print(f"   indexed {path.name}: {result['chunks']} chunks")
        names.append(path.name)
    return names


def all_chunks(user: dict) -> list[dict]:
    """Every indexed chunk of the benchmark user, as {"source", "content", "page"}."""
    client = vectorstore.get_client()
    out, offset = [], None
    while True:
        points, offset = client.scroll(vectorstore.settings.collection_name,
                                       scroll_filter=vectorstore.user_filter(user["id"]),
                                       limit=256, offset=offset, with_payload=True)
        for p in points:
            meta = p.payload.get("metadata", {})
            out.append({"source": meta.get("source"), "content": p.payload.get("page_content", ""),
                        "page": meta.get("page")})
        if offset is None:
            return out


_ws = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _ws.sub(" ", text).lower()


def is_relevant(hit: dict, q: dict) -> bool:
    if hit.get("source") not in q["sources"]:
        return False
    if not q["gold"]:
        return True
    text = _norm(hit.get("content", ""))
    return any(_norm(g) in text for g in q["gold"])
