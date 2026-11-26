"""Copy the conversations and messages in the orchestrator's app.db into the Conversation service.

    python scripts/migrate_conversations.py                    # the service must be running (python scripts/run_all.py)
    python scripts/migrate_conversations.py --source <app.db> --address localhost:50062 --dest <backup folder>

In order: (1) back up app.db into <dest>/day7-conversations-<time>/ with SQLite's backup API (a consistent copy
while the backend runs); (2) send every conversation and its messages to the service, which keeps their ids and
timestamps and ignores rows it already has, so running it again copies nothing and is safe after a crash;
(3) verify, per conversation, that the service holds the same number of messages, and in total the same counts.
Exit 0 only when every check passed.

The source tables are never touched: `conversations` and `messages` stay in app.db, now unused (the orchestrator no
longer reads or creates them). Drop them by hand once the chats have been seen in the app. Messages whose
conversation no longer exists are skipped and reported.
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

from agentic_rag_proto import conversation_pb2 as pb  # noqa: E402
from agentic_rag_proto import conversation_pb2_grpc as pb_grpc  # noqa: E402
from services.common import config  # noqa: E402
from services.common.log import REQUEST_ID_HEADER  # noqa: E402

BATCH = 200            # messages per ImportConversation call (the conversation row goes with each; repeats are ignored)
TIMEOUT_SECONDS = 60.0
DEFAULT_BACKUP_DEST = Path(r"D:\onboarding_tasks\backups")


class MigrationError(RuntimeError):
    pass


@dataclass
class Report:
    conversations: int = 0              # in the source
    messages: int = 0
    conversations_created: int = 0      # new in the service on this run
    messages_created: int = 0
    skipped_messages: int = 0           # no conversation in the source
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
        print(f"  source: {self.conversations} conversations, {self.messages} messages"
              + (f" ({self.skipped_messages} skipped: no conversation)" if self.skipped_messages else ""))
        print(f"  copied now: {self.conversations_created} conversations, {self.messages_created} messages")
        for name, ok, detail in self.checks:
            print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
        for line in self.left_behind:
            print(f"  left behind: {line}")
        print("MIGRATION VERIFIED" if self.ok else "MIGRATION FAILED")


def backup_source(source: Path, dest_root: Path, now: datetime | None = None) -> Path:
    """app.db into a new folder under dest_root (never overwrites one)."""
    folder = dest_root / f"day7-conversations-{(now or datetime.now()).strftime('%Y-%m-%d_%H%M%S')}"
    if folder.exists():
        raise MigrationError(f"{folder} exists already; a backup never overwrites one")
    folder.mkdir(parents=True)
    with contextlib.closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as src, \
            contextlib.closing(sqlite3.connect(folder / "app.db")) as dst:
        src.backup(dst)
    return folder


def _message(row: sqlite3.Row, columns: set[str]) -> pb.Message:
    """A source row as a message. Stat columns the source has are sent as they are; where it has none, the service
    reads them from the meta."""
    m = pb.Message(id=row["id"], conversation_id=row["conversation_id"], role=row["role"], content=row["content"],
                   meta_json=row["meta"] or "{}", created_at=row["created_at"])
    if row["tool"] is not None:
        m.tool = row["tool"]
    if "latency_ms" in columns and row["latency_ms"] is not None:
        m.latency_ms = row["latency_ms"]
    if "rewritten" in columns:
        m.rewritten = bool(row["rewritten"])
    if "sufficient" in columns and row["sufficient"] is not None:
        m.sufficient = bool(row["sufficient"])
    return m


def migrate(source: Path, address: str, backup_dest: Path | None = DEFAULT_BACKUP_DEST,
            request_id: str | None = None) -> Report:
    """Back up, copy, verify. Raises MigrationError when the source or the service can't be used; check
    report.ok for the verification."""
    if not source.is_file():
        raise MigrationError(f"no database at {source}")
    report = Report()
    if backup_dest is not None:
        report.backup = backup_source(source, backup_dest)
    metadata = ((REQUEST_ID_HEADER, request_id or f"migrate-{datetime.now():%H%M%S}"),)
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"conversations", "messages"} <= tables:
            raise MigrationError(f"{source} has no conversations/messages tables: nothing to migrate")
        columns = {r["name"] for r in src.execute("PRAGMA table_info(messages)")}
        convs = src.execute("SELECT * FROM conversations ORDER BY created_at, rowid").fetchall()
        report.conversations = len(convs)
        report.messages = src.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        orphans = src.execute("SELECT COUNT(*) FROM messages WHERE conversation_id NOT IN "
                              "(SELECT id FROM conversations)").fetchone()[0]
        report.skipped_messages = orphans

        with grpc.insecure_channel(address) as channel:
            stub = pb_grpc.ConversationServiceStub(channel)
            try:
                for c in convs:
                    conv = pb.Conversation(id=c["id"], user_id=c["user_id"], title=c["title"],
                                           created_at=c["created_at"], updated_at=c["updated_at"])
                    rows = src.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid",
                                       (c["id"],)).fetchall()
                    for start in range(0, max(len(rows), 1), BATCH):
                        reply = stub.ImportConversation(
                            pb.ImportConversationRequest(conversation=conv,
                                                         messages=[_message(r, columns) for r in rows[start:start + BATCH]]),
                            timeout=TIMEOUT_SECONDS, metadata=metadata)
                        report.conversations_created += int(reply.conversation_created)
                        report.messages_created += reply.messages_created
                _verify(report, src, stub, convs, orphans, metadata)
            except grpc.RpcError as e:
                raise MigrationError(f"the Conversation service at {address} failed: {e.code().name} "
                                     f"{e.details()}") from e
        report.left_behind = [
            f"app.db table conversations ({report.conversations} rows)",
            f"app.db table messages ({report.messages} rows)",
        ]
    finally:
        src.close()
    return report


def _verify(report: Report, src: sqlite3.Connection, stub, convs: list, orphans: int, metadata: tuple) -> None:
    wrong = []
    in_service = 0
    for c in convs:
        want = src.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (c["id"],)).fetchone()[0]
        try:
            held = stub.GetConversation(pb.ConversationRef(user_id=c["user_id"], conversation_id=c["id"]),
                                        timeout=TIMEOUT_SECONDS, metadata=metadata)
        except grpc.RpcError as e:
            wrong.append(f"{c['id']}: {e.code().name}")
            continue
        got = len(stub.ListMessages(pb.ListMessagesRequest(conversation_id=c["id"]),
                                    timeout=TIMEOUT_SECONDS, metadata=metadata).messages)
        in_service += got
        if got != want or held.title != c["title"] or held.created_at != c["created_at"]:
            wrong.append(f"{c['id']}: {got}/{want} messages")
    report.check(f"every conversation is in the service with its messages ({len(convs)} conversations)", not wrong,
                 ", ".join(wrong[:5]))
    expected_messages = report.messages - orphans
    report.check(f"message total: {in_service}", in_service == expected_messages, f"expected {expected_messages}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, help="the orchestrator's database (default: DATABASE_PATH / settings)")
    parser.add_argument("--address", default=config.service_address("conversation"), help="the Conversation service")
    parser.add_argument("--dest", type=Path, default=DEFAULT_BACKUP_DEST, help="the folder the backup goes under")
    args = parser.parse_args()
    source = args.source
    if source is None:
        from backend.config.settings import settings
        source = Path(settings.database_path)
    try:
        report = migrate(source, args.address, args.dest)
    except MigrationError as e:
        print(f"migration stopped: {e}", file=sys.stderr)
        return 1
    report.print()
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
