"""Where each service listens: the one place that knows the names, env variables and default ports.

The orchestrator dials the same variable a service listens on (USER_SERVICE_ADDR=localhost:50061), so one
setting configures both ends. backend/config/settings.py takes its defaults from here.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# service name -> (env variable holding "host:port", default address, fully-qualified gRPC service name)
SERVICE_ADDRESSES = {
    "user": ("USER_SERVICE_ADDR", "localhost:50061", "agentic_rag.v1.UserService"),
    "conversation": ("CONVERSATION_SERVICE_ADDR", "localhost:50062", "agentic_rag.v1.ConversationService"),
    "memory": ("MEMORY_SERVICE_ADDR", "localhost:50063", "agentic_rag.v1.MemoryService"),
    "document": ("DOCUMENT_SERVICE_ADDR", "localhost:50064", "agentic_rag.v1.DocumentService"),
}

# Each service keeps its SQLite file here as <name>.db (override with SERVICES_DATA_DIR). Sits beside the
# orchestrator's own data, so it is already git-ignored (backend/data/).
DEFAULT_DATA_DIR = REPO_ROOT / "backend" / "data" / "services"


def default_address(name: str) -> str:
    return SERVICE_ADDRESSES[name][1]


def service_address(name: str) -> str:
    """The address a service listens on and the orchestrator dials."""
    env_var, default, _ = SERVICE_ADDRESSES[name]
    return os.environ.get(env_var) or default


def listen_address(name: str) -> str:
    """service_address(), with the host replaced by GRPC_LISTEN_HOST (e.g. 0.0.0.0) when that is set: in a
    container the address others dial is not one to bind to."""
    address = service_address(name)
    host = os.environ.get("GRPC_LISTEN_HOST")
    return f"{host}:{address.rsplit(':', 1)[1]}" if host else address


def data_dir() -> Path:
    return Path(os.environ.get("SERVICES_DATA_DIR") or DEFAULT_DATA_DIR)
