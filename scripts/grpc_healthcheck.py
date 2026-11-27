"""Exit 0 when the gRPC server on localhost:<port> answers grpc.health.v1 Check with SERVING (container healthcheck).

    python scripts/grpc_healthcheck.py 50061 [host]
"""
import sys

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc


def check(port: int | str, host: str = "localhost", timeout: float = 3.0) -> bool:
    with grpc.insecure_channel(f"{host}:{port}") as channel:
        reply = health_pb2_grpc.HealthStub(channel).Check(health_pb2.HealthCheckRequest(), timeout=timeout)
    return reply.status == health_pb2.HealthCheckResponse.SERVING


if __name__ == "__main__":
    try:
        sys.exit(0 if check(*sys.argv[1:3]) else 1)
    except (grpc.RpcError, TypeError) as e:
        print(f"not serving: {e}", file=sys.stderr)
        sys.exit(1)
