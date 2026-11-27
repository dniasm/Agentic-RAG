"""grpc.aio server bootstrap: health service, background tasks, graceful shutdown.

A `Service` is built, started and stopped the same way in production (`serve_forever`) and in tests
(`bind("localhost:0")`, `start`, `stop`), so tests exercise the real wiring.
"""
import asyncio
import contextlib
import logging
import signal
import time
from collections.abc import Callable, Coroutine
from typing import Any

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from services.common import config
from services.common.log import REQUEST_ID_HEADER, set_request_id, setup_logging, valid_request_id

log = logging.getLogger(__name__)
STOP_PATIENCE_SECONDS = 5.0  # how long past the grace period we wait for gRPC to finish stopping


HEALTH_CHECK_METHOD = "/grpc.health.v1.Health/Check"


class RequestIdInterceptor(grpc.aio.ServerInterceptor):
    """Continue the caller's request id (x-request-id metadata) inside each call and log one access line per call:
    method, status, time. The id is set inside the wrapped handler, which runs in the call's own task, so it cannot
    leak into another call. Calls the client streams to are passed through untouched."""

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None or (handler.request_streaming and not handler.response_streaming):
            return handler   # (a call that only the client streams is passed through untouched)
        md = {k: v for k, v in (handler_call_details.invocation_metadata or ()) if isinstance(v, str)}
        method = handler_call_details.method
        name = method.rsplit(".", 1)[-1]  # "UserService/GetUser"
        ok_level = logging.DEBUG if method == HEALTH_CHECK_METHOD else logging.INFO
        incoming = md.get(REQUEST_ID_HEADER)

        def line(started: float, code: grpc.StatusCode) -> None:
            ms = int((time.perf_counter() - started) * 1000)
            normal = (grpc.StatusCode.OK, grpc.StatusCode.NOT_FOUND, grpc.StatusCode.ALREADY_EXISTS)  # outcomes, not trouble
            log.log(ok_level if code in normal else logging.WARNING, "%s -> %s %dms", name, code.name, ms)

        def failed(started: float, context, error: BaseException) -> None:
            if isinstance(error, grpc.aio.AbortError):  # the servicer ended the call with a status on purpose
                line(started, context.code() or grpc.StatusCode.UNKNOWN)
            elif isinstance(error, asyncio.CancelledError):  # the client went away
                log.info("%s -> CANCELLED %dms", name, int((time.perf_counter() - started) * 1000))
            else:
                # grpc itself logs this error's traceback (with this call's request id): one traceback, not two
                log.error("%s failed: %s: %s", name, type(error).__name__, str(error)[:300])

        if handler.response_streaming:
            inner = handler.stream_stream if handler.request_streaming else handler.unary_stream

            async def stream(request, context):   # `request` is the request iterator for a call the client streams too
                set_request_id(valid_request_id(incoming))
                started = time.perf_counter()
                try:
                    async for item in inner(request, context):
                        yield item
                except (Exception, asyncio.CancelledError) as e:
                    failed(started, context, e)
                    raise
                line(started, context.code() or grpc.StatusCode.OK)

            make = grpc.stream_stream_rpc_method_handler if handler.request_streaming else grpc.unary_stream_rpc_method_handler
            return make(stream, handler.request_deserializer, handler.response_serializer)

        async def unary(request, context):
            set_request_id(valid_request_id(incoming))
            started = time.perf_counter()
            try:
                result = await handler.unary_unary(request, context)
            except (Exception, asyncio.CancelledError) as e:
                failed(started, context, e)
                raise
            line(started, context.code() or grpc.StatusCode.OK)
            return result

        return grpc.unary_unary_rpc_method_handler(unary, handler.request_deserializer, handler.response_serializer)


class Service:
    def __init__(self, name: str):
        self.name = name
        self.server = grpc.aio.server(interceptors=[RequestIdInterceptor()])
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


def serve(name: str, build: Callable[[], Service], address: str | None = None) -> None:
    """Entry point for `python -m services.<name>`: bind (the service's configured address by default) and run until stopped.
    `build` makes the Service; it is called inside the event loop, because grpc.aio ties a server to the loop it is created on."""
    setup_logging(name)   # logs/<service>/<date>.log, logs/audit/, the console (services/common/log.py)

    async def run() -> None:
        service = build()
        service.bind(address or config.listen_address(name))
        await service.serve_forever()

    asyncio.run(run())
