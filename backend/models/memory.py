"""Remembered facts about a user: key -> many values.

They live in the Memory service (services/memory/, its own memory.db); these functions call it over gRPC
(backend/clients.py) and return the same dicts the old SQL did, so every caller is unchanged. app.db has no facts
table any more (scripts/migrate_memory.py copies an old one, and the older user_memory table, into the service).
"""
from agentic_rag_proto import memory_pb2 as pb
from backend.clients import get_clients


def _call(rpc_name: str, request):
    clients = get_clients()
    return clients.call(getattr(clients.memory, rpc_name), request)


def list_memories(user_id: str) -> list[dict]:
    """The user's facts grouped by key, most recently updated key first:
    [{"memory_key", "memory_value" (values joined with "; "), "values", "updated_at"}].
    memory_value is what prompts show, e.g. "hobbies: trekking; futsal"."""
    return [{"memory_key": g.memory_key, "values": list(g.values), "updated_at": g.updated_at,
             "memory_value": "; ".join(g.values)}
            for g in _call("ListFacts", pb.ListFactsRequest(user_id=user_id)).groups]


def add_fact(user_id: str, memory_key: str, value: str) -> None:
    """Add one value to a key (a duplicate, ignoring case, only refreshes its timestamp)."""
    _call("AddFact", pb.FactRequest(user_id=user_id, memory_key=memory_key, value=value))


def set_fact(user_id: str, memory_key: str, value: str) -> None:
    """Make `value` the key's only value (single-value keys such as preferred_name)."""
    _call("SetFact", pb.FactRequest(user_id=user_id, memory_key=memory_key, value=value))


def delete_fact(user_id: str, memory_key: str, value: str | None = None) -> int:
    """Delete one value of a key (ignoring case), or the whole key when value is None. Returns rows deleted."""
    request = pb.DeleteFactRequest(user_id=user_id, memory_key=memory_key)
    if value is not None:
        request.value = value
    return _call("DeleteFact", request).deleted
