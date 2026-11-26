"""Users table (accounts made by the app live in backend/library/catalog.py, see auth.py)."""
import uuid

from backend.models.connection import _now, _one, tx

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def create_user(username: str, password_hash: str) -> dict:
    """For tests that use this module alone: the app makes accounts with backend/library/auth.py (catalog)."""
    user = {"id": uuid.uuid4().hex, "username": username, "password_hash": password_hash, "created_at": _now()}
    with tx() as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, created_at) "
            "VALUES (:id, :username, :password_hash, :created_at)",
            user,
        )
    return user


def get_user(user_id: str) -> dict | None:
    with tx() as c:
        return _one(c.execute("SELECT * FROM users WHERE id = ?", (user_id,)))

