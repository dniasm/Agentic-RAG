"""Copy the site's data to a new timestamped folder, so invited users' documents survive a mistake.

    python -m backend.scripts.backup                    # into D:\\onboarding_tasks\\backups\\day7-<time>
    python -m backend.scripts.backup --dest <folder>    # into <folder>\\day7-<time>

What is copied:
  * app.db (users, documents, chats, edits): through SQLite's backup API, so a consistent copy even while the backend
    is running;
  * the stored documents (data/library) and their edit history (data/versions);
  * the services' data (SERVICES_DATA_DIR, backend/data/services by default): each service's SQLite file (user,
    conversation, memory, document .db) through the same backup API, and their Qdrant folders (document_qdrant,
    the search index; memory_qdrant), but only while the owning service is stopped. Their files change under a running
    service, so a copy then could be torn; the backup says it has no search index instead (FULL vs PARTIAL).

Never overwrites a folder and never deletes old backups: pruning them is the owner's call. A Qdrant folder is only
checked for its lock (its service holds it); the store is never opened and the lock file never removed.

The copy holds documents and password hashes: keep it on this machine or a drive of the owner's, and don't upload it
anywhere without their say. A copy on the same disk doesn't survive that disk failing.

Restoring is manual, because it overwrites live data (docs/DOCUMENTATION.md, "Backups"): stop the backend, copy
app.db, library/ and versions/ back into backend/data/ and services/ back into backend/data/services/ (or
SERVICES_DATA_DIR); then start the backend and the services. From a PARTIAL backup, re-index afterwards (it costs embedding quota).
"""
import argparse
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DEFAULT_DEST = Path(r"D:\onboarding_tasks\backups")


@contextmanager
def qdrant_unlocked(qdrant_dir: Path):
    """Yields True, holding Qdrant's lock, when no backend holds it (so the folder is still while it is copied); yields
    False when a backend holds it. The store itself is never opened."""
    lock = qdrant_dir / ".lock"
    if not lock.exists():
        yield qdrant_dir.exists()   # no lock file: nothing has opened the folder
        return
    import portalocker
    with open(lock, "r+") as f:
        try:
            portalocker.lock(f, portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING)
        except portalocker.exceptions.LockException:
            yield False
            return
        try:
            yield True
        finally:
            portalocker.unlock(f)


def _copy_database(src_path: Path, dst_path: Path) -> None:
    src = sqlite3.connect(f"file:{src_path.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)   # consistent even while the owner writes
    finally:
        dst.close()
        src.close()


def backup(dest_root: Path, db_path: Path, data_dir: Path, services_dir: Path, now: datetime | None = None) -> dict:
    """One backup into a new folder under dest_root. Returns what was copied: {folder, mode, copied, skipped}.
    services_dir is the services' data folder (SERVICES_DATA_DIR): each service's <name>.db and its Qdrant folders
    (document_qdrant, memory_qdrant), copied to services/ in the backup."""
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    folder = dest_root / f"day7-{stamp}"
    if folder.exists():
        raise FileExistsError(f"{folder} exists already; a backup never overwrites one")
    folder.mkdir(parents=True)
    copied, skipped = [], []

    _copy_database(db_path, folder / "app.db")
    copied.append("app.db")

    for name in ("library", "versions"):
        if (data_dir / name).exists():
            shutil.copytree(data_dir / name, folder / name)
            copied.append(name)
        else:
            skipped.append(f"{name} (not there)")

    (folder / "services").mkdir()
    for db in sorted(services_dir.glob("*.db")) if services_dir.exists() else []:
        _copy_database(db, folder / "services" / db.name)
        copied.append(f"services/{db.name}")
    if not any(c.startswith("services/") for c in copied):
        skipped.append("services/*.db (none found)")

    indexes = sorted(d for d in services_dir.glob("*_qdrant") if d.is_dir()) if services_dir.exists() else []
    for qdrant_dir in indexes:
        with qdrant_unlocked(qdrant_dir) as stopped:
            if stopped:
                shutil.copytree(qdrant_dir, folder / "services" / qdrant_dir.name, ignore=shutil.ignore_patterns(".lock"))
                copied.append(f"services/{qdrant_dir.name}")
            else:
                skipped.append(f"services/{qdrant_dir.name} (its service is running: stop it for a full backup)")
    mode = "FULL" if indexes and not any("_qdrant" in x for x in skipped) else "PARTIAL (no search index)"
    (folder / "BACKUP.txt").write_text(
        f"day7 backup, {stamp}\nmode: {mode}\ncopied: {', '.join(copied)}\n"
        + (f"skipped: {'; '.join(skipped)}\n" if skipped else "")
        + "Restore: see docs/DOCUMENTATION.md, Backups. Contains documents and password hashes: keep it private.\n",
        encoding="utf-8")
    return {"folder": folder, "mode": mode, "copied": copied, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="the folder the backup goes under")
    args = parser.parse_args()
    from backend.config.settings import settings
    from backend.library import config
    from services.common import config as services_config
    result = backup(args.dest, Path(settings.database_path), config.DATA_DIR, services_config.data_dir())
    print(f"{result['mode']} backup in {result['folder']}")
    print("copied:", ", ".join(result["copied"]))
    for s in result["skipped"]:
        print("skipped:", s)
    print("It holds documents and password hashes: keep it private. A copy on this disk won't survive the disk failing.")


if __name__ == "__main__":
    main()
