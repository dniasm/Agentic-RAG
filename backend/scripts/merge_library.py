"""One-time merge of the renamer's data (RenamingPipeline/data) into day7's (stage 3 of
docs/superpowers/specs/2026-11-14-merge-renamer-design.md).

    python -m backend.scripts.merge_library                 # dry run: build in staging, print the checks
    python -m backend.scripts.merge_library --apply         # the same, then swap in if every check passed

Stop the server and the services first: the embedded vector store allows one process per folder, and the chats
go into the Conversation service's own file (conversation.db), which is swapped like app.db.

Everything is built in a staging folder from copies; nothing is re-embedded and no model is called. Every user,
document, chunk and chat keeps its id. --apply keeps the originals beside the new files (app.db.before-merge,
qdrant_data.before-merge); RenamingPipeline/data and backend/documents are only read.

  * day7's documents become library documents: title = filename without extension, stored under
    library/<user_id>/ with the library's collision rule. A document without a stored file is copied from the repo
    root or benchmark/corpus/wikipedia/ by SHA-256; a row with no file stops the run.
  * day7's chunks get title, file_name and source = title (a payload update).
  * The renamer's rows are copied as they are; its chunks are copied with their vectors.
  * The renamer's chats become conversations (session id = conversation id) and keep their agent state; they are
    added to a staged copy of conversation.db (day7's own chats are moved there by scripts/migrate_conversations.py).
  * RENAMER_SIGNUP_CODE is copied to SIGNUP_CODE in day7's .env (never printed).
"""
import argparse
import contextlib
import hashlib
import json
import shutil
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from qdrant_client import QdrantClient, models

from backend.config.settings import BACKEND_DIR, PROJECT_ROOT, settings
from backend.library import catalog, config, library
from backend.models import database as db
from services.conversation import db as conv_store
from backend.rag import sparse, vectorstore
from services.common import config as service_config

MIGRATION = "library_merge_v1"
RENAMER_COLLECTION = "renamer_docs"
COPIED_TABLES = ("users", "documents", "renames", "edits", "batches", "batch_tasks", "sessions", "auth_tokens")
DATA_FOLDERS = ("library", "versions", "font-index", "logs")


class MergeError(RuntimeError):
    pass


@dataclass
class Report:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, bool(ok), detail))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.checks)

    def print(self) -> None:
        for note in self.notes:
            print("  ", note)
        for name, ok, detail in self.checks:
            print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
        print("ALL CHECKS PASSED" if self.ok else "CHECKS FAILED")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(conn: sqlite3.Connection, sql: str, args=()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, args)]


@contextlib.contextmanager
def _pointed_at(staging: Path):
    """The catalog and the library's file helpers work on the staging copy while the merge runs."""
    saved = settings.database_path, config.DATA_DIR, config.LIBRARY_DIR, db._conn
    catalog.reset_connection()
    settings.database_path, config.DATA_DIR, config.LIBRARY_DIR, db._conn = (
        str(staging / "app.db"), staging, staging / "library", None)
    try:
        yield
    finally:
        catalog.reset_connection()
        if db._conn is not None:
            db._conn.close()
        settings.database_path, config.DATA_DIR, config.LIBRARY_DIR, db._conn = saved


def _source_files(day7_root: Path) -> dict[str, Path]:
    """Files day7 ingested without keeping a copy (the repo root and the benchmark corpus), by SHA-256."""
    found = {}
    for path in [*day7_root.glob("*.*"), *(day7_root / "benchmark" / "corpus" / "wikipedia").glob("*")]:
        if path.is_file():
            found.setdefault(_sha256(path), path)
    return found


def _stored_file(day7_root: Path, doc: dict, by_hash: dict[str, Path]) -> Path | None:
    upload_dir = day7_root / "backend" / "documents" / doc["user_id"]
    for path in upload_dir.glob(f"{doc['id']}.*") if upload_dir.exists() else []:
        return path
    return by_hash.get(doc["file_hash"])


def _first_user_text(display: list[dict]) -> str:
    return next((m.get("text", "") for m in display if m.get("role") == "user"), "") or "Document chat"


def _message_times(start: str, end: str, n: int) -> list[str]:
    """n ascending timestamps from start to end (the renamer kept one time per chat, day7 one per message)."""
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    step = (b - a) / max(n - 1, 1)
    return [(a + step * i).isoformat() for i in range(n)]


