"""The Memory service: facts and episodes about each person over gRPC (protos/agentic_rag_proto/memory.proto).

    python -m services.memory        # listens on MEMORY_SERVICE_ADDR (default localhost:50063)

Needs VOYAGE_API_KEY (environment or .env) to store and search episodes; facts work without it. The episode
collection is created (or its vector size checked) when the service starts.
"""
import asyncio
import logging

import grpc

from agentic_rag_proto import memory_pb2 as pb
from agentic_rag_proto import memory_pb2_grpc as pb_grpc
from backend.config import voyage
from backend.config.voyage import VoyageBusy
from services.common.server import Service, serve
from services.common.sqlite_db import ServiceDB
from services.memory import db as store
from services.memory.vectors import EpisodeStore

log = logging.getLogger(__name__)
NAME = "memory"
GRPC_NAME = "agentic_rag.v1.MemoryService"


def _opt(msg, name: str):
    return getattr(msg, name) if msg.HasField(name) else None


class MemoryServicer(pb_grpc.MemoryServiceServicer):
    def __init__(self, db: ServiceDB, episodes: EpisodeStore):
        self.db = db
        self.episodes = episodes

    async def _run(self, context, fn, *args):
        """Run a blocking call off the event loop; bad input and a busy embedding limit become gRPC statuses."""
        try:
            return await asyncio.to_thread(fn, *args)
        except ValueError as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e)[:200])
        except VoyageBusy as e:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, str(e)[:200])

    async def ListFacts(self, request, context):
        groups = await self._run(context, store.list_facts, self.db, request.user_id)
        return pb.ListFactsReply(groups=[pb.FactGroup(**g) for g in groups])

    async def AddFact(self, request, context):
        await self._run(context, store.add_fact, self.db, request.user_id, request.memory_key, request.value)
        return pb.FactReply()

    async def SetFact(self, request, context):
        await self._run(context, store.set_fact, self.db, request.user_id, request.memory_key, request.value)
        return pb.FactReply()

    async def DeleteFact(self, request, context):
        n = await self._run(context, store.delete_fact, self.db, request.user_id, request.memory_key,
                            _opt(request, "value"))
        return pb.DeleteFactReply(deleted=n)

    async def StoreEpisode(self, request, context):
        point = await self._run(context, self.episodes.store, request.user_id, request.text, request.kind,
                                _opt(request, "conversation_id"))
        return pb.StoreEpisodeReply(id=point)

    async def SearchEpisodes(self, request, context):
        found = await self._run(context, self.episodes.search, request.user_id, request.query, request.limit or 5,
                                _opt(request, "score_threshold"))
        return pb.SearchEpisodesReply(episodes=[pb.Episode(**{k: v for k, v in e.items() if v is not None})
                                                for e in found])

    async def DeleteConversationEpisodes(self, request, context):
        await self._run(context, self.episodes.delete_conversation, request.user_id, request.conversation_id)
        return pb.DeleteConversationEpisodesReply()

    async def ImportFacts(self, request, context):
        facts = [{"id": f.id, "user_id": f.user_id, "memory_key": f.memory_key, "value": f.value,
                  "created_at": f.created_at, "updated_at": f.updated_at} for f in request.facts]
        return pb.ImportReply(created=await self._run(context, store.import_facts, self.db, facts))

    async def ImportEpisodes(self, request, context):
        episodes = [{"id": e.id, "vector": list(e.vector), "user_id": e.user_id, "text": e.text, "kind": e.kind,
                     "conversation_id": _opt(e, "conversation_id"), "updated_at": e.updated_at}
                    for e in request.episodes]
        return pb.ImportReply(created=await self._run(context, self.episodes.import_points, episodes))

    async def CountMemory(self, request, context):
        facts = await self._run(context, store.count_facts, self.db)
        return pb.CountMemoryReply(facts=facts, episodes=await self._run(context, self.episodes.count))


def build_service(db: ServiceDB | None = None, episodes: EpisodeStore | None = None) -> Service:
    """The service ready to bind and start; tests pass a database and an episode folder in a temporary folder.
    The episode collection is created here, at start."""
    db = db or store.open_db()
    episodes = episodes or EpisodeStore()
    service = Service(NAME)
    service.register(pb_grpc.add_MemoryServiceServicer_to_server, MemoryServicer(db, episodes), GRPC_NAME)
    service.add_closer(episodes.close)
    service.add_closer(db.close)
    return service


def main() -> None:
    voyage.use_memory_share()   # this process may use only the memory part of the account's Voyage budget
    serve(NAME, build_service)
