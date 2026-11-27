"""User service SQL: accounts, login tokens, and the two usage counters (passages embedded per account per day, and
API requests per provider key per hour). Plain sync functions; the servicer runs them on a thread.

user.db is this service's own file (services.common.sqlite_db): the one users table of the whole app.
"""
import sqlite3
import uuid
from datetime import datetime, timezone

from services.common.sqlite_db import ServiceDB
from services.user import accounts

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,      -- pbkdf2_sha256$iterations$salt$hash, or day7's bcrypt (services/user/accounts.py)
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash TEXT PRIMARY KEY,      -- sha256 of the login cookie: a stolen database holds no usable tokens
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON auth_tokens(user_id);
CREATE TABLE IF NOT EXISTS embed_usage (
    day TEXT NOT NULL,
    user_id TEXT NOT NULL,
    chunks INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id)
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,   -- the refund token
    user_id TEXT NOT NULL,
    sent_at REAL NOT NULL                   -- wall-clock seconds (time.time): a monotonic clock doesn't survive a restart
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_user ON chat_messages(user_id, sent_at);
CREATE TABLE IF NOT EXISTS api_usage (
    hour TEXT NOT NULL,
    provider TEXT NOT NULL,
    key_index INTEGER NOT NULL,
    model TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER NOT NULL DEFAULT 0,
    limited INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, provider, key_index, model)
);
"""

API_COLUMNS = ("hour", "provider", "key_index", "model", "calls", "tokens", "limited")


class UsernameTaken(Exception):
    pass


class BadLogin(Exception):
    pass


def open_db(path=None) -> ServiceDB:
    return ServiceDB("user", SCHEMA, path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public(row) -> dict:
    return {"id": row["id"], "username": row["username"], "created_at": row["created_at"]}


def count_users(db: ServiceDB) -> int:
    with db.tx() as c:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def _add_token(c: sqlite3.Connection, user_id: str) -> str:
    token, hashed, expires = accounts.new_token()
    c.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (_now(),))
    c.execute("INSERT INTO auth_tokens (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
              (hashed, user_id, _now(), expires))
    return token


def sign_up(db: ServiceDB, username: str, password: str, password_hash: str | None = None) -> tuple[dict, str, bool]:
    """(user, token, first_account). ValueError for a bad shape; UsernameTaken. The hash is made by the caller
    (off the lock: it takes a moment) from the same password."""
    username = accounts.validate_new(username, password)
    password_hash = password_hash or accounts.hash_password(password)
    with db.tx() as c:
        first = c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        user_id = uuid.uuid4().hex[:12]
        try:
            c.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                      (user_id, username, password_hash, _now()))
        except sqlite3.IntegrityError as e:
            raise UsernameTaken(username) from e
        user = _public(c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        return user, _add_token(c, user_id), first


def log_in(db: ServiceDB, username: str, password: str) -> tuple[dict, str]:
    username = (username or "").strip()
    with db.tx() as c:
        row = c.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None or not accounts.check_password(password or "", row["password_hash"]):
        raise BadLogin()
    with db.tx() as c:
        return _public(row), _add_token(c, row["id"])


def log_out(db: ServiceDB, token: str) -> None:
    if token:
        with db.tx() as c:
            c.execute("DELETE FROM auth_tokens WHERE token_hash = ?", (accounts.token_hash(token),))


def user_for_token(db: ServiceDB, token: str) -> dict | None:
    if not token:
        return None
    with db.tx() as c:
        row = c.execute("SELECT u.* FROM auth_tokens t JOIN users u ON u.id = t.user_id "
                        "WHERE t.token_hash = ? AND t.expires_at > ?", (accounts.token_hash(token), _now())).fetchone()
    return _public(row) if row else None


def list_users(db: ServiceDB) -> list[dict]:
    with db.tx() as c:
        return [_public(r) for r in c.execute("SELECT * FROM users ORDER BY created_at, rowid")]


# ---------------------------------------------------------------- usage
def embed_used(db: ServiceDB, day: str, user_id: str | None) -> int:
    with db.tx() as c:
        if user_id is None:
            row = c.execute("SELECT COALESCE(SUM(chunks), 0) FROM embed_usage WHERE day = ?", (day,)).fetchone()
        else:
            row = c.execute("SELECT COALESCE(SUM(chunks), 0) FROM embed_usage WHERE day = ? AND user_id = ?",
                            (day, user_id)).fetchone()
    return int(row[0])


def add_embed_usage(db: ServiceDB, day: str, user_id: str, chunks: int) -> int:
    with db.tx() as c:
        if chunks > 0:
            c.execute("INSERT INTO embed_usage (day, user_id, chunks) VALUES (?, ?, ?) "
                      "ON CONFLICT(day, user_id) DO UPDATE SET chunks = chunks + excluded.chunks", (day, user_id, chunks))
        row = c.execute("SELECT COALESCE(SUM(chunks), 0) FROM embed_usage WHERE day = ? AND user_id = ?",
                        (day, user_id)).fetchone()
    return int(row[0])


def list_embed_usage(db: ServiceDB, day: str) -> list[dict]:
    with db.tx() as c:
        return [{"user_id": r[0], "chunks": r[1]} for r in
                c.execute("SELECT user_id, chunks FROM embed_usage WHERE day = ? ORDER BY user_id", (day,))]


def add_api_usage(db: ServiceDB, rows: list[dict]) -> None:
    with db.tx() as c:
        c.executemany("INSERT INTO api_usage (hour, provider, key_index, model, calls, tokens, limited) "
                      "VALUES (:hour, :provider, :key_index, :model, :calls, :tokens, :limited) "
                      "ON CONFLICT(hour, provider, key_index, model) DO UPDATE SET "
                      "calls = calls + excluded.calls, tokens = tokens + excluded.tokens, "
                      "limited = limited + excluded.limited", rows)


def list_api_usage(db: ServiceDB, since_hour: str) -> list[dict]:
    with db.tx() as c:
        found = c.execute("SELECT hour, provider, key_index, model, calls, tokens, limited FROM api_usage "
                          "WHERE hour >= ? ORDER BY hour, provider, key_index, model", (since_hour,)).fetchall()
    return [dict(zip(API_COLUMNS, r)) for r in found]


def calls_by_key(db: ServiceDB, provider: str, since_hour: str) -> dict[int, int]:
    with db.tx() as c:
        found = c.execute("SELECT key_index, SUM(calls) FROM api_usage WHERE provider = ? AND hour >= ? "
                          "GROUP BY key_index", (provider, since_hour)).fetchall()
    return {int(k): int(n) for k, n in found}


# ---------------------------------------------------------------- chat messages
KEEP_SECONDS = 86400   # rows older than the longest window asked for (and never less than a day) are pruned


def reserve_chat(db: ServiceDB, user_id: str, windows: list[tuple[int, int, bool]], now: float) -> tuple:
    """(token, 0, False) when a message was recorded, or (None, retry_after, daily) when a window is full. Check and
    insert are one transaction on the serialized connection."""
    active = [(s, n, d) for s, n, d in windows if n > 0]
    with db.tx() as c:
        longest = max([s for s, _n, _d in active] + [KEEP_SECONDS])
        c.execute("DELETE FROM chat_messages WHERE sent_at <= ?", (now - longest,))
        for seconds, allowed, daily in active:
            recent = [r[0] for r in c.execute(
                "SELECT sent_at FROM chat_messages WHERE user_id = ? AND sent_at > ? ORDER BY sent_at",
                (user_id, now - seconds))]
            if len(recent) >= allowed:
                oldest = recent[-allowed]   # the message whose aging out frees a place
                return None, max(1, int(seconds - (now - oldest)) + 1), daily
        cur = c.execute("INSERT INTO chat_messages (user_id, sent_at) VALUES (?, ?)", (user_id, now))
        return str(cur.lastrowid), 0, False


def refund_chat(db: ServiceDB, user_id: str, token: str) -> bool:
    if not token.isdigit():
        return False
    with db.tx() as c:
        return c.execute("DELETE FROM chat_messages WHERE id = ? AND user_id = ?", (int(token), user_id)).rowcount > 0


def chat_usage(db: ServiceDB, user_id: str | None, windows: list[tuple[int, int, bool]], now: float) -> dict:
    """{user_id: [(used, oldest_age or -1) per window]}; a given user_id always has an entry."""
    with db.tx() as c:
        if user_id is None:
            users = [r[0] for r in c.execute("SELECT DISTINCT user_id FROM chat_messages ORDER BY user_id")]
        else:
            users = [user_id]
        out = {}
        for user in users:
            times = [r[0] for r in c.execute("SELECT sent_at FROM chat_messages WHERE user_id = ? ORDER BY sent_at",
                                             (user,))]
            row = []
            for seconds, _n, _d in windows:
                recent = [t for t in times if now - t < seconds]
                row.append((len(recent), now - recent[0] if recent else -1.0))
            out[user] = row
        return out


# ---------------------------------------------------------------- import
def import_rows(db: ServiceDB, users: list[dict], tokens: list[dict], embed: list[dict], api: list[dict]) -> dict:
    """Copy rows the service doesn't have yet; what it has stays as it is (so a repeat copies nothing)."""
    out = {"users": 0, "tokens": 0, "embed": 0, "api": 0, "skipped": []}
    with db.tx() as c:
        for u in users:
            if c.execute("SELECT 1 FROM users WHERE id = ?", (u["id"],)).fetchone():
                continue
            if c.execute("SELECT 1 FROM users WHERE username = ?", (u["username"],)).fetchone():
                out["skipped"].append(f"{u['id']}: the name {u['username']!r} belongs to another account")
                continue
            c.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                      (u["id"], u["username"], u["password_hash"], u["created_at"]))
            out["users"] += 1
        for t in tokens:
            if not c.execute("SELECT 1 FROM users WHERE id = ?", (t["user_id"],)).fetchone():
                continue
            out["tokens"] += c.execute("INSERT OR IGNORE INTO auth_tokens (token_hash, user_id, created_at, expires_at) "
                                       "VALUES (?, ?, ?, ?)", (t["token_hash"], t["user_id"], t["created_at"],
                                                               t["expires_at"])).rowcount
        for e in embed:
            out["embed"] += c.execute("INSERT OR IGNORE INTO embed_usage (day, user_id, chunks) VALUES (?, ?, ?)",
                                      (e["day"], e["user_id"], e["chunks"])).rowcount
        for r in api:
            out["api"] += c.execute("INSERT OR IGNORE INTO api_usage (hour, provider, key_index, model, calls, tokens, "
                                    "limited) VALUES (:hour, :provider, :key_index, :model, :calls, :tokens, :limited)",
                                    r).rowcount
    return out
