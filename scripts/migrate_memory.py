"""Copy remembered facts and episodes from the orchestrator's data into the Memory service.

    python scripts/migrate_memory.py       # STOP THE BACKEND FIRST; the Memory service must be running
    python scripts/migrate_memory.py --source <app.db> --qdrant <folder> --address localhost:50063 --dest <backup folder>

The orchestrator's embedded Qdrant folder can be opened by one process only, and this script reads it directly: stop the
backend (uvicorn) before running it, or it refuses ("already in use"). The Memory service has its own folder, so it can
keep running. (Needs only the service's address; run_all.py without the orchestrator is enough.)

What moves: (1) facts: app.db's `user_facts` and, when the old one-time conversion (`user_facts_v1` in `migrations`)
never ran on this database, the older `user_memory` table (one row per key, collections as JSON lists) turned into one
fact per value; (2) episodes: every point of the memory collection (settings.voyage_memory_collection) with its id,
vector and payload, so nothing is embedded again.

In order: (1) back up app.db (SQLite's backup API) and copy the Qdrant folder into <dest>/day7-memory-<time>/; (2) send
everything to the service in batches, which keeps ids, vectors and timestamps and ignores what it already has, so
running it again copies nothing and is safe after a crash; (3) verify: sending every fact and episode once more creates
nothing (each is there), and the service holds at least as many as the source. Exit 0 only when every check passed.

The sources are never touched: user_facts / user_memory stay in app.db and the collection stays in the orchestrator's
Qdrant, now unused (the orchestrator no longer reads or creates them). Delete them by hand once memory has been seen
working in the app.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import grpc

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agentic_rag_proto import memory_pb2 as pb  # noqa: E402
from agentic_rag_proto import memory_pb2_grpc as pb_grpc  # noqa: E402
from services.common import config  # noqa: E402
from services.common.log import REQUEST_ID_HEADER  # noqa: E402

BATCH = 200   # episodes carry a 1024-number vector each: keep a request well under gRPC's 4 MB message limit
TIMEOUT_SECONDS = 120.0
DEFAULT_BACKUP_DEST = Path(r"D:\onboarding_tasks\backups")


class MigrationError(RuntimeError):
    pass


@dataclass
class Report:
    facts: int = 0                  # in the source
    episodes: int = 0
    created: dict = field(default_factory=lambda: {"facts": 0, "episodes": 0})
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
            print(f"  backup of app.db and the Qdrant folder: {self.backup}")
        print(f"  source: {self.facts} facts, {self.episodes} episodes")
        print("  copied now: " + ", ".join(f"{n} {k}" for k, n in self.created.items()))
        for name, ok, detail in self.checks:
            print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
        for line in self.left_behind:
            print(f"  left behind: {line}")
        print("MIGRATION VERIFIED" if self.ok else "MIGRATION FAILED")


def backup_source(source: Path, qdrant: Path | None, dest_root: Path, now: datetime | None = None) -> Path:
    """app.db and the Qdrant folder into a new folder under dest_root (never overwrites one)."""
    folder = dest_root / f"day7-memory-{(now or datetime.now()).strftime('%Y-%m-%d_%H%M%S')}"
    if folder.exists():
        raise MigrationError(f"{folder} exists already; a backup never overwrites one")
    folder.mkdir(parents=True)
    with contextlib.closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as src, \
            contextlib.closing(sqlite3.connect(folder / "app.db")) as dst:
        src.backup(dst)
    if qdrant is not None and qdrant.is_dir():
        shutil.copytree(qdrant, folder / "qdrant_data", ignore=shutil.ignore_patterns(".lock"))
    return folder


def decode_values(raw: str) -> list[str]:
    """Old user_memory values: a JSON list of strings for collections, plain text otherwise."""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [v.strip() for v in parsed if isinstance(v, str) and v.strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    return [raw.strip()] if raw and raw.strip() else []


def read_facts(src: sqlite3.Connection) -> list[pb.ImportedFact]:
    tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    facts = [pb.ImportedFact(id=r["id"], user_id=r["user_id"], memory_key=r["memory_key"], value=r["value"],
                             created_at=r["created_at"], updated_at=r["updated_at"])
             for r in src.execute("SELECT * FROM user_facts ORDER BY created_at, rowid")] if "user_facts" in tables else []
    converted = "migrations" in tables and src.execute("SELECT 1 FROM migrations WHERE name = 'user_facts_v1'").fetchone()
    if "user_memory" in tables and not converted:
        # the one-time conversion app.db used to do at start: a fresh id per value (deterministic, so a repeat is a no-op)
        for r in src.execute("SELECT user_id, memory_key, memory_value, created_at, updated_at FROM user_memory"):
            for value in decode_values(r["memory_value"]):
                key = f"{r['user_id']}\0{r['memory_key']}\0{value.lower()}"
                facts.append(pb.ImportedFact(id=uuid.uuid5(uuid.NAMESPACE_OID, key).hex, user_id=r["user_id"],
                                             memory_key=r["memory_key"], value=value, created_at=r["created_at"],
                                             updated_at=r["updated_at"]))
    return facts


def read_episodes(qdrant: Path, collection: str) -> list[pb.ImportedEpisode]:
    from qdrant_client import QdrantClient
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
            points, offset = client.scroll(collection, limit=500, offset=offset, with_payload=True, with_vectors=True)
            for p in points:
                payload = p.payload or {}
                episode = pb.ImportedEpisode(id=str(p.id), vector=list(p.vector), user_id=payload.get("user_id", ""),
                                             text=payload.get("text", ""), kind=payload.get("kind") or "episodic",
                                             updated_at=payload.get("updated_at") or "")
                if payload.get("conversation_id"):
                    episode.conversation_id = payload["conversation_id"]
                out.append(episode)
            if offset is None:
                return out
    finally:
        client.close()


def _batches(items: list):
    for start in range(0, len(items), BATCH):
        yield items[start:start + BATCH]


def migrate(source: Path, qdrant: Path, address: str, collection: str | None = None,
            backup_dest: Path | None = DEFAULT_BACKUP_DEST, request_id: str | None = None) -> Report:
    """Back up, copy, verify. Raises MigrationError when the source or the service can't be used; check
    report.ok for the verification."""
    if not source.is_file():
        raise MigrationError(f"no database at {source}")
    if collection is None:
        from backend.config.settings import settings
        collection = settings.voyage_memory_collection
    report = Report()
    if backup_dest is not None:
        report.backup = backup_source(source, qdrant, backup_dest)
    metadata = ((REQUEST_ID_HEADER, request_id or f"migrate-{datetime.now():%H%M%S}"),)
    with contextlib.closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as src:
        src.row_factory = sqlite3.Row
        facts = read_facts(src)
    episodes = read_episodes(qdrant, collection) if qdrant.is_dir() else []
    report.facts, report.episodes = len(facts), len(episodes)

    with grpc.insecure_channel(address, options=[("grpc.max_send_message_length", 16 * 1024 * 1024)]) as channel:
        stub = pb_grpc.MemoryServiceStub(channel)

        def send_facts(batch) -> int:
            return stub.ImportFacts(pb.ImportFactsRequest(facts=batch), timeout=TIMEOUT_SECONDS, metadata=metadata).created

        def send_episodes(batch) -> int:
            return stub.ImportEpisodes(pb.ImportEpisodesRequest(episodes=batch), timeout=TIMEOUT_SECONDS,
                                       metadata=metadata).created

        try:
            for batch in _batches(facts):
                report.created["facts"] += send_facts(batch)
            for batch in _batches(episodes):
                report.created["episodes"] += send_episodes(batch)
            # Sending a row the service has creates nothing: so a repeat that creates nothing proves each row is there.
            missing_facts = sum(send_facts(b) for b in _batches(facts))
            missing_episodes = sum(send_episodes(b) for b in _batches(episodes))
            held = stub.CountMemory(pb.CountMemoryRequest(), timeout=TIMEOUT_SECONDS, metadata=metadata)
        except grpc.RpcError as e:
            raise MigrationError(f"the Memory service at {address} failed: {e.code().name} {e.details()}") from e
    report.check(f"every fact is in the service ({len(facts)} facts)", missing_facts == 0,
                 f"{missing_facts} were still missing" if missing_facts else "")
    report.check(f"every episode is in the service, with its vector ({len(episodes)} episodes)", missing_episodes == 0,
                 f"{missing_episodes} were still missing" if missing_episodes else "")
    report.check("the service holds at least as many as the source",
                 held.facts >= len(facts) and held.episodes >= len(episodes),
                 f"service has {held.facts} facts, {held.episodes} episodes")
    report.left_behind = [f"app.db rows ({len(facts)} facts)", f"Qdrant collection {collection} ({len(episodes)} points)"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, help="the orchestrator's database (default: DATABASE_PATH / settings)")
    parser.add_argument("--qdrant", type=Path, help="the orchestrator's embedded Qdrant folder (default: QDRANT_PATH / settings)")
    parser.add_argument("--address", default=config.service_address("memory"), help="the Memory service")
    parser.add_argument("--dest", type=Path, default=DEFAULT_BACKUP_DEST, help="the folder the backup goes under")
    args = parser.parse_args()
    from backend.config.settings import settings
    source = args.source or Path(settings.database_path)
    qdrant = args.qdrant or Path(settings.qdrant_path)
    print("Reading the orchestrator's Qdrant folder directly: the backend must be stopped.")
    try:
        report = migrate(source, qdrant, args.address, backup_dest=args.dest)
    except MigrationError as e:
        print(f"migration stopped: {e}", file=sys.stderr)
        return 1
    report.print()
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
