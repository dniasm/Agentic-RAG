from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from backend import clients, version
from backend.api.dependencies import get_current_user

from backend.config.settings import settings
from backend.tools import web_search

router = APIRouter(
    prefix = "/health",
    tags = ["Health"]
)

@router.get("")
def health_check():
    return{
        "status" : "healthy",
        "llm" : settings.llm_model,
        "embeddings" : settings.embedding_model,
        "web_search" : " → ".join(web_search.provider_names()),
    }


@router.get("/services")
def services_health():
    """Each gRPC service's grpc.health.v1 answer (public, like /health). Always 200: the body says who is down;
    `all_up` is what a container check or the CI job waits for."""
    c = clients.get_clients()
    with ThreadPoolExecutor(max_workers=len(clients.SERVICES)) as pool:
        services = list(pool.map(c.probe, clients.SERVICES))
    return {
        "all_up": all(s["status"] == "up" for s in services),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "services": services,
    }


@router.get("/version")
def running_version(_: dict = Depends(get_current_user)):
    """The code this backend started with (backend/version.py): signed-in users only, the public route doesn't say."""
    return version.current()
