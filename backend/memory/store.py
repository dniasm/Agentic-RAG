"""Episodic memory: short summaries of past exchanges, found again by meaning.

They live in the Memory service (services/memory/): it embeds the text with Voyage, keeps the vectors in its own
Qdrant folder and creates the collection when it starts. These functions call it over gRPC (backend/clients.py) and
return the same dicts the old in-process store did.
"""
from typing import Any

from agentic_rag_proto import memory_pb2 as pb
from backend.clients import get_clients
from backend.config.settings import settings


def _call(rpc_name: str, request, timeout: float | None = None):
    clients = get_clients()
    return clients.call(getattr(clients.memory, rpc_name), request, timeout=timeout)


def _voyage_deadline(max_wait: float) -> float:
    """Deadline for a call the service embeds with Voyage: its wait for a slot, the call itself, and a margin.
    Shorter, and the orchestrator gave up while the service was still inside its own budget (a save never stored)."""
    return max_wait + settings.voyage_rerank_timeout + settings.grpc_search_margin_seconds


def save_memory(
    user_id: str,
    text: str,
    kind: str = "episodic",
    conversation_id: str | None = None,
) -> str | None:
    """Store an episode (the service embeds it). Returns its point ID."""
    request = pb.StoreEpisodeRequest(user_id=user_id, text=text, kind=kind)
    if conversation_id is not None:
        request.conversation_id = conversation_id
    return _call("StoreEpisode", request, timeout=_voyage_deadline(settings.voyage_save_max_wait)).id


def search_memories(
    user_id: str,
    query: str,
    limit: int = 5,
    score_threshold: float | None = None,
) -> list[dict[str, Any]]:
    """The user's episodes closest in meaning to the query, best first."""
    if not query.strip() or limit <= 0:
        return []
    request = pb.SearchEpisodesRequest(user_id=user_id, query=query, limit=limit)
    if score_threshold is not None:
        request.score_threshold = score_threshold
    return [{"id": e.id, "text": e.text, "kind": e.kind or "episodic",
             "conversation_id": e.conversation_id if e.HasField("conversation_id") else None,
             "updated_at": e.updated_at if e.HasField("updated_at") else None, "score": e.score}
            for e in _call("SearchEpisodes", request,
                           timeout=_voyage_deadline(settings.voyage_recall_max_wait)).episodes]


def delete_conversation_memories(user_id: str, conversation_id: str) -> None:
    """Delete the episodes made in one of the user's conversations."""
    _call("DeleteConversationEpisodes",
          pb.DeleteConversationEpisodesRequest(user_id=user_id, conversation_id=conversation_id))
