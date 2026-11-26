"""Run a real service inside the current process, for tests: its own thread and asyncio loop, an ephemeral port.

    running = RunningService(lambda: build_service(db))   # a package's build_service(); built on the service's own loop
    running.start()                                 # returns once it is serving
    running.address                                 # "localhost:<port>", what the orchestrator's clients dial
    running.stop()

It is the production wiring (services.common.server.Service) without a subprocess, so tests exercise the real
interceptors, health service and shutdown.
"""
import asyncio
import threading
from collections.abc import Callable

from services.common.server import Service


class RunningService:
    def __init__(self, build: Callable[[], Service], host: str = "localhost"):
        self._build = build   # grpc.aio binds a server to the loop it is created on, so it is built in the thread
        self.service: Service | None = None
        self.host = host
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    port = 0

    def start(self, timeout: float = 15.0) -> "RunningService":
        self._thread = threading.Thread(target=self._run, name="in-process-service", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout) or self._error:
            raise RuntimeError(f"service did not start: {self._error!r}")
        return self

    def _run(self) -> None:
        async def main() -> None:
            self._loop = asyncio.get_running_loop()
            self._stopping = asyncio.Event()
            try:
                self.service = self._build()
                self.port = self.service.bind(f"{self.host}:0")
                await self.service.start()
            except BaseException as e:   # reported to start()
                self._error = e
                self._ready.set()
                return
            self._ready.set()
            await self._stopping.wait()
            await self.service.stop(grace=0.1)

        asyncio.run(main())

    def stop(self) -> None:
        if self._loop and self._thread and self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._stopping.set)
            self._thread.join(timeout=15)
