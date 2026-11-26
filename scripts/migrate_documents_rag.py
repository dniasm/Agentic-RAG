"""Copy the document passages and the RAG caches from the orchestrator's data into the Document service.

    python scripts/migrate_documents_rag.py    # STOP THE BACKEND FIRST; the Document service must be running
    python scripts/migrate_documents_rag.py --qdrant <folder> --data-dir <folder> --address localhost:50064 --dest <backup>

The orchestrator's embedded Qdrant folder can be opened by one process only, and this script reads it directly: stop the
backend (uvicorn) before running it, or it refuses ("already in use"). The Document service has its own folder, so it can
keep running. (Needs only the service's address; run_all.py without the orchestrator is enough.)

What moves: (1) passages: every point of the document collection (settings.collection_name) with its id, dense vector,
BM25 sparse vector and payload, so nothing is embedded again; (2) caches: `page_text` (OCR text of scanned pages) from
ocr_cache.db and `query_embeddings` (question embeddings) from embedding_cache.db, both next to app.db.

In order: (1) copy the Qdrant folder and the two cache files into <dest>/day7-documents-<time>/; (2) send everything to the
service in batches, which keeps ids, vectors and keys and ignores what it already has, so running it again copies nothing
and is safe after a crash; (3) verify: sending every passage and cache row once more creates nothing (each is there), and
the service holds at least as many as the source. Exit 0 only when every check passed.

The sources are never touched: the collection stays in the orchestrator's Qdrant and the cache files stay where they are,
now unused. Delete them by hand once documents have been seen working in the app. The documents catalog (app.db) is not
part of this step.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import grpc

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agentic_rag_proto import document_pb2 as pb  # noqa: E402
from agentic_rag_proto import document_pb2_grpc as pb_grpc  # noqa: E402
from services.common import config  # noqa: E402
from services.common.log import REQUEST_ID_HEADER  # noqa: E402

BATCH = 100   # a passage carries a 768-number vector and its text, a cached question 768 numbers as text: well under 4 MB
TIMEOUT_SECONDS = 120.0
DEFAULT_BACKUP_DEST = Path(r"D:\onboarding_tasks\backups")
CACHES = (("ocr_cache.db", "page_text", "text"), ("embedding_cache.db", "query_embeddings", "vector"))


class MigrationError(RuntimeError):
    pass


@dataclass
class Report:
    passages: int = 0               # in the source
    page_texts: int = 0
    query_embeddings: int = 0
    created: dict = field(default_factory=lambda: {"passages": 0, "caches": 0})
    backup: Path | None = None
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    left_behind: list[str] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, bool(ok), detail))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.checks)

    def print(self) -> None:
        if self.backup:
            print(f"  backup of the Qdrant folder and the cache files: {self.backup}")
        print(f"  source: {self.passages} passages, {self.page_texts} OCR pages, {self.query_embeddings} question embeddings")
        print("  copied now: " + ", ".join(f"{n} {k}" for k, n in self.created.items()))
        for name, ok, detail in self.checks:
            print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
        for line in self.left_behind:
            print(f"  left behind: {line}")
        print("MIGRATION VERIFIED" if self.ok else "MIGRATION FAILED")


def backup_source(qdrant: Path, data_dir: Path, dest_root: Path, now: datetime | None = None) -> Path:
    """The Qdrant folder and the cache files into a new folder under dest_root (never overwrites one)."""
    folder = dest_root / f"day7-documents-{(now or datetime.now()).strftime('%Y-%m-%d_%H%M%S')}"
    if folder.exists():
        raise MigrationError(f"{folder} exists already; a backup never overwrites one")
    folder.mkdir(parents=True)
    if qdrant.is_dir():
        shutil.copytree(qdrant, folder / "qdrant_data", ignore=shutil.ignore_patterns(".lock"))
    for name, _table, _column in CACHES:
        if (data_dir / name).is_file():
            with contextlib.closing(sqlite3.connect(f"file:{(data_dir / name).as_posix()}?mode=ro", uri=True)) as src, \
                    contextlib.closing(sqlite3.connect(folder / name)) as dst:
                src.backup(dst)
    return folder


def read_passages(qdrant: Path, collection: str) -> list[pb.StoredPassage]:
    from qdrant_client import QdrantClient
    from services.document import vectorstore
    try:
        client = QdrantClient(path=str(qdrant))
    except RuntimeError as e:
        if "already accessed" in str(e):
            raise MigrationError("the orchestrator's vector store is in use: stop the backend, then run this again") from e
        raise
    try:
        if not client.collection_exists(collection):
            return []
        out, offset = [], None
        while True:
            points, offset = client.scroll(collection, limit=200, offset=offset, with_payload=True, with_vectors=True)
            for p in points:
                vectors = p.vector if isinstance(p.vector, dict) else {}
                dense, sparse = vectors.get(vectorstore.DENSE), vectors.get(vectorstore.SPARSE)
                if dense is None or sparse is None:
                    raise MigrationError(f"point {p.id} has no dense and sparse vectors (an old single-vector collection?)")
                out.append(pb.StoredPassage(id=str(p.id), dense=list(dense), payload_json=json.dumps(p.payload or {}),
                                            sparse=pb.SparseVector(indices=list(sparse.indices), values=list(sparse.values))))
            if offset is None:
                return out
    finally:
        client.close()


def read_cache(data_dir: Path, name: str, table: str, column: str) -> list[pb.CacheRow]:
    path = data_dir / name
    if not path.is_file():
        return []
    with contextlib.closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as src:
        try:
            return [pb.CacheRow(key=k, value=v) for k, v in src.execute(f"SELECT key, {column} FROM {table}")]
        except sqlite3.OperationalError:
            return []


def _batches(items: list):
    for start in range(0, len(items), BATCH):
        yield items[start:start + BATCH]


def migrate(qdrant: Path, data_dir: Path, address: str, collection: str | None = None,
            backup_dest: Path | None = DEFAULT_BACKUP_DEST, request_id: str | None = None) -> Report:
    """Back up, copy, verify. Raises MigrationError when the source or the service can't be used; check
    report.ok for the verification."""
    if collection is None:
        from backend.config.settings import settings
        collection = settings.collection_name
    if not qdrant.is_dir() and not any((data_dir / name).is_file() for name, _, _ in CACHES):
        raise MigrationError(f"nothing to copy: no Qdrant folder at {qdrant} and no cache files in {data_dir}")
    report = Report()
    if backup_dest is not None:
        report.backup = backup_source(qdrant, data_dir, backup_dest)
    metadata = ((REQUEST_ID_HEADER, request_id or f"migrate-{datetime.now():%H%M%S}"),)
    passages = read_passages(qdrant, collection) if qdrant.is_dir() else []
    pages = read_cache(data_dir, *CACHES[0])
    queries = read_cache(data_dir, *CACHES[1])
    report.passages, report.page_texts, report.query_embeddings = len(passages), len(pages), len(queries)

    with grpc.insecure_channel(address, options=[("grpc.max_send_message_length", 16 * 1024 * 1024)]) as channel:
        stub = pb_grpc.DocumentServiceStub(channel)

        def send_passages(batch) -> int:
            return stub.ImportPassages(pb.ImportPassagesRequest(passages=batch), timeout=TIMEOUT_SECONDS,
                                       metadata=metadata).created

        def send_caches(pages_batch, queries_batch) -> int:
            return stub.ImportCaches(pb.ImportCachesRequest(page_texts=pages_batch, query_embeddings=queries_batch),
                                     timeout=TIMEOUT_SECONDS, metadata=metadata).created

        def send_all() -> tuple[int, int]:
            made_passages = sum(send_passages(b) for b in _batches(passages))
            made_caches = sum(send_caches(b, []) for b in _batches(pages)) + sum(send_caches([], b) for b in _batches(queries))
            return made_passages, made_caches

        try:
            report.created["passages"], report.created["caches"] = send_all()
            # Sending a row the service has creates nothing: so a repeat that creates nothing proves each row is there.
            missing_passages, missing_caches = send_all()
            held = stub.CountPassages(pb.CountPassagesRequest(), timeout=TIMEOUT_SECONDS, metadata=metadata)
        except grpc.RpcError as e:
            raise MigrationError(f"the Document service at {address} failed: {e.code().name} {e.details()}") from e
    report.check(f"every passage is in the service, with its vectors ({len(passages)} passages)", missing_passages == 0,
                 f"{missing_passages} were still missing" if missing_passages else "")
    report.check(f"every cached OCR page and question embedding is in the service ({len(pages) + len(queries)} rows)",
                 missing_caches == 0, f"{missing_caches} were still missing" if missing_caches else "")
    report.check("the service holds at least as many as the source",
                 held.passages >= len(passages) and held.page_texts >= len(pages) and held.query_embeddings >= len(queries),
                 f"service has {held.passages} passages, {held.page_texts} OCR pages, {held.query_embeddings} question embeddings")
    report.left_behind = [f"Qdrant collection {collection} ({len(passages)} points)",
                          f"{CACHES[0][0]} ({len(pages)} rows), {CACHES[1][0]} ({len(queries)} rows)"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--qdrant", type=Path, help="the orchestrator's embedded Qdrant folder (default: QDRANT_PATH / settings)")
    parser.add_argument("--data-dir", type=Path, help="the folder with ocr_cache.db and embedding_cache.db (default: next to DATABASE_PATH)")
    parser.add_argument("--address", default=config.service_address("document"), help="the Document service")
    parser.add_argument("--dest", type=Path, default=DEFAULT_BACKUP_DEST, help="the folder the backup goes under")
    args = parser.parse_args()
    from backend.config.settings import settings
    qdrant = args.qdrant or Path(settings.qdrant_path)
    data_dir = args.data_dir or Path(settings.database_path).parent
    print("Reading the orchestrator's Qdrant folder directly: the backend must be stopped.")
    try:
        report = migrate(qdrant, data_dir, args.address, backup_dest=args.dest)
    except MigrationError as e:
        print(f"migration stopped: {e}", file=sys.stderr)
        return 1
    report.print()
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
