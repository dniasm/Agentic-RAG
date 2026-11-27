"""A service's own SQLite database: one file per service under the services data dir (see services.common.config).

    db = ServiceDB("user", SCHEMA)       # <data dir>/user.db, created with the schema on first use
    with db.tx() as c: c.execute(...)    # serialized transaction; commits on success, rolls back on error
"""
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from services.common import config


class ServiceDB:
    def __init__(self, name: str, schema: str, path: str | Path | None = None, migrate=None):
        self.name = name
        self.schema = schema
        self.migrate = migrate   # migrate(conn): run after the schema, for columns added to tables that already exist
        self.path = Path(path) if path else config.data_dir() / f"{name}.db"
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    def connection(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = NORMAL")
                conn.executescript(self.schema)
                if self.migrate:
                    self.migrate(conn)
                    conn.commit()
                self._conn = conn
            return self._conn

    @contextmanager
    def tx(self):
        with self._lock:
            conn = self.connection()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
