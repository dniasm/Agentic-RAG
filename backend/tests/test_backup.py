"""The backup script (backend/scripts/backup.py), on temporary folders only: a consistent copy of the database even
while it is written, the documents and their versions, and the search index only while no backend holds its lock."""
import sqlite3
from datetime import datetime

import pytest

from backend.scripts import backup

NOW = datetime(2026, 11, 17, 3, 0, 0)


@pytest.fixture
def site(tmp_path):
    db = tmp_path / "app.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE documents (id TEXT, title TEXT)")
        c.execute("INSERT INTO documents VALUES ('d1', 'Resume')")
    data = tmp_path / "data"
    (data / "library" / "u1").mkdir(parents=True)
    (data / "library" / "u1" / "Resume.pdf").write_bytes(b"%PDF-1.4 resume")
    (data / "versions" / "d1").mkdir(parents=True)
    (data / "versions" / "d1" / "001.pdf").write_bytes(b"%PDF-1.4 before")
    qdrant = tmp_path / "qdrant_data"
    (qdrant / "collection").mkdir(parents=True)
    (qdrant / "collection" / "segment.bin").write_bytes(b"vectors")
    (qdrant / ".lock").write_text("tmp lock file")
    return tmp_path, db, data, qdrant


def test_a_full_backup_while_the_backend_is_stopped(site):
    root, db, data, qdrant = site
    out = backup.backup(root / "backups", db, data, qdrant, now=NOW)
    folder = out["folder"]
    assert out["mode"] == "FULL" and folder.name == "day7-2026-11-17_030000"
    with sqlite3.connect(folder / "app.db") as c:
        assert c.execute("SELECT title FROM documents").fetchall() == [("Resume",)]
    assert (folder / "library" / "u1" / "Resume.pdf").read_bytes() == b"%PDF-1.4 resume"
    assert (folder / "versions" / "d1" / "001.pdf").exists()
    assert (folder / "qdrant_data" / "collection" / "segment.bin").read_bytes() == b"vectors"
    assert not (folder / "qdrant_data" / ".lock").exists()          # the lock isn't copied
    assert (qdrant / ".lock").exists()                              # nor removed from the live folder
    assert "FULL" in (folder / "BACKUP.txt").read_text(encoding="utf-8")


def test_a_running_backend_gets_a_partial_backup_without_the_index(site):
    import portalocker
    root, db, data, qdrant = site
    with open(qdrant / ".lock", "r+") as held:   # what a running backend does
        portalocker.lock(held, portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING)
        try:
            out = backup.backup(root / "backups", db, data, qdrant, now=NOW)
        finally:
            portalocker.unlock(held)
    assert out["mode"].startswith("PARTIAL") and "qdrant_data" not in out["copied"]
    assert not (out["folder"] / "qdrant_data").exists()
    assert any("backend is running" in s for s in out["skipped"])
    assert (out["folder"] / "app.db").exists() and (out["folder"] / "library").exists()


def test_the_database_copy_is_consistent_while_it_is_being_written(site):
    root, db, data, qdrant = site
    writer = sqlite3.connect(db)
    writer.execute("BEGIN")
    writer.execute("INSERT INTO documents VALUES ('d2', 'Uncommitted')")   # a write in progress
    try:
        out = backup.backup(root / "backups", db, data, qdrant, now=NOW)
    finally:
        writer.rollback()
        writer.close()
    with sqlite3.connect(out["folder"] / "app.db") as c:
        assert c.execute("SELECT id FROM documents").fetchall() == [("d1",)]   # the committed state only


def test_a_backup_never_overwrites_another(site):
    root, db, data, qdrant = site
    backup.backup(root / "backups", db, data, qdrant, now=NOW)
    with pytest.raises(FileExistsError):
        backup.backup(root / "backups", db, data, qdrant, now=NOW)
