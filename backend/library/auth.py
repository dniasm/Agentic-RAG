"""Accounts: sign-up, log-in, log-out.

  * Passwords are stored as salted PBKDF2-SHA256 hashes (600,000 rounds), never as text.
  * A login is a random token in an HttpOnly cookie; the catalog keeps only the token's SHA-256, so a copied database
    holds no token anyone can use. Logins last LOGIN_DAYS.
  * Wrong passwords are throttled: after MAX_FAILURES for a name from one address within FAILURE_WINDOW, that
    name waits there; after MAX_ADDRESS_FAILURES from one address, that address waits.
  * The first account can always be made, and the library from before accounts existed becomes its own. After that a
    sign-up needs the invite code (SIGNUP_CODE in .env): the site is public through the tunnel, and every
    account spends the owner's Groq, Gemini and Voyage allowances. With no code set, sign-ups are closed.
  * One login for the whole app (day7's chat and the library): backend/api/routes/auth.py serves these at /api/auth/*.
"""
import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from backend.library import catalog, config
from backend.library.logs import event

ROUNDS = 600_000
LOGIN_DAYS = 30
MAX_FAILURES, FAILURE_WINDOW = 5, 15 * 60   # wrong passwords for one name from one address
MAX_ADDRESS_FAILURES = 20                    # wrong passwords from one address, any names
COOKIE = "login"
USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD = 8

_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ROUNDS)
    return f"pbkdf2_sha256${ROUNDS}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    """Both formats in the users table: PBKDF2 (made here) and bcrypt (made by day7's sign-up before the merge),
    told apart by prefix. They are equal in strength, so neither is re-hashed."""
    if stored.startswith("$2"):
        return _check_bcrypt(password, stored)
    try:
        _, rounds, salt, digest = stored.split("$")
        mine = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(mine.hex(), digest)


def _check_bcrypt(password: str, stored: str) -> bool:
    import base64

    import bcrypt
    # day7 pre-hashed with SHA-256 so that passphrases past bcrypt's 72 bytes still count
    prehashed = base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())
    try:
        return bcrypt.checkpw(prehashed, stored.encode())
    except ValueError:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_login(user: dict) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=LOGIN_DAYS)).isoformat(timespec="seconds")
    catalog.add_token(user["id"], _token_hash(token), expires)
    return token


def _invite_code() -> str:
    return config.SIGNUP_CODE


def signups_open() -> str:
    """'first' (no account yet), 'invite' (a code is needed) or 'closed'."""
    if catalog.count_users() == 0:
        return "first"
    return "invite" if _invite_code() else "closed"


def sign_up(username: str, password: str, invite: str = "", address: str = "") -> tuple[dict, str]:
    """Returns (user, login token). The first account takes the library from before accounts existed."""
    username = (username or "").strip()
    if not USERNAME.match(username):
        raise AuthError("A username is 3-32 letters, digits, dots, dashes or underscores.")
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(f"Use a password of at least {MIN_PASSWORD} characters.")
    state = signups_open()
    if state == "closed":
        raise AuthError("Sign-ups are closed. Ask the site's owner for an invite.", 403)
    if state == "invite":
        if _address_throttled(address):
            raise AuthError("Too many wrong tries. Wait a few minutes and try again.", 429)
        if not hmac.compare_digest((invite or "").strip(), _invite_code()):
            _failed("", address)   # wrong invite codes count against the address like wrong passwords
            raise AuthError("That invite code isn't right.", 403)
    if catalog.user_by_name(username):
        raise AuthError("That username is taken.", 409)
    user = catalog.create_user(username, hash_password(password))
    event("signup", user=user["id"], username=username, first=state == "first")
    if state == "first":
        from backend.library import library   # imported here: library imports day7, which tests set up first
        moved = library.give_legacy_library_to(user["id"])
        catalog.give_legacy_chats_and_batches_to(user["id"])
        event("legacy_library_claimed", user=user["id"], documents=moved)
    return user, _new_login(user)


def _recent(key: str, now: float) -> list[float]:
    recent = [t for t in _failures.get(key, []) if now - t < FAILURE_WINDOW]
    _failures[key] = recent
    return recent


def _throttled(username: str, address: str) -> bool:
    """A name is locked only for the address that keeps guessing it, so knowing someone's username is not enough
    to lock them out; an address that guesses many names is stopped as a whole."""
    now = time.monotonic()
    with _failures_lock:
        return (len(_recent(f"{username.lower()}@{address}", now)) >= MAX_FAILURES
                or len(_recent(f"@{address}", now)) >= MAX_ADDRESS_FAILURES)


def _address_throttled(address: str) -> bool:
    with _failures_lock:
        return len(_recent(f"@{address}", time.monotonic())) >= MAX_ADDRESS_FAILURES


def _failed(username: str, address: str) -> None:
    with _failures_lock:
        now = time.monotonic()
        if username:   # wrong invite codes have no name: they count for the address only
            _failures.setdefault(f"{username.lower()}@{address}", []).append(now)
        _failures.setdefault(f"@{address}", []).append(now)


def log_in(username: str, password: str, address: str = "") -> tuple[dict, str]:
    username = (username or "").strip()
    if _throttled(username, address):
        raise AuthError("Too many wrong passwords. Wait a few minutes and try again.", 429)
    user = catalog.user_by_name(username)
    if not user or not check_password(password or "", user["password_hash"]):
        _failed(username, address)
        event("login_failed", username=username[:40], address=address)
        raise AuthError("Wrong username or password.", 401)
    event("login", user=user["id"])
    return user, _new_login(user)


def log_out(token: str | None) -> None:
    if token:
        catalog.delete_token(_token_hash(token))


def user_for(token: str | None) -> dict | None:
    return catalog.user_for_token(_token_hash(token)) if token else None


def public(user: dict) -> dict:
    return {"id": user["id"], "username": user["username"]}
