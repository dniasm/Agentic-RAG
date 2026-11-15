"""Pydantic request / response schemas for the HTTP API."""
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- auth
class UserOut(BaseModel):
    id: str
    username: str


# ---------------------------------------------------------------- documents
class DocumentOut(BaseModel):
    id: str
    title: str            # the library's name for it (renamed by the agent); filename is the uploaded name
    filename: str
    ext: str = ""
    size_bytes: int
    pages: int
    chunks: int
    status: str
    error: str | None = None
    created_at: str


class UploadOut(BaseModel):
    # processing: indexing runs in the background; GET /documents shows when it is indexed or failed.
    status: Literal["processing", "duplicate"]
    filename: str
    chunks_indexed: int
    document: DocumentOut


# ---------------------------------------------------------------- chat
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    answer: str
    tool: str
    intent: str
    sources: list[dict[str, Any]] = []
    notices: list[str] = []
    rewritten_queries: list[str] = []
    evaluation: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []   # document changes (rename, edit, ...): Preview / Undo buttons
    latency_ms: int


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=60)


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    tool: str | None = None
    meta: dict[str, Any] = {}
    created_at: str
