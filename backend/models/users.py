"""Test helpers for accounts. The one users table is the User service's (services/user/, user.db); the app makes
accounts with backend/library/auth.py. These add or read an account with a password hash given by the caller, which a
real sign-up never does, so they go through the service's bulk-import call (and are for tests and scripts only)."""
import uuid
from datetime import datetime, timezone

from agentic_rag_proto import user_pb2 as pb
from backend.clients import get_clients



def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _call(rpc_name: str, request):
    clients = get_clients()
    return clients.call(getattr(clients.user, rpc_name), request)


def create_user(username: str, password_hash: str) -> dict:
    user = {"id": uuid.uuid4().hex, "username": username, "password_hash": password_hash, "created_at": _now()}
    reply = _call("ImportUsers", pb.ImportUsersRequest(users=[pb.ImportedUser(**user)]))
    if not reply.users_created:   # tests that each make "their" account share one service database: the name gets a suffix
        user["username"] = f"{username}-{user['id'][:6]}"
        _call("ImportUsers", pb.ImportUsersRequest(users=[pb.ImportedUser(**user)]))
    return user


def get_user(user_id: str) -> dict | None:
    for u in _call("ListUsers", pb.ListUsersRequest()).users:
        if u.id == user_id:
            return {"id": u.id, "username": u.username, "created_at": u.created_at}
    return None
