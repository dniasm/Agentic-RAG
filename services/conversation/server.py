"""The Conversation service: saved chats over gRPC (protos/agentic_rag_proto/conversation.proto).

    python -m services.conversation        # listens on CONVERSATION_SERVICE_ADDR (default localhost:50062)
"""
import asyncio
import json
import logging

import grpc

from agentic_rag_proto import conversation_pb2 as pb
from agentic_rag_proto import conversation_pb2_grpc as pb_grpc
from services.common.log import audit
from services.common.server import Service, serve
from services.common.sqlite_db import ServiceDB
from services.conversation import db as store

log = logging.getLogger(__name__)
NAME = "conversation"
GRPC_NAME = "agentic_rag.v1.ConversationService"


def _conversation(row: dict) -> pb.Conversation:
    return pb.Conversation(id=row["id"], user_id=row["user_id"], title=row["title"],
                           created_at=row["created_at"], updated_at=row["updated_at"])


def _message(row: dict) -> pb.Message:
    msg = pb.Message(id=row["id"], conversation_id=row.get("conversation_id", ""), role=row.get("role", ""),
                     content=row.get("content", ""), meta_json=row.get("meta") or "{}", created_at=row["created_at"],
                     rewritten=bool(row.get("rewritten")))
    if row.get("tool") is not None:
        msg.tool = row["tool"]
    if row.get("latency_ms") is not None:
        msg.latency_ms = row["latency_ms"]
    if row.get("sufficient") is not None:
        msg.sufficient = bool(row["sufficient"])
    return msg


def _row(m: pb.Message) -> dict:
    """An imported message as a row; the stat columns are the message's own, else read from its meta."""
    try:
        meta = json.loads(m.meta_json or "{}")
    except ValueError:
        meta = {}
    stats = store.stats_from_meta(meta if isinstance(meta, dict) else {})
    return {"id": m.id, "conversation_id": m.conversation_id, "role": m.role, "content": m.content,
            "tool": m.tool if m.HasField("tool") else None, "meta": m.meta_json or "{}", "created_at": m.created_at,
            "latency_ms": m.latency_ms if m.HasField("latency_ms") else stats["latency_ms"],
            "rewritten": int(m.rewritten or stats["rewritten"]),
            "sufficient": int(m.sufficient) if m.HasField("sufficient") else stats["sufficient"]}


class ConversationServicer(pb_grpc.ConversationServiceServicer):
    def __init__(self, db: ServiceDB):
        self.db = db

    async def _run(self, context, fn, *args):
        """Run a blocking store call off the event loop; NOT_FOUND and bad input become gRPC statuses."""
        try:
            return await asyncio.to_thread(fn, self.db, *args)
        except store.NotFound:
            await context.abort(grpc.StatusCode.NOT_FOUND, "conversation not found")
        except ValueError as e:   # includes json.JSONDecodeError
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e)[:200])

    async def CreateConversation(self, request, context):
        conv = await self._run(context, store.create_conversation, request.user_id, request.title)
        audit("conversation_created", conversation=conv["id"], user=request.user_id)
        return _conversation(conv)

    async def GetConversation(self, request, context):
        return _conversation(await self._run(context, store.get_conversation, request.user_id, request.conversation_id))

    async def ListConversations(self, request, context):
        rows = await self._run(context, store.list_conversations, request.user_id)
        return pb.ListConversationsReply(conversations=[_conversation(r) for r in rows])

    async def RenameConversation(self, request, context):
        return _conversation(await self._run(context, store.rename_conversation, request.user_id,
                                             request.conversation_id, request.title))

    async def DeleteConversation(self, request, context):
        gone = await self._run(context, store.delete_conversation, request.user_id, request.conversation_id)
        audit("conversation_deleted", conversation=request.conversation_id, user=request.user_id, messages=gone)
        return pb.DeleteConversationReply()

    async def AddMessage(self, request, context):
        tool = request.tool if request.HasField("tool") else None
        return _message(await self._run(context, store.add_message, request.conversation_id, request.role,
                                        request.content, tool, request.meta_json))

    async def ListMessages(self, request, context):
        rows = await self._run(context, store.list_messages, request.conversation_id, request.limit)
        return pb.ListMessagesReply(messages=[_message(r) for r in rows])

    async def ListToolMessages(self, request, context):
        rows = await self._run(context, store.list_tool_messages, request.conversation_id, request.tool)
        return pb.ListMessagesReply(messages=[_message(r) for r in rows])

    async def GetStats(self, request, context):
        return pb.Stats(**await self._run(context, store.user_stats, request.user_id))

    async def ImportConversation(self, request, context):
        c = request.conversation
        conv = {"id": c.id, "user_id": c.user_id, "title": c.title, "created_at": c.created_at,
                "updated_at": c.updated_at}
        created, n = await self._run(context, store.import_conversation, conv, [_row(m) for m in request.messages])
        return pb.ImportConversationReply(conversation_created=created, messages_created=n)


def build_service(db: ServiceDB | None = None) -> Service:
    """The service ready to bind and start; tests pass a database in a temporary folder."""
    db = db or store.open_db()
    service = Service(NAME)
    service.register(pb_grpc.add_ConversationServiceServicer_to_server, ConversationServicer(db), GRPC_NAME)
    service.add_closer(db.close)
    return service


def main() -> None:
    serve(NAME, build_service)
