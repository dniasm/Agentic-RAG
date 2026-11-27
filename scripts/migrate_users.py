"""Copy accounts, logins and usage counts from the orchestrator's app.db into the User service.

    python scripts/migrate_users.py                    # the service must be running (python scripts/run_all.py)
    python scripts/migrate_users.py --source <app.db> --address localhost:50061 --dest <backup folder>

What moves: `users` (day7's table and the library's are one table in app.db, so there is nothing to merge by id or
name beyond what the service itself ignores), `auth_tokens` (only their SHA-256, as stored: people stay logged in),
`embed_usage` (passages embedded per account per day) and `api_usage` (requests per provider key per hour).

In order: (1) back up app.db into <dest>/day7-users-<time>/ with SQLite's backup API (a consistent copy while the
backend runs); (2) send everything to the service, which keeps ids and timestamps and ignores rows it already has (a
user it holds under the same id, or another id with the same name without regard to case, is left as it is and the
name clash is reported), so running it again copies nothing and is safe after a crash; (3) verify: every source user
is in the service under its id and name, and sending the tokens and usage rows once more creates nothing, which means
each is there. Exit 0 only when every check passed.

The source tables are never touched: they stay in app.db, now unused (the orchestrator no longer reads or creates
them). Drop them by hand once sign-in has been seen working in the app.
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

from agentic_rag_proto import user_pb2 as pb  # noqa: E402
from agentic_rag_proto import user_pb2_grpc as pb_grpc  # noqa: E402
from services.common import config  # noqa: E402
from services.common.log import REQUEST_ID_HEADER  # noqa: E402

BATCH = 500
TIMEOUT_SECONDS = 60.0
DEFAULT_BACKUP_DEST = Path(r"D:\onboarding_tasks\backups")
API_COLUMNS = ("hour", "provider", "key_index", "model", "calls", "tokens", "limited")


class MigrationError(RuntimeError):
    pass


@dataclass
class Report:
    users: int = 0                  # in the source
    tokens: int = 0
    embed_usage: int = 0
    api_usage: int = 0
    created: dict = field(default_factory=lambda: {"users": 0, "tokens": 0, "embed_usage": 0, "api_usage": 0})
    skipped: list[str] = field(default_factory=list)
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
        print(f"  source: {self.users} users, {self.tokens} logins, {self.embed_usage} embed-usage rows, "
              f"{self.api_usage} api-usage rows")
        print("  copied now: " + ", ".join(f"{n} {k}" for k, n in self.created.items()))
        for line in self.skipped:
            print(f"  not copied: {line}")
        for name, ok, detail in self.checks:
            print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
        for line in self.left_behind:
            print(f"  left behind: {line}")
        print("MIGRATION VERIFIED" if self.ok else "MIGRATION FAILED")


def backup_source(source: Path, dest_root: Path, now: datetime | None = None) -> Path:
    """app.db into a new folder under dest_root (never overwrites one)."""
    folder = dest_root / f"day7-users-{(now or datetime.now()).strftime('%Y-%m-%d_%H%M%S')}"
    if folder.exists():
        raise MigrationError(f"{folder} exists already; a backup never overwrites one")
    folder.mkdir(parents=True)
    with contextlib.closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as src, \
            contextlib.closing(sqlite3.connect(folder / "app.db")) as dst:
        src.backup(dst)
    return folder


def _rows(src: sqlite3.Connection, tables: set[str], table: str) -> list[sqlite3.Row]:
    return src.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() if table in tables else []


def _batches(items: list):
    for start in range(0, len(items), BATCH):
        yield items[start:start + BATCH]


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
        if "users" not in tables:
            raise MigrationError(f"{source} has no users table: nothing to migrate")
        users = [pb.ImportedUser(id=r["id"], username=r["username"], password_hash=r["password_hash"],
                                 created_at=r["created_at"]) for r in _rows(src, tables, "users")]
        tokens = [pb.ImportedToken(token_hash=r["token_hash"], user_id=r["user_id"], created_at=r["created_at"],
                                   expires_at=r["expires_at"]) for r in _rows(src, tables, "auth_tokens")]
        embed = [pb.ImportedEmbedUsage(day=r["day"], user_id=r["user_id"], chunks=r["chunks"])
                 for r in _rows(src, tables, "embed_usage")]
        api = [pb.ApiUsageRow(**{c: r[c] for c in API_COLUMNS}) for r in _rows(src, tables, "api_usage")]
        report.users, report.tokens, report.embed_usage, report.api_usage = len(users), len(tokens), len(embed), len(api)

        with grpc.insecure_channel(address) as channel:
            stub = pb_grpc.UserServiceStub(channel)

            def send(**kwargs) -> pb.ImportUsersReply:
                return stub.ImportUsers(pb.ImportUsersRequest(**kwargs), timeout=TIMEOUT_SECONDS, metadata=metadata)

            try:
                # users first: a login is only taken for an account the service holds
                for batch in _batches(users):
                    reply = send(users=batch)
                    report.created["users"] += reply.users_created
                    report.skipped.extend(reply.skipped_users)
                for key, field_name, items in (("tokens", "tokens", tokens), ("embed_usage", "embed_usage", embed),
                                               ("api_usage", "api_usage", api)):
                    for batch in _batches(items):
                        reply = send(**{field_name: batch})
                        report.created[key] += getattr(reply, f"{key}_created")
                _verify(report, stub, users, tokens, embed, api, send, metadata)
            except grpc.RpcError as e:
                raise MigrationError(f"the User service at {address} failed: {e.code().name} "
                                     f"{e.details()}") from e
        report.left_behind = [f"app.db table {t} ({len(rows)} rows)" for t, rows in
                              (("users", users), ("auth_tokens", tokens), ("embed_usage", embed), ("api_usage", api))
                              if t in tables]
    finally:
        src.close()
    return report


def _verify(report: Report, stub, users: list, tokens: list, embed: list, api: list, send, metadata: tuple) -> None:
    held = {u.id: u.username for u in stub.ListUsers(pb.ListUsersRequest(), timeout=TIMEOUT_SECONDS,
                                                     metadata=metadata).users}
    wrong = [f"{u.id}" for u in users if held.get(u.id) != u.username]
    report.check(f"every account is in the service under its id and name ({len(users)} accounts)", not wrong,
                 ", ".join(wrong[:5]) + (" (see the name clashes above)" if report.skipped else ""))
    # Sending a row the service has creates nothing: so a repeat that creates nothing proves each row is there.
    missing = {"logins": 0, "embed-usage rows": 0, "api-usage rows": 0}
    for batch in _batches(tokens):
        missing["logins"] += send(tokens=[t for t in batch if t.user_id in held]).tokens_created
    for batch in _batches(embed):
        missing["embed-usage rows"] += send(embed_usage=batch).embed_usage_created
    for batch in _batches(api):
        missing["api-usage rows"] += send(api_usage=batch).api_usage_created
    for name, n in missing.items():
        report.check(f"every {name[:-1] if name.endswith('s') else name} is in the service", n == 0,
                     f"{n} were still missing" if n else "")
    orphans = sum(1 for t in tokens if t.user_id not in held)
    if orphans:
        report.skipped.append(f"{orphans} logins of accounts the service doesn't hold")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, help="the orchestrator's database (default: DATABASE_PATH / settings)")
    parser.add_argument("--address", default=config.service_address("user"), help="the User service")
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
