"""Copy the site's data to a new timestamped folder, so invited users' documents survive a mistake.

    python -m backend.scripts.backup                    # into D:\\onboarding_tasks\\backups\\day7-<time>
    python -m backend.scripts.backup --dest <folder>    # into <folder>\\day7-<time>

What is copied:
  * app.db (users, documents, chats, edits): through SQLite's backup API, so a consistent copy even while the backend
    is running;
  * the stored documents (data/library) and their edit history (data/versions);
  * the search index (the Qdrant folder), but only while the backend is stopped. Its files change under a running
    backend, so a copy then could be torn; the backup says it has no search index instead (FULL vs PARTIAL).

Never overwrites a folder and never deletes old backups: pruning them is the owner's call. The Qdrant folder is only
checked for its lock (the backend holds it); the store is never opened and the lock file never removed.

The copy holds documents and password hashes: keep it on this machine or a drive of the owner's, and don't upload it
anywhere without their say. A copy on the same disk doesn't survive that disk failing.

Restoring is manual, because it overwrites live data (docs/DOCUMENTATION.md, "Backups"): stop the backend, copy
app.db, library/ and versions/ back into backend/data/ and, from a FULL backup, qdrant_data/ back into
backend/vector_db/; then start the backend. From a PARTIAL backup, re-index afterwards (it costs embedding quota).
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


def backup(dest_root: Path, db_path: Path, data_dir: Path, qdrant_dir: Path, now: datetime | None = None) -> dict:
    """One backup into a new folder under dest_root. Returns what was copied: {folder, mode, copied, skipped}."""
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    folder = dest_root / f"day7-{stamp}"
    if folder.exists():
        raise FileExistsError(f"{folder} exists already; a backup never overwrites one")
    folder.mkdir(parents=True)
    copied, skipped = [], []

    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(folder / "app.db")
    try:
        src.backup(dst)   # consistent even while the backend writes
    finally:
        dst.close()
        src.close()
    copied.append("app.db")

    for name in ("library", "versions"):
        if (data_dir / name).exists():
            shutil.copytree(data_dir / name, folder / name)
            copied.append(name)
        else:
            skipped.append(f"{name} (not there)")

    with qdrant_unlocked(qdrant_dir) as stopped:
        if stopped:
            shutil.copytree(qdrant_dir, folder / "qdrant_data", ignore=shutil.ignore_patterns(".lock"))
            copied.append("qdrant_data")
        else:
            skipped.append("qdrant_data (the backend is running: stop it for a full backup)")
    mode = "FULL" if "qdrant_data" in copied else "PARTIAL (no search index)"
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
    result = backup(args.dest, Path(settings.database_path), config.DATA_DIR, Path(settings.qdrant_path))
    print(f"{result['mode']} backup in {result['folder']}")
    print("copied:", ", ".join(result["copied"]))
    for s in result["skipped"]:
        print("skipped:", s)
    print("It holds documents and password hashes: keep it private. A copy on this disk won't survive the disk failing.")


if __name__ == "__main__":
    main()
