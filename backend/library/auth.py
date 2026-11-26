"""Accounts: sign-up, log-in, log-out, as the orchestrator sees them.

The accounts themselves live in the User service (services/user/): it hashes and checks passwords (salted PBKDF2, and
day7's older bcrypt), makes login tokens and keeps only their SHA-256, so a copied database holds no token anyone can
use. What stays here is what belongs to the HTTP side:
  * A login is a random token in an HttpOnly cookie (backend/api/routes/auth.py sets it). Logins last LOGIN_DAYS.
  * Wrong passwords are throttled, in memory: after MAX_FAILURES for a name from one address within FAILURE_WINDOW,
    that name waits there; after MAX_ADDRESS_FAILURES from one address, that address waits.
  * The first account can always be made, and the library from before accounts existed becomes its own. After that a
    sign-up needs the invite code (SIGNUP_CODE in .env): the site is public through the tunnel, and every
    account spends the owner's Groq, Gemini and Voyage allowances. With no code set, sign-ups are closed.
  * Admin names (ADMIN_USERNAMES) are checked here, against the account's name.
  * If the User service can't answer, nobody is logged in: user_for raises UserServiceDown and the routes answer 503.
  * One login for the whole app (day7's chat and the library): backend/api/routes/auth.py serves these at /api/auth/*.
"""
import hmac
import threading
import time

import grpc

from agentic_rag_proto import user_pb2 as pb
from backend.clients import get_clients
from backend.library import config
from backend.library.logs import event
from services.user import accounts

LOGIN_DAYS = accounts.LOGIN_DAYS
MAX_FAILURES, FAILURE_WINDOW = 5, 15 * 60   # wrong passwords for one name from one address
MAX_ADDRESS_FAILURES = 20                    # wrong passwords from one address, any names
COOKIE = "login"

_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()

# answers about the person (taken name, wrong password, bad shape); every other gRPC failure means "can't tell"
_ANSWERS = (grpc.StatusCode.ALREADY_EXISTS, grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.INVALID_ARGUMENT)


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class UserServiceDown(Exception):
    """The User service did not answer: whoever asked is NOT logged in (every caller must fail closed)."""


def _call(rpc_name: str, request):
    """One User service call. Answers about the person come back as grpc.RpcError; an unreachable or failing
    service is UserServiceDown."""
    clients = get_clients()
    try:
        return clients.call(getattr(clients.user, rpc_name), request)
    except grpc.RpcError as e:
        if e.code() in _ANSWERS:
            raise
        raise UserServiceDown(f"{rpc_name}: {e.code().name}") from e


def _user(u: pb.User) -> dict:
    return {"id": u.id, "username": u.username, "created_at": u.created_at}


def user_count() -> int:
    return _call("CountUsers", pb.CountUsersRequest()).count


def _invite_code() -> str:
    return config.SIGNUP_CODE


def signups_open() -> str:
    """'first' (no account yet), 'invite' (a code is needed) or 'closed'."""
    if user_count() == 0:
        return "first"
    return "invite" if _invite_code() else "closed"


def sign_up(username: str, password: str, invite: str = "", address: str = "") -> tuple[dict, str]:
    """Returns (user, login token). The first account takes the library from before accounts existed."""
    try:
        username = accounts.validate_new(username, password)
    except ValueError as e:
        raise AuthError(str(e)) from e
    state = signups_open()
    if state == "closed":
        raise AuthError("Sign-ups are closed. Ask the site's owner for an invite.", 403)
    if state == "invite":
        if _address_throttled(address):
            raise AuthError("Too many wrong tries. Wait a few minutes and try again.", 429)
        if not hmac.compare_digest((invite or "").strip(), _invite_code()):
            _failed("", address)   # wrong invite codes count against the address like wrong passwords
            raise AuthError("That invite code isn't right.", 403)
    if is_admin_name(username):   # an admin name is never claimable by signing up: only an existing account counts
        raise AuthError("That username is taken.", 409)
    try:
        reply = _call("SignUp", pb.SignUpRequest(username=username, password=password))
    except grpc.RpcError as e:
        taken = e.code() == grpc.StatusCode.ALREADY_EXISTS
        raise AuthError(e.details() or "Couldn't sign up.", 409 if taken else 400) from e
    user = _user(reply.user)
    if reply.first_account:
        from backend.library import catalog, library   # imported here: library imports day7, which tests set up first
        moved = library.give_legacy_library_to(user["id"])
        catalog.give_legacy_chats_and_batches_to(user["id"])
        event("legacy_library_claimed", user=user["id"], documents=moved)
    return user, reply.token


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
    try:
        reply = _call("LogIn", pb.LogInRequest(username=username, password=password or ""))
    except grpc.RpcError as e:   # only an answer about the person gets here: the wrong name or password
        _failed(username, address)
        event("login_failed", username=username[:40], address=address)
        raise AuthError("Wrong username or password.", 401) from e
    return _user(reply.user), reply.token


def log_out(token: str | None) -> None:
    if token:
        _call("LogOut", pb.LogOutRequest(token=token))


def user_for(token: str | None) -> dict | None:
    """The account this login token belongs to, or None. Raises UserServiceDown when the service can't say: the
    caller must refuse the request, never treat the person as signed in."""
    if not token:
        return None
    reply = _call("ValidateToken", pb.ValidateTokenRequest(token=token))
    return _user(reply.user) if reply.found else None


def list_users() -> list[dict]:
    return [_user(u) for u in _call("ListUsers", pb.ListUsersRequest()).users]


def admin_names() -> set[str]:
    """settings.admin_usernames, compared without case, as logins are (usernames are unique without regard to case)."""
    from backend.config.settings import settings
    return {n.strip().casefold() for n in (settings.admin_usernames or "").split(",") if n.strip()}


def is_admin_name(username: str) -> bool:
    return (username or "").strip().casefold() in admin_names()


def public(user: dict) -> dict:
    return {"id": user["id"], "username": user["username"], "is_admin": is_admin_name(user["username"])}