def _readable_by_this_client(meta: Path) -> None:
    """The renamer's store was written by qdrant-client 1.20, which saves `id_tracker: null` in each collection's
    config; day7's pinned 1.19 refuses unknown fields. A null there is the default, so the copy drops it. Any other
    unknown value stops the run."""
    data = json.loads(meta.read_text(encoding="utf-8"))
    for name, collection in data.get("collections", {}).items():
        if "id_tracker" in collection:
            if collection["id_tracker"] is not None:
                raise MergeError(f"{meta}: collection {name} has an id_tracker setting this client can't read")
            del collection["id_tracker"]
    meta.write_text(json.dumps(data), encoding="utf-8")


def _conversation_db_path(conversation_db: Path | None) -> Path:
    return conversation_db or service_config.data_dir() / "conversation.db"


def merge(day7_root: Path, renamer_data: Path, staging: Path, conversation_db: Path | None = None) -> Report:
    report = Report()
    conversation_db = _conversation_db_path(conversation_db)
    day7_db = day7_root / "backend" / "data" / "app.db"
    day7_qdrant = day7_root / "backend" / "vector_db" / "qdrant_data"
    with contextlib.closing(sqlite3.connect(f"file:{day7_db}?mode=ro", uri=True)) as c:
        merged = c.execute("SELECT 1 FROM migrations WHERE name = ?", (MIGRATION,)).fetchone()
    if merged:
        raise MergeError(f"{day7_db} is already merged ({MIGRATION})")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    # ---- copies
    shutil.copy2(day7_db, staging / "app.db")
    shutil.copytree(day7_qdrant, staging / "qdrant_data", ignore=shutil.ignore_patterns(".lock"))
    shutil.copytree(renamer_data / "qdrant", staging / "renamer_qdrant", ignore=shutil.ignore_patterns(".lock"))
    _readable_by_this_client(staging / "renamer_qdrant" / "meta.json")
    for name in DATA_FOLDERS:
        if (renamer_data / name).exists():
            shutil.copytree(renamer_data / name, staging / name)
    (staging / "library").mkdir(exist_ok=True)

    src = sqlite3.connect(f"file:{renamer_data / 'catalog.db'}?mode=ro", uri=True)
    old = sqlite3.connect(f"file:{day7_db}?mode=ro", uri=True)
    # the staged conversation.db starts as a copy of the live one (day7's chats, once migrated), plus the renamer's
    staged_chats = staging / "conversation.db"
    chats_before = 0
    if conversation_db.exists():
        with contextlib.closing(sqlite3.connect(f"file:{conversation_db}?mode=ro", uri=True)) as live, \
                contextlib.closing(sqlite3.connect(staged_chats)) as copy:
            live.backup(copy)
            chats_before = copy.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    expected = {
        "users": len(_rows(old, "SELECT id FROM users")) + len(_rows(src, "SELECT id FROM users")),
        "documents": len(_rows(old, "SELECT id FROM documents")) + len(_rows(src, "SELECT id FROM documents")),
        "conversations": chats_before + len(_rows(src, "SELECT id FROM sessions")),
    }
    day7_docs = _rows(old, "SELECT * FROM documents ORDER BY created_at")
    renamer_sessions = _rows(src, "SELECT * FROM sessions ORDER BY created_at")
    old.close()
    src.close()

    stage = sqlite3.connect(staging / "app.db")
    stage.execute("ALTER TABLE documents RENAME TO documents_day7")
    stage.commit()
    stage.close()

    with _pointed_at(staging):
        db.init_db()   # day7's schema (no documents table) and the catalog's
        conn = sqlite3.connect(f"file:{staging / 'app.db'}", uri=True)   # uri: ATTACH below opens read-only
        conn.execute("PRAGMA foreign_keys = ON")

        # ---- the renamer's rows, as they are
        conn.execute("ATTACH DATABASE ? AS r", (f"file:{renamer_data / 'catalog.db'}?mode=ro",))
        for table in COPIED_TABLES:
            cols = [r[1] for r in conn.execute(f"PRAGMA r.table_info({table})")]
            names = ", ".join(cols)
            conn.execute(f"INSERT INTO main.{table} ({names}) SELECT {names} FROM r.{table}")
        conn.commit()
        conn.execute("DETACH DATABASE r")

        # ---- day7's documents, as library documents
        by_hash = _source_files(day7_root)
        for d in day7_docs:
            path = _stored_file(day7_root, d, by_hash)
            if path is None:
                raise MergeError(f"no file for day7 document {d['id']} ({d['filename']})")
            ext = Path(d["filename"]).suffix.lower()
            title = library._unique_title(Path(d["filename"]).stem, owner=d["user_id"])
            file_name = library.stored_name(d["user_id"], title, ext)
            shutil.copy2(path, config.LIBRARY_DIR / file_name)
            conn.execute(
                "INSERT INTO documents (id, title, original_name, file_name, ext, sha256, original_sha256, size_bytes, "
                "pages, chunks, summary, keywords, owner, status, error, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '[]', ?, ?, ?, ?, ?)",
                (d["id"], title, d["filename"], file_name, ext, d["file_hash"], d["file_hash"], d["size_bytes"],
                 d["pages"], d["chunks"], d["user_id"], d["status"], d["error"], d["created_at"], d["created_at"]))
            conn.commit()   # stored_name and _unique_title read the catalog
        conn.execute("DROP TABLE documents_day7")

        # ---- the renamer's chats, as conversations (in the staged conversation.db)
        chats_db = conv_store.open_db(staged_chats)
        with chats_db.tx() as chats:
            for s in renamer_sessions:
                display = json.loads(s["display"] or "[]")
                chats.execute("INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                              (s["id"], s["owner"], _first_user_text(display).strip()[:60], s["created_at"],
                               s["updated_at"]))
                for m, at in zip(display, _message_times(s["created_at"], s["updated_at"], len(display))):
                    assistant = m.get("role") == "assistant"
                    meta = json.dumps({"actions": m["actions"]}) if m.get("actions") else None
                    chats.execute("INSERT INTO messages (id, conversation_id, role, content, tool, meta, created_at) "
                                  "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                  (uuid.uuid4().hex, s["id"], "assistant" if assistant else "user", m.get("text", ""),
                                   "manage_documents" if assistant else None, meta, at))
        n_chats = chats_db.connection().execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        chats_db.close()
        conn.execute("INSERT INTO migrations (name, applied_at) VALUES (?, ?)", (MIGRATION, datetime.now().isoformat()))
        conn.commit()

        # ---- vectors
        target = QdrantClient(path=str(staging / "qdrant_data"))
        origin = QdrantClient(path=str(staging / "renamer_qdrant"))
        name = settings.collection_name
        try:
            if origin.get_collection(RENAMER_COLLECTION).config.params != target.get_collection(name).config.params:
                raise MergeError("the two collections are configured differently")
            for d in day7_docs:
                doc = catalog.get_document(d["id"])
                target.set_payload(name, payload={"title": doc["title"], "file_name": doc["file_name"],
                                                  "source": doc["title"]},
                                   key="metadata", wait=True,
                                   points=models.FilterSelector(filter=vectorstore.user_filter(d["user_id"], d["id"])))
            offset, copied = None, 0
            while True:
                points, offset = origin.scroll(RENAMER_COLLECTION, limit=128, offset=offset, with_payload=True,
                                               with_vectors=True)
                if points:
                    target.upsert(name, points=[models.PointStruct(id=p.id, vector=p.vector, payload=p.payload)
                                                for p in points], wait=True)
                    copied += len(points)
                if offset is None:
                    break
            report.notes.append(f"renamer chunks copied: {copied}")
            _checks(report, conn, target, expected, n_chats)
        finally:
            target.close()
            origin.close()
            conn.close()
    shutil.rmtree(staging / "renamer_qdrant")
    return report


