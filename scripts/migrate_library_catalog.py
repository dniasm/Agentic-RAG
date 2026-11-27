"""Copy the library catalog from the orchestrator's app.db into the Document service (document.db).

    python scripts/migrate_library_catalog.py     # the Document service must be running; stop the backend first
    python scripts/migrate_library_catalog.py --source <app.db> --address localhost:50064 --dest <backup folder>

What moves: the tables `documents`, `renames`, `edits`, `undone_edits`, `batches`, `batch_tasks` and `sessions` (the
library chat agent's state), with their ids and timestamps. The stored files (the library and versions folders) are not
copied: they stay where they are.

In order: (1) back up app.db (SQLite's backup API) into <dest>/day7-library-catalog-<time>/; (2) send the rows to the
service in batches; rows it already has are kept as they are, so running it again copies nothing and is safe after a
crash; (3) verify: sending every row once more creates nothing, and the service holds at least as many rows per table
as the source. Exit 0 only when every check passed.

The source tables are never touched: they stay in app.db, now unused (the orchestrator no longer reads them). Delete
them by hand once the library has been seen working in the app.
"""
from __future__ import annotations

import argparse
import contextlib
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

BATCH = 100   # sessions carry whole conversations: keep a request well under gRPC's 4 MB message limit
TIMEOUT_SECONDS = 120.0
DEFAULT_BACKUP_DEST = Path(r"D:\onboarding_tasks\backups")
TABLES = ("documents", "renames", "edits", "undone_edits", "batches", "batch_tasks", "sessions")


class MigrationError(RuntimeError):
    pass


@dataclass
class Report:
    source: dict = field(default_factory=dict)      # rows per table in app.db
    created: dict = field(default_factory=lambda: dict.fromkeys(TABLES, 0))
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
            print(f"  backup of app.db: {self.backup}")
        print("  source: " + ", ".join(f"{n} {t}" for t, n in self.source.items()))
        print("  copied now: " + ", ".join(f"{n} {t}" for t, n in self.created.items()))
        for name, ok, detail in self.checks:
            print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
        for line in self.left_behind:
            print(f"  left behind: {line}")
        print("MIGRATION VERIFIED" if self.ok else "MIGRATION FAILED")


def backup_source(source: Path, dest_root: Path, now: datetime | None = None) -> Path:
    folder = dest_root / f"day7-library-catalog-{(now or datetime.now()).strftime('%Y-%m-%d_%H%M%S')}"
    if folder.exists():
        raise MigrationError(f"{folder} exists already; a backup never overwrites one")
    folder.mkdir(parents=True)
    with contextlib.closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as src, \
            contextlib.closing(sqlite3.connect(folder / "app.db")) as dst:
        src.backup(dst)
    return folder


def _get(row: sqlite3.Row, name: str, default=None):
    return row[name] if name in row.keys() and row[name] is not None else default


def _document(r) -> pb.CatalogDocument:
    m = pb.CatalogDocument(
        id=r["id"], title=r["title"], original_name=r["original_name"], file_name=r["file_name"], ext=r["ext"],
        sha256=r["sha256"], size_bytes=r["size_bytes"], summary=_get(r, "summary", ""),
        keywords_json=_get(r, "keywords", "[]"), owner=_get(r, "owner", "library"), status=r["status"],
        created_at=r["created_at"], updated_at=r["updated_at"])
    for name in ("original_sha256", "pages", "chunks", "error"):
        if _get(r, name) is not None:
            setattr(m, name, r[name])
    return m


def _task(r) -> pb.CatalogTask:
    m = pb.CatalogTask(id=r["id"], batch_id=r["batch_id"], position=r["position"], instruction=r["instruction"],
                       status=r["status"], reply=_get(r, "reply", ""), actions_json=_get(r, "actions", "[]"),
                       tokens=_get(r, "tokens", 0))
    for name, field_name in (("payload", "payload_json"), ("checkpoint", "checkpoint_json"), ("started_at", "started_at"),
                             ("finished_at", "finished_at")):
        if _get(r, name) is not None:
            setattr(m, field_name, r[name])
    return m


def _session(r) -> pb.StoredSession:
    return pb.StoredSession(
        session=pb.LibrarySession(id=r["id"], turn=_get(r, "turn", 0), llm_messages_json=_get(r, "llm_messages", "[]"),
                                  display_json=_get(r, "display", "[]"), guard_json=_get(r, "guard", "{}"),
                                  owner=_get(r, "owner", "library")),
        created_at=r["created_at"], updated_at=r["updated_at"])


