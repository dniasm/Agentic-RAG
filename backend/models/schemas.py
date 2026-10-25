"""Pydantic request / response schemas for the HTTP API."""
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- auth
class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=128)


class UserOut(BaseModel):
    id: str
    username: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------------------------------------------------------------- documents
class DocumentOut(BaseModel):
    id: str
    filename: str
    size_bytes: int
    pages: int
    chunks: int
    status: str
    error: str | None = None
    created_at: str


class UploadOut(BaseModel):
    status: Literal["success", "duplicate"]
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
    latency_ms: int


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    tool: str | None = None
    meta: dict[str, Any] = {}
    created_at: str
