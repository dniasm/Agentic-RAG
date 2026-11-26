"""Conversations and their messages, plus the per-user stats computed from them.

They live in the Conversation service (services/conversation/, its own conversation.db); these functions call it
over gRPC (backend/clients.py) and return the same dicts the old SQL did, so every caller is unchanged. The
documents part of user_stats is counted here, from the catalog (also the Document service's).
"""
import json

import grpc

from agentic_rag_proto import conversation_pb2 as pb
from backend.clients import get_clients
from backend.library import catalog


def _call(rpc_name: str, request):
    clients = get_clients()
    return clients.call(getattr(clients.conversation, rpc_name), request)


def _not_found(e: grpc.RpcError) -> bool:
    return e.code() == grpc.StatusCode.NOT_FOUND


def _conv(c: pb.Conversation) -> dict:
    return {"id": c.id, "user_id": c.user_id, "title": c.title, "created_at": c.created_at, "updated_at": c.updated_at}


def _msg(m: pb.Message) -> dict:
    return {"id": m.id, "conversation_id": m.conversation_id, "role": m.role, "content": m.content,
            "tool": m.tool if m.HasField("tool") else None, "meta": json.loads(m.meta_json or "{}"),
            "created_at": m.created_at, "latency_ms": m.latency_ms if m.HasField("latency_ms") else None,
            "rewritten": int(m.rewritten), "sufficient": int(m.sufficient) if m.HasField("sufficient") else None}


# ---------------------------------------------------------------- conversations
def create_conversation(user_id: str, title: str) -> dict:
    return _conv(_call("CreateConversation", pb.CreateConversationRequest(user_id=user_id, title=title)))


def get_conversation(user_id: str, conv_id: str) -> dict | None:
    """None when there is no such conversation or it is another user's."""
    try:
        return _conv(_call("GetConversation", pb.ConversationRef(user_id=user_id, conversation_id=conv_id)))
    except grpc.RpcError as e:
        if _not_found(e):
            return None
        raise


def list_conversations(user_id: str) -> list[dict]:
    reply = _call("ListConversations", pb.ListConversationsRequest(user_id=user_id))
    return [{"id": c.id, "title": c.title, "created_at": c.created_at, "updated_at": c.updated_at}
            for c in reply.conversations]


def rename_conversation(user_id: str, conv_id: str, title: str) -> None:
    """Changes the title only: updated_at is left alone, so a renamed chat keeps its place in the list. Another
    user's (or no) conversation is left as it is."""
    try:
        _call("RenameConversation", pb.RenameConversationRequest(user_id=user_id, conversation_id=conv_id, title=title))
    except grpc.RpcError as e:
        if not _not_found(e):
            raise


def delete_conversation(user_id: str, conv_id: str) -> None:
    try:
        _call("DeleteConversation", pb.ConversationRef(user_id=user_id, conversation_id=conv_id))
    except grpc.RpcError as e:
        if not _not_found(e):
            raise


def add_message(conv_id: str, role: str, content: str, tool: str | None = None, meta: dict | None = None) -> dict:
    request = pb.AddMessageRequest(conversation_id=conv_id, role=role, content=content,
                                   meta_json=json.dumps(meta or {}))
    if tool is not None:
        request.tool = tool
    try:
        m = _call("AddMessage", request)
    except grpc.RpcError as e:
        if _not_found(e):
            raise LookupError(f"no conversation {conv_id}") from e
        raise
    return {"id": m.id, "conversation_id": m.conversation_id, "role": m.role, "content": m.content,
            "tool": tool, "meta": meta or {}, "created_at": m.created_at}


def list_tool_messages(conv_id: str, tool: str) -> list[dict]:
    """One conversation's answers from one tool, oldest first, with their meta."""
    reply = _call("ListToolMessages", pb.ListToolMessagesRequest(conversation_id=conv_id, tool=tool))
    return [{"id": m.id, "meta": json.loads(m.meta_json or "{}"), "created_at": m.created_at} for m in reply.messages]


def list_messages(conv_id: str, limit: int | None = None) -> list[dict]:
    reply = _call("ListMessages", pb.ListMessagesRequest(conversation_id=conv_id, limit=limit or 0))
    return [_msg(m) for m in reply.messages]


# ---------------------------------------------------------------- stats
def user_stats(user_id: str) -> dict:
    indexed = catalog.list_documents(status="indexed", owner=user_id)
    s = _call("GetStats", pb.GetStatsRequest(user_id=user_id))
    return {
        "documents": len(indexed),
        "chunks": sum(d["chunks"] or 0 for d in indexed),
        "conversations": s.conversations,
        "queries": s.queries,
        "by_tool": dict(s.by_tool),
        "queries_rewritten": s.queries_rewritten,
        "answers_evaluated": s.answers_evaluated,
        "answers_sufficient": s.answers_sufficient,
        "avg_latency_ms": s.avg_latency_ms,
    }
