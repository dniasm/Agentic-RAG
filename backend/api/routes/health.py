from fastapi import APIRouter

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
