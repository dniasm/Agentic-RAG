"""Passwords and login tokens: the part of accounts that must never leave the User service.

  * Passwords are stored as salted PBKDF2-SHA256 hashes (600,000 rounds), never as text. Hashes made by day7's own
    sign-up before the merge are bcrypt (over a SHA-256 of the password); both are checked, neither is re-hashed.
  * A login is a random token (the browser's cookie); the database keeps only its SHA-256, so a copied database
    holds no token anyone can use. Logins last LOGIN_DAYS.
"""
import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

ROUNDS = 600_000
LOGIN_DAYS = 30
USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD = 8


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ROUNDS)
    return f"pbkdf2_sha256${ROUNDS}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    """Both formats in the users table, told apart by prefix."""
    if stored.startswith("$2"):
        return _check_bcrypt(password, stored)
    try:
        _, rounds, salt, digest = stored.split("$")
        mine = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(mine.hex(), digest)


def _check_bcrypt(password: str, stored: str) -> bool:
    import bcrypt
    # day7 pre-hashed with SHA-256 so that passphrases past bcrypt's 72 bytes still count
    prehashed = base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())
    try:
        return bcrypt.checkpw(prehashed, stored.encode())
    except ValueError:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> tuple[str, str, str]:
    """(the token for the cookie, its hash for the database, when it expires)."""
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=LOGIN_DAYS)).isoformat(timespec="seconds")
    return token, token_hash(token), expires


def validate_new(username: str, password: str) -> str:
    """The trimmed username, or ValueError with what to tell the person."""
    username = (username or "").strip()
    if not USERNAME.match(username):
        raise ValueError("A username is 3-32 letters, digits, dots, dashes or underscores.")
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"Use a password of at least {MIN_PASSWORD} characters.")
    return username
