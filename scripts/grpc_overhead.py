"""What one gRPC call between the orchestrator and a service costs: the four services run in this process on
ephemeral localhost ports (as in the tests, throw-away data) and the orchestrator's own code makes N round-trips.

    python scripts/grpc_overhead.py            # 200 round-trips of each call
    python scripts/grpc_overhead.py --n 1000

Prints the median and 95th percentile in milliseconds for ValidateToken (the User service, made on every signed-in
request) and ListConversations (the Conversation service), plus the whole sign-in check `auth.user_for`.
Loopback only: a service on another host or container adds the network's own latency to every number.
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _percentile(sorted_ms: list[float], p: float) -> float:
    return sorted_ms[min(len(sorted_ms) - 1, int(len(sorted_ms) * p))]


def measure(fn, n: int) -> dict:
    for _ in range(10):   # warm up the channel and the server's thread
        fn()
    ms = []
    for _ in range(n):
        started = time.perf_counter()
        fn()
        ms.append((time.perf_counter() - started) * 1000)
    ms.sort()
    return {"n": n, "median_ms": round(statistics.median(ms), 2), "p95_ms": round(_percentile(ms, 0.95), 2)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--n", type=int, default=200, help="round-trips per call (default 200)")
    n = parser.parse_args().n

    data = tempfile.mkdtemp(prefix="grpc_overhead_")
    os.environ.update({"SERVICES_DATA_DIR": os.path.join(data, "services"), "LOG_DIR": os.path.join(data, "logs"),
                       "LIBRARY_DATA_DIR": os.path.join(data, "library"), "QDRANT_URL": ""})
    from backend import clients
    from backend.config.settings import settings
    from backend.library import auth
    from services.common.inprocess import RunningService
    from services.conversation import db as conversation_store
    from services.conversation.server import build_service as build_conversation
    from services.document.server import build_service as build_document
    from services.memory import db as memory_store
    from services.memory.server import build_service as build_memory
    from services.memory.vectors import EpisodeStore
    from services.user import db as user_store
    from services.user.server import build_service as build_user
    from agentic_rag_proto import conversation_pb2, user_pb2

    running = {
        "conversation": RunningService(lambda db=conversation_store.open_db(): build_conversation(db)).start(),
        "user": RunningService(lambda db=user_store.open_db(): build_user(db)).start(),
        "memory": RunningService(lambda db=memory_store.open_db(), eps=EpisodeStore(): build_memory(db, eps)).start(),
        "document": RunningService(build_document).start(),
    }
    for name, svc in running.items():
        setattr(settings, f"{name}_service_addr", svc.address)
    clients.reset_clients()
    try:
        user, token = auth.sign_up("overhead", "correct horse")
        c = clients.get_clients()
        validate = user_pb2.ValidateTokenRequest(token=token)
        listing = conversation_pb2.ListConversationsRequest(user_id=user["id"])
        results = {
            "ValidateToken": measure(lambda: c.call(c.user.ValidateToken, validate), n),
            "ListConversations": measure(lambda: c.call(c.conversation.ListConversations, listing), n),
            "auth.user_for": measure(lambda: auth.user_for(token), n),
        }
    finally:
        clients.reset_clients()
        for svc in running.values():
            svc.stop()
    print(f"{'call':<20}{'round-trips':>12}{'median ms':>11}{'p95 ms':>9}")
    for name, r in results.items():
        print(f"{name:<20}{r['n']:>12}{r['median_ms']:>11}{r['p95_ms']:>9}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
