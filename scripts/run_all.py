"""Start the gRPC services and the orchestrator (uvicorn app:app) together.

    python scripts/run_all.py              # start everything; Ctrl+C stops it all
    python scripts/run_all.py --port 8000  # orchestrator port (default 8000)
    python scripts/run_all.py --reload     # uvicorn --reload for the orchestrator
    python scripts/run_all.py --host 0.0.0.0 --port $PORT   # a host that routes to the orchestrator (Render)

In order: every target port must be free (else refuse, naming it, exit 1; nothing is killed); each gRPC service
starts as `python -m services.<name>` and must answer grpc.health.v1 with SERVING before the next one starts; then
the orchestrator, which must answer GET /api/health. Child output is prefixed with the process name. Ctrl+C, or
any child exiting, stops every child this script started (graceful, then terminate, then kill); exit 1 if one
crashed. Only processes this script started are ever signalled.

SERVICES is the registry: add an entry per service once its package `services/<name>/__main__.py` exists.
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.common import config  # noqa: E402

READY_TIMEOUT_SECONDS = 30.0
STOP_GRACE_SECONDS = 5.0
POLL_SECONDS = 0.25
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


@dataclass(frozen=True)
class Proc:
    """One process in the stack. kind "grpc": address from the service's env variable, ready on health Check;
    kind "http": `port` is fixed, ready when GET ready_path returns 200."""
    name: str
    argv: tuple[str, ...]
    kind: str
    port: int
    ready_path: str = "/"
    env: dict = field(default_factory=dict, hash=False, compare=False)


# Names of the services to start, in order, each run as `python -m services.<name>`.
SERVICES: list[str] = ["user", "conversation", "memory", "document"]


def service_proc(name: str) -> Proc:
    port = int(config.service_address(name).rsplit(":", 1)[1])
    return Proc(name, (sys.executable, "-m", f"services.{name}"), "grpc", port)


def orchestrator_proc(port: int = 8000, reload: bool = False, host: str | None = None) -> Proc:
    """host: the address uvicorn binds (its own default, 127.0.0.1, when None). The gRPC services never leave localhost."""
    argv = [sys.executable, "-m", "uvicorn", "app:app", "--port", str(port)]
    if host:
        argv += ["--host", host]
    if reload:
        argv.append("--reload")
    return Proc("orchestrator", tuple(argv), "http", port, ready_path="/api/health")


def build_plan(services: list[str] | None = None, port: int = 8000, reload: bool = False,
               host: str | None = None) -> list[Proc]:
    """The processes in start order: services first, the orchestrator last."""
    names = SERVICES if services is None else services
    unknown = [n for n in names if n not in config.SERVICE_ADDRESSES]
    if unknown:
        raise ValueError(f"unknown service(s): {', '.join(unknown)}")
    return [*(service_proc(n) for n in names), orchestrator_proc(port, reload, host)]


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def is_ready(proc: Proc) -> bool:
    try:
        if proc.kind == "grpc":
            from grpc_healthcheck import check
            return check(proc.port, "127.0.0.1", timeout=1.0)
        with urllib.request.urlopen(f"http://127.0.0.1:{proc.port}{proc.ready_path}", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def pump(proc: Proc, child: subprocess.Popen) -> None:
    for line in child.stdout:
        sys.stdout.write(f"[{proc.name:<12}] {line}")
        sys.stdout.flush()


def _stop_one(child: subprocess.Popen) -> None:
    """Graceful stop, then terminate, then kill; waits until the child is gone."""
    if child.poll() is None:
        try:
            child.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        except OSError:
            pass
    try:
        child.wait(STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        child.terminate()
        try:
            child.wait(2)
        except subprocess.TimeoutExpired:
            child.kill()


def stop_all(children: list[tuple[Proc, subprocess.Popen]]) -> None:
    """Reverse start order, each one fully stopped before the next is signalled: the orchestrator's last flush of
    usage counts can still reach the services it talks to."""
    for _, child in reversed(children):
        _stop_one(child)


def run(plan: list[Proc]) -> int:
    busy = [p for p in plan if port_in_use(p.port)]
    if busy:
        for p in busy:
            print(f"port {p.port} ({p.name}) is already in use; not starting anything", file=sys.stderr)
        return 1
    children: list[tuple[Proc, subprocess.Popen]] = []
    crashed = False
    try:
        for proc in plan:
            child = subprocess.Popen(
                proc.argv, cwd=REPO_ROOT, env={**os.environ, **proc.env, "PYTHONUNBUFFERED": "1"},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=CREATE_NEW_PROCESS_GROUP,
            )
            children.append((proc, child))
            threading.Thread(target=pump, args=(proc, child), daemon=True).start()
            started = time.monotonic()
            while not is_ready(proc):
                if child.poll() is not None or time.monotonic() - started > READY_TIMEOUT_SECONDS:
                    print(f"{proc.name} did not become ready", file=sys.stderr)
                    return 1
                time.sleep(POLL_SECONDS)
            print(f"{proc.name} ready on port {proc.port}")
        while all(child.poll() is None for _, child in children):
            time.sleep(POLL_SECONDS)
        crashed = True
        for proc, child in children:
            if child.poll() is not None:
                print(f"{proc.name} exited with code {child.returncode}", file=sys.stderr)
    except KeyboardInterrupt:
        print("stopping...")
    finally:
        stop_all(children)
    return 1 if crashed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000, help="orchestrator port")
    parser.add_argument("--host", default=None, help="address the orchestrator binds (default 127.0.0.1)")
    parser.add_argument("--reload", action="store_true", help="run uvicorn with --reload")
    args = parser.parse_args()
    return run(build_plan(port=args.port, reload=args.reload, host=args.host))


if __name__ == "__main__":
    sys.exit(main())
