"""The messages on the WebSocket, in one place. The browser sends requests over REST; the socket only carries what the
server pushes, plus a ping from the client.

Every pushed message is an envelope `{seq, channel, type, request_id, data, ts}`:
  * seq  a number per account that only goes up (0 for connection messages that are not kept for replay);
  * request_id  the id of the REST request that started the work (its log lines carry the same id);
  * data  one of the models below, chosen by (channel, type).

  channel "system"   hello {user, seq}, resync {}, heartbeat {}, pong {}
  channel "chat"     meta, status, step, draft, final, error: all carry conversation_id and turn_id (the id the
                     browser put on POST /api/chat/stream), so a tab picks out its own answer
  channel "queue"    task (one queued task changed state), changed (a batch was added or cancelled: read the queue again)
  channel "library"  document (a file's indexing moved to a new stage)

The client sends `{"type": "ping"}` (answered with system/pong) and nothing else; any other message is ignored.
"""
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict


class _Data(BaseModel):
    model_config = ConfigDict(extra="allow")   # the answer's own fields (sources, trace, ...) pass through


class Hello(_Data):
    user: dict
    seq: int


class Empty(_Data):
    pass


class ChatEvent(_Data):
    conversation_id: str
    turn_id: str


class ChatMeta(ChatEvent):
    title: str


class ChatStatus(ChatEvent):
    node: str
    label: str


class ChatStep(ChatEvent):
    node: str
    detail: str = ""
    id: str | None = None
    data: dict[str, Any] = {}


class ChatDraft(ChatEvent):
    node: str | None = None
    text: str


class ChatFinal(ChatEvent):
    message_id: str
    answer: str


class ChatError(ChatEvent):
    message: str
    fatal: bool = False   # true: no final will follow (the answer could not be saved)


class QueueTask(_Data):
    batch_id: str
    task_id: int | str
    status: str
    reply: str | None = None


class LibraryDocument(_Data):
    doc_id: str
    stage: str          # reading | ocr | embedding | indexed | failed
    done: int | None = None    # ocr: pages read so far; embedding: passages embedded so far
    total: int | None = None
    title: str | None = None
    error: str | None = None


MODELS: dict[tuple[str, str], type[BaseModel]] = {
    ("system", "hello"): Hello, ("system", "resync"): Empty, ("system", "heartbeat"): Empty, ("system", "pong"): Empty,
    ("chat", "meta"): ChatMeta, ("chat", "status"): ChatStatus, ("chat", "step"): ChatStep,
    ("chat", "draft"): ChatDraft, ("chat", "final"): ChatFinal, ("chat", "error"): ChatError,
    ("queue", "task"): QueueTask, ("queue", "changed"): Empty,
    ("library", "document"): LibraryDocument,
}


def envelope(channel: str, type_: str, data: dict, *, seq: int = 0, request_id: str = "") -> dict:
    """A validated message ready to send. Raises ValueError for an unknown (channel, type) or data that doesn't fit."""
    model = MODELS.get((channel, type_))
    if model is None:
        raise ValueError(f"unknown WebSocket message {channel}/{type_}")
    body = model.model_validate(data).model_dump()
    return {"seq": seq, "channel": channel, "type": type_, "request_id": request_id, "data": body,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}
