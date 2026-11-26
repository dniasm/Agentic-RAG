"""grpc.aio server bootstrap: health service, background tasks, graceful shutdown.

A `Service` is built, started and stopped the same way in production (`serve_forever`) and in tests
(`bind("localhost:0")`, `start`, `stop`), so tests exercise the real wiring.
"""
import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable, Coroutine
from typing import Any

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from services.common import config

log = logging.getLogger(__name__)
STOP_PATIENCE_SECONDS = 5.0  # how long past the grace period we wait for gRPC to finish stopping


class Service:
    def __init__(self, name: str):
        self.name = name
        self.server = grpc.aio.server()
        self._health = health.aio.HealthServicer()
        health_pb2_grpc.add_HealthServicer_to_server(self._health, self.server)
        self._grpc_names: list[str] = [""]  # "" is the whole-server health entry
        self._background: list[Callable[[], Coroutine[Any, Any, None]]] = []
        self._tasks: list[asyncio.Task] = []
        self._closers: list[Callable[[], Any]] = []
        self.port = 0

    def register(self, add_to_server: Callable, servicer: Any, grpc_name: str) -> None:
        """Attach a servicer and expose its fully-qualified name to grpc.health.v1."""
        add_to_server(servicer, self.server)
        self._grpc_names.append(grpc_name)

    def run_in_background(self, coro_fn: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """A loop (sweeper, worker) started with the server and cancelled before it stops."""
        self._background.append(coro_fn)

    def add_closer(self, closer: Callable[[], Any]) -> None:
        """Cleanup run during stop(), after background tasks are cancelled and the gRPC server has stopped
        (a sync callable or one returning an awaitable), e.g. closing the service's SQLite connection."""
        self._closers.append(closer)

    def bind(self, address: str) -> int:
        self.port = self.server.add_insecure_port(address)
        if self.port == 0:
            raise RuntimeError(f"{self.name}: could not bind {address}")
        return self.port

    async def start(self) -> None:
        await self.server.start()
        self._tasks = [asyncio.create_task(fn(), name=f"{self.name}-bg") for fn in self._background]
        for n in self._grpc_names:
            await self._health.set(n, health_pb2.HealthCheckResponse.SERVING)
        log.info("%s serving on port %s", self.name, self.port)

    async def stop(self, grace: float = 1.0) -> None:
        for n in self._grpc_names:
            await self._health.set(n, health_pb2.HealthCheckResponse.NOT_SERVING)
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        try:
            # grpc.aio's Server.stop() can wait forever on an open streaming call; a stuck shutdown must not wedge the process.
            await asyncio.wait_for(self.server.stop(grace), timeout=grace + STOP_PATIENCE_SECONDS)
        except asyncio.TimeoutError:
            log.warning("%s: the gRPC server did not stop within %.1fs; continuing the shutdown", self.name, grace + STOP_PATIENCE_SECONDS)
        for closer in self._closers:
            result = closer()
            if asyncio.iscoroutine(result):
                await result
        log.info("%s stopped", self.name)

    async def serve_forever(self) -> None:
        """Run until SIGINT/SIGTERM (or task cancellation)."""
        await self.start()
        stopping = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stopping.set)
            except (NotImplementedError, RuntimeError):  # Windows event loops have none: use the plain handler
                with contextlib.suppress(ValueError, OSError):  # not the main thread, or signal unsupported
                    signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stopping.set))
        try:
            await stopping.wait()
        finally:
            await asyncio.shield(self.stop())


def serve(service: Service, address: str | None = None) -> None:
    """Entry point for `python -m services.<name>`: bind (the service's configured address by default) and run until stopped."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    service.bind(address or config.listen_address(service.name))
    asyncio.run(service.serve_forever())