def _checks(report: Report, conn: sqlite3.Connection, client: QdrantClient, expected: dict, n_chats: int) -> None:
    name = settings.collection_name
    for table in ("users", "documents"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        report.check(f"{table}: {n}", n == expected[table], f"expected {expected[table]}")
    report.check(f"conversations: {n_chats}", n_chats == expected["conversations"], f"expected {expected['conversations']}")
    docs = _rows(conn, "SELECT * FROM documents")
    bad_files = [d["id"] for d in docs if not (config.LIBRARY_DIR / d["file_name"]).is_file()
                 or _sha256(config.LIBRARY_DIR / d["file_name"]) != d["sha256"]]
    report.check("every document has its file, with a matching SHA-256", not bad_files, ", ".join(bad_files[:5]))
    bad_chunks = []
    for d in docs:
        if d["status"] != "indexed":
            continue
        n = client.count(name, count_filter=vectorstore.user_filter(d["owner"], d["id"]), exact=True).count
        if n != d["chunks"]:
            bad_chunks.append(f"{d['id']} {n}/{d['chunks']}")
    report.check("chunks in the collection = chunks column, every indexed document", not bad_chunks,
                 ", ".join(bad_chunks[:5]))
    leaks = []
    owners = {d["owner"] for d in docs}
    for owner in sorted(owners):
        mine = {d["id"] for d in docs if d["owner"] == owner}
        own, _ = client.scroll(name, scroll_filter=vectorstore.user_filter(owner), limit=1, with_payload=True)
        if not own:   # nothing indexed (a failed upload only)
            continue
        query = sparse.query_vector(own[0].payload["page_content"][:300])   # a passage of their own
        hits = client.query_points(name, query=query, using=vectorstore.SPARSE, limit=20, with_payload=True,
                                   query_filter=vectorstore.user_filter(owner)).points
        leaks += [str(h.id) for h in hits if h.payload["metadata"]["doc_id"] not in mine
                  or h.payload["metadata"]["user_id"] != owner]
        if not hits:
            leaks.append(f"{owner}: no hits for a passage of their own")
    report.check(f"a search per user with chunks ({len(owners)} users) returns only that user's documents", not leaks, ", ".join(leaks[:5]))
    ids, orphans, total, offset = {d["id"] for d in docs}, 0, 0, None
    while True:
        points, offset = client.scroll(name, limit=1000, offset=offset, with_payload=["metadata"], with_vectors=False)
        total += len(points)
        orphans += sum(1 for p in points if p.payload["metadata"].get("doc_id") not in ids)
        if offset is None:
            break
    report.notes.append(f"points in {name}: {total} (move to a Qdrant server at 20,000)")
    report.check("every point belongs to a document in the catalog", orphans == 0, f"{orphans} orphaned")


def _move_signup_code(renamer_env: Path, day7_env: Path) -> str:
    from dotenv import dotenv_values
    code = dotenv_values(renamer_env).get("RENAMER_SIGNUP_CODE") if renamer_env.exists() else None
    if not code:
        return "no RENAMER_SIGNUP_CODE to move"
    if "SIGNUP_CODE" in dotenv_values(day7_env):
        return "SIGNUP_CODE already set in day7's .env"
    with day7_env.open("a", encoding="utf-8") as f:
        f.write(f"\n# Invite code for sign-ups after the first account (moved from RenamingPipeline/.env)\nSIGNUP_CODE={code}\n")
    return "SIGNUP_CODE added to day7's .env"


def apply(staging: Path, day7_root: Path, renamer_data: Path, conversation_db: Path | None = None) -> None:
    """Swap the staged data in. Every move is undone if one fails; nothing after the swap can fail the run."""
    data = day7_root / "backend" / "data"
    qdrant = day7_root / "backend" / "vector_db" / "qdrant_data"
    moves = [(data / "app.db", data / "app.db.before-merge"),
             (qdrant, qdrant.with_name("qdrant_data.before-merge")),
             (staging / "app.db", data / "app.db"),
             (staging / "qdrant_data", qdrant)]
    moves += [(staging / name, data / name) for name in DATA_FOLDERS if (staging / name).exists()]
    chats = _conversation_db_path(conversation_db)
    chats_moves = []
    if (staging / "conversation.db").exists():
        chats.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(staging / "conversation.db")) as c:   # fold the log in, as for app.db
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.execute("PRAGMA journal_mode = DELETE")
        if chats.exists():
            chats_moves.append((chats, chats.with_name("conversation.db.before-merge")))
        chats_moves.append((staging / "conversation.db", chats))
    # sqlite's write-ahead log may still hold the last writes: fold it into app.db (its -wal/-shm go with it)
    with contextlib.closing(sqlite3.connect(staging / "app.db")) as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("PRAGMA journal_mode = DELETE")   # the app turns WAL back on when it connects
    taken = [str(dst) for _, dst in [moves[0], moves[1], *moves[4:], *chats_moves[:-1]] if dst.exists()]
    moves += chats_moves
    if taken:
        raise MergeError(f"already there: {', '.join(taken)}")
    done = []
    try:
        for src, dst in moves:
            src.rename(dst)
            done.append((src, dst))
    except OSError as e:
        for src, dst in reversed(done):
            dst.rename(src)
        raise MergeError(f"swap undone, nothing changed ({e})") from e
    for step in (lambda: shutil.rmtree(staging), lambda: print(_move_signup_code(renamer_data.parent / ".env",
                                                                                  day7_root / ".env"))):
        try:
            step()
        except Exception as e:   # the data is swapped in; these are tidy-ups
            print(f"warning: {e}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m backend.scripts.merge_library")
    parser.add_argument("--apply", action="store_true", help="swap the merged data in if every check passes")
    parser.add_argument("--renamer-data", type=Path, default=PROJECT_ROOT.parent / "RenamingPipeline" / "data")
    args = parser.parse_args()
    staging = BACKEND_DIR / "data" / ".merge-staging"
    report = merge(PROJECT_ROOT, args.renamer_data, staging)
    report.print()
    if not args.apply:
        print(f"Dry run: built in {staging}; nothing swapped.")
        return
    if not report.ok:
        raise SystemExit("Not applied: a check failed.")
    apply(staging, PROJECT_ROOT, args.renamer_data)
    print("Applied. Originals kept as app.db.before-merge, qdrant_data.before-merge and conversation.db.before-merge.")


if __name__ == "__main__":
    main()