def read_dumps(src: sqlite3.Connection) -> tuple[list[pb.CatalogDump], dict]:
    """The source rows as dumps of at most BATCH rows of one kind each, and the row count per table."""
    have = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    def rows(table: str, order: str = "rowid") -> list:
        return src.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall() if table in have else []

    documents, renames, edits = rows("documents"), rows("renames", "id"), rows("edits", "id")
    undone, sessions = rows("undone_edits", "id"), rows("sessions")
    batches, tasks = rows("batches"), rows("batch_tasks", "id")
    tasks_of: dict[str, list] = {}
    for t in tasks:
        tasks_of.setdefault(t["batch_id"], []).append(_task(t))
    counts = {"documents": len(documents), "renames": len(renames), "edits": len(edits), "undone_edits": len(undone),
              "batches": len(batches), "batch_tasks": len(tasks), "sessions": len(sessions)}
    dumps: list[pb.CatalogDump] = []

    def add(field_name: str, items: list) -> None:
        for i in range(0, len(items), BATCH):
            dump = pb.CatalogDump()
            getattr(dump, field_name).extend(items[i:i + BATCH])
            dumps.append(dump)

    add("documents", [_document(r) for r in documents])
    add("renames", [pb.RenameRow(id=r["id"], doc_id=r["doc_id"], old_title=r["old_title"], new_title=r["new_title"],
                                 reason=r["reason"], at=r["at"]) for r in renames])
    add("edits", [pb.EditRecord(id=r["id"], doc_id=r["doc_id"], operation=r["operation"], detail=r["detail"],
                                backup=r["backup"], at=r["at"]) for r in edits])
    add("undone_edits", [pb.UndoneEdit(id=r["id"], doc_id=r["doc_id"], operation=r["operation"], detail=r["detail"],
                                       backup=r["backup"], redo=r["redo"], ext=r["ext"], at=r["at"],
                                       undone_at=r["undone_at"], undo_order=_get(r, "undo_order", 0)) for r in undone])
    add("batches", [pb.CatalogBatch(id=r["id"], session_id=r["session_id"], owner=_get(r, "owner", "library"),
                                    created_at=r["created_at"], tasks=tasks_of.get(r["id"], [])) for r in batches])
    add("sessions", [_session(r) for r in sessions])
    return dumps, counts


def migrate(source: Path, address: str, backup_dest: Path | None = DEFAULT_BACKUP_DEST,
            request_id: str | None = None) -> Report:
    """Back up, copy, verify. Raises MigrationError when the source or the service can't be used; check report.ok
    for the verification."""
    if not source.is_file():
        raise MigrationError(f"no database at {source}")
    report = Report()
    if backup_dest is not None:
        report.backup = backup_source(source, backup_dest)
    metadata = ((REQUEST_ID_HEADER, request_id or f"migrate-{datetime.now():%H%M%S}"),)
    with contextlib.closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as src:
        src.row_factory = sqlite3.Row
        dumps, report.source = read_dumps(src)

    with grpc.insecure_channel(address, options=[("grpc.max_send_message_length", 16 * 1024 * 1024)]) as channel:
        stub = pb_grpc.DocumentServiceStub(channel)

        def send(dump: pb.CatalogDump) -> pb.CatalogCounts:
            return stub.ImportCatalog(pb.ImportCatalogRequest(dump=dump), timeout=TIMEOUT_SECONDS,
                                      metadata=metadata).created

        try:
            for dump in dumps:
                made = send(dump)
                for table in TABLES:
                    report.created[table] += getattr(made, table)
            # Sending a row the service has creates nothing: so a repeat that creates nothing proves each row is there.
            missing = dict.fromkeys(TABLES, 0)
            for dump in dumps:
                made = send(dump)
                for table in TABLES:
                    missing[table] += getattr(made, table)
            held = stub.CountCatalog(pb.CatalogOwnerlessRequest(), timeout=TIMEOUT_SECONDS, metadata=metadata).held
        except grpc.RpcError as e:
            raise MigrationError(f"the Document service at {address} failed: {e.code().name} {e.details()}") from e
    for table in TABLES:
        report.check(f"every {table} row is in the service ({report.source[table]} rows)", missing[table] == 0,
                     f"{missing[table]} were still missing" if missing[table] else "")
        report.check(f"the service holds at least as many {table} as the source", getattr(held, table) >= report.source[table],
                     f"service has {getattr(held, table)}")
    report.left_behind = [f"app.db tables {', '.join(TABLES)}", "the stored files in the library and versions folders"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, help="the orchestrator's database (default: DATABASE_PATH / settings)")
    parser.add_argument("--address", default=config.service_address("document"), help="the Document service")
    parser.add_argument("--dest", type=Path, default=DEFAULT_BACKUP_DEST, help="the folder the backup goes under")
    args = parser.parse_args()
    from backend.config.settings import settings
    source = args.source or Path(settings.database_path)
    try:
        report = migrate(source, args.address, backup_dest=args.dest)
    except MigrationError as e:
        print(f"migration stopped: {e}", file=sys.stderr)
        return 1
    report.print()
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
