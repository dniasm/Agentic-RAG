"""The User service: accounts, logins and usage limits over gRPC (protos/agentic_rag_proto/user.proto).

    python -m services.user        # listens on USER_SERVICE_ADDR (default localhost:50061)

Passwords and tokens are never logged: the request log (services.common.server) records the method and the status only.
"""
import asyncio
import logging
import time

import grpc

from agentic_rag_proto import user_pb2 as pb
from agentic_rag_proto import user_pb2_grpc as pb_grpc
from services.common.log import audit
from services.common.server import Service, serve
from services.common.sqlite_db import ServiceDB
from services.user import accounts
from services.user import db as store

log = logging.getLogger(__name__)
NAME = "user"
GRPC_NAME = "agentic_rag.v1.UserService"


def _user(row: dict) -> pb.User:
    return pb.User(id=row["id"], username=row["username"], created_at=row["created_at"])


def _api_row(r: dict) -> pb.ApiUsageRow:
    return pb.ApiUsageRow(**r)


class UserServicer(pb_grpc.UserServiceServicer):
    def __init__(self, db: ServiceDB, clock=time.time):
        self.db = db
        self.clock = clock   # wall-clock seconds; tests pass their own

    async def _run(self, context, fn, *args):
        """Run a blocking store call off the event loop; bad input and a taken name become gRPC statuses."""
        try:
            return await asyncio.to_thread(fn, self.db, *args)
        except store.UsernameTaken:
            await context.abort(grpc.StatusCode.ALREADY_EXISTS, "That username is taken.")
        except store.BadLogin:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Wrong username or password.")
        except ValueError as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e)[:200])

    @staticmethod
    def _login(user: dict, token: str, first: bool = False) -> pb.LoginReply:
        return pb.LoginReply(user=_user(user), token=token, first_account=first,
                             max_age_seconds=accounts.LOGIN_DAYS * 86400)

    async def CountUsers(self, request, context):
        return pb.CountUsersReply(count=await self._run(context, store.count_users))

    async def SignUp(self, request, context):
        user, token, first = await self._run(context, store.sign_up, request.username, request.password)
        audit("signup", user=user["id"], username=user["username"], first=first)
        return self._login(user, token, first)

    async def LogIn(self, request, context):
        user, token = await self._run(context, store.log_in, request.username, request.password)
        audit("login", user=user["id"])
        return self._login(user, token)

    async def LogOut(self, request, context):
        await self._run(context, store.log_out, request.token)
        return pb.LogOutReply()

    async def ValidateToken(self, request, context):
        user = await self._run(context, store.user_for_token, request.token)
        return pb.ValidateTokenReply(found=True, user=_user(user)) if user else pb.ValidateTokenReply(found=False)

    async def ListUsers(self, request, context):
        return pb.ListUsersReply(users=[_user(u) for u in await self._run(context, store.list_users)])

    async def GetEmbedUsage(self, request, context):
        user_id = request.user_id if request.HasField("user_id") else None
        return pb.EmbedUsage(chunks=await self._run(context, store.embed_used, request.day, user_id))

    async def AddEmbedUsage(self, request, context):
        return pb.EmbedUsage(chunks=await self._run(context, store.add_embed_usage, request.day, request.user_id,
                                                    request.chunks))

    async def ListEmbedUsage(self, request, context):
        rows = await self._run(context, store.list_embed_usage, request.day)
        return pb.ListEmbedUsageReply(rows=[pb.EmbedUsageRow(**r) for r in rows])

    async def AddApiUsage(self, request, context):
        rows = [{c: getattr(r, c) for c in store.API_COLUMNS} for r in request.rows]
        await self._run(context, store.add_api_usage, rows)
        return pb.AddApiUsageReply()

    async def ListApiUsage(self, request, context):
        rows = await self._run(context, store.list_api_usage, request.since_hour)
        return pb.ListApiUsageReply(rows=[_api_row(r) for r in rows])

    async def CallsByKey(self, request, context):
        return pb.CallsByKeyReply(calls=await self._run(context, store.calls_by_key, request.provider,
                                                        request.since_hour))

    @staticmethod
    def _windows(request) -> list[tuple[int, int, bool]]:
        return [(w.seconds, w.allowed, w.daily) for w in request.windows]

    async def ReserveChatMessage(self, request, context):
        token, retry_after, daily = await self._run(context, store.reserve_chat, request.user_id,
                                                    self._windows(request), self.clock())
        if token is None:
            return pb.ReserveChatMessageReply(ok=False, retry_after=retry_after, daily=daily)
        return pb.ReserveChatMessageReply(ok=True, token=token)

    async def RefundChatMessage(self, request, context):
        return pb.RefundChatMessageReply(refunded=await self._run(context, store.refund_chat, request.user_id,
                                                                  request.token))

    async def ChatUsage(self, request, context):
        user_id = request.user_id if request.HasField("user_id") else None
        found = await self._run(context, store.chat_usage, user_id, self._windows(request), self.clock())
        return pb.ChatUsageReply(rows=[pb.ChatUsageRow(user_id=u, windows=[
            pb.ChatWindowUsage(used=n, oldest_age_seconds=age) for n, age in row]) for u, row in found.items()])

    async def ImportUsers(self, request, context):
        users = [{"id": u.id, "username": u.username, "password_hash": u.password_hash, "created_at": u.created_at}
                 for u in request.users]
        tokens = [{"token_hash": t.token_hash, "user_id": t.user_id, "created_at": t.created_at,
                   "expires_at": t.expires_at} for t in request.tokens]
        embed = [{"day": e.day, "user_id": e.user_id, "chunks": e.chunks} for e in request.embed_usage]
        api = [{c: getattr(r, c) for c in store.API_COLUMNS} for r in request.api_usage]
        done = await self._run(context, store.import_rows, users, tokens, embed, api)
        return pb.ImportUsersReply(users_created=done["users"], tokens_created=done["tokens"],
                                   embed_usage_created=done["embed"], api_usage_created=done["api"],
                                   skipped_users=done["skipped"])


def build_service(db: ServiceDB | None = None, clock=time.time) -> Service:
    """The service ready to bind and start; tests pass a database in a temporary folder."""
    db = db or store.open_db()
    service = Service(NAME)
    service.register(pb_grpc.add_UserServiceServicer_to_server, UserServicer(db, clock), GRPC_NAME)
    service.add_closer(db.close)
    return service


def main() -> None:
    serve(NAME, build_service)
