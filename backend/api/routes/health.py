from fastapi import APIRouter, Depends

from backend import version
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


@router.get("/version")
def running_version(_: dict = Depends(get_current_user)):
    """The code this backend started with (backend/version.py): signed-in users only, the public route doesn't say."""
    return version.current()
