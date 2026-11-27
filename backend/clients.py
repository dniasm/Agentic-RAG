"""gRPC clients for the orchestrator: one shared sync channel per service (thread-safe), a deadline on every unary call.

`SERVICES` maps each service to its address setting and its gRPC name. A service that owns data has a stub attribute
here (`clients.conversation`), and the orchestrator calls `clients.call(clients.conversation.GetConversation,
request)`, from sync routes and worker threads alike. `get_clients()` is the process-wide instance.
"""
import threading
import time

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from agentic_rag_proto import conversation_pb2_grpc, document_pb2_grpc, memory_pb2_grpc, user_pb2_grpc
from backend.config.settings import Settings, settings as default_settings
from services.common.config import SERVICE_ADDRESSES
from services.common.log import REQUEST_ID_HEADER, get_request_id

# name -> (fully-qualified gRPC service, Settings attribute holding its address)
SERVICES = {name: (grpc_name, f"{name}_service_addr") for name, (_, _, grpc_name) in SERVICE_ADDRESSES.items()}


class Clients:
    def __init__(self, settings: Settings | None = None):
        settings = settings or default_settings
        self._timeout = settings.grpc_timeout_seconds
        self._health_timeout = settings.grpc_health_timeout_seconds
        self.addresses = {name: getattr(settings, attr) for name, (_, attr) in SERVICES.items()}
        # channels connect lazily, so building Clients needs no running service
        self._channels = {name: grpc.insecure_channel(addr) for name, addr in self.addresses.items()}
        self.conversation = conversation_pb2_grpc.ConversationServiceStub(self._channels["conversation"])
        self.user = user_pb2_grpc.UserServiceStub(self._channels["user"])
        self.memory = memory_pb2_grpc.MemoryServiceStub(self._channels["memory"])
        self.document = document_pb2_grpc.DocumentServiceStub(self._channels["document"])

    def channel(self, name: str) -> grpc.Channel:
        return self._channels[name]

    def call(self, rpc, request, metadata: tuple | None = None, timeout: float | None = None):
        """Run one unary RPC with the standard deadline (or `timeout`, for a call the service may spend longer on,
        such as one waiting for a Voyage slot), carrying the current request id so the service's log lines join the
        request's trace. gRPC errors propagate as grpc.RpcError."""
        rid = get_request_id()
        if rid != "-":   # "-" is "no request" (a background job): the service then starts its own trace
            metadata = (*(metadata or ()), (REQUEST_ID_HEADER, rid))
        return rpc(request, timeout=self._timeout if timeout is None else timeout, metadata=metadata)

    def call_stream(self, rpc, requests, timeout: float | None = None):
        """Start a streaming RPC (an iterator of requests in, an iterator of replies out) with the current request id.
        The deadline is the caller's: a file can take minutes to read or index."""
        rid = get_request_id()
        metadata = ((REQUEST_ID_HEADER, rid),) if rid != "-" else None
        return rpc(requests, timeout=timeout, metadata=metadata)

    def probe(self, name: str) -> dict:
        """Ask one service's standard grpc.health.v1 endpoint; down means no SERVING answer within the deadline."""
        stub = health_pb2_grpc.HealthStub(self._channels[name])
        started = time.perf_counter()
        error = None
        try:
            reply = stub.Check(health_pb2.HealthCheckRequest(), timeout=self._health_timeout)
            if reply.status != health_pb2.HealthCheckResponse.SERVING:
                error = f"reports {health_pb2.HealthCheckResponse.ServingStatus.Name(reply.status)}"
        except grpc.RpcError as e:
            error = "no response (timed out)" if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED else (e.details() or e.code().name)
        return {
            "name": name, "service": SERVICES[name][0], "address": self.addresses[name],
            "status": "down" if error else "up", "latency_ms": round((time.perf_counter() - started) * 1000),
            "error": error,
        }

    def close(self) -> None:
        for channel in self._channels.values():
            channel.close()


_shared: Clients | None = None
_shared_lock = threading.Lock()


def get_clients() -> Clients:
    """The process-wide clients, built on first use from the settings (so a service address set before that applies)."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Clients()
        return _shared


def reset_clients() -> None:
    """Close the shared clients; the next get_clients() dials the addresses in the settings again."""
    global _shared
    with _shared_lock:
        if _shared is not None:
            _shared.close()
        _shared = None
