import json
import logging
import re
import time
import queue
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from backend.api.dependencies import get_current_user
from backend.config.settings import settings
from backend.config.usage import UsageTracker, record as record_usage
from backend.graph.workflow import run_agent, stream_agent
from backend.memory.store import (
    ensure_memory_collection,
    save_memory,
    search_memories,
    delete_conversation_memories,
)
from backend.models import database as db
from backend.models.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationOut,
    MessageOut,
)
from backend.tools.memory import extract_and_save_memories, summarize_exchange

log = logging.getLogger(__name__)

router = APIRouter(tags=["Chat"])

EPISODE_LIMIT = 3          # past-conversation memories added to each request
EPISODE_MAX_CHARS = 800    # characters kept from each (summaries are shorter; older episodes may not be)

# Memory work (fact extraction, episode summary) runs after the reply is sent, one job at a time, so it
# adds no wait for the user and two quick messages can't update the same memory keys at once.
_memory_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="memory")
_STREAM_END = object()  # marks the end of an answer's event stream


# ----------------------------------------------------------------
# Helpers

def _prepare(
    req: ChatRequest,
    user: dict,
) -> tuple[dict, list[dict], list[dict], list[str]]:
    if req.conversation_id:
        conv = db.get_conversation(user["id"], req.conversation_id)
        if not conv:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found",
            )
    else:
        conv = db.create_conversation(user["id"], req.message)

    history = [
        {"role": m["role"], "content": m["content"]}
        for m in db.list_messages(
            conv["id"],
            limit=settings.history_turns,
        )
    ]

    # Exact facts and preferences from SQLite.
    memories = db.list_memories(user["id"])

    # Relevant past conversations from semantic memory.
    try:
        ensure_memory_collection()

        # Each episode is up to ~4,000 characters and goes into several prompts (router, knowledge gate,
        # answer), so only the closest few are used, shortened.
        semantic_memories = search_memories(
            user["id"],
            req.message,
            limit=EPISODE_LIMIT,
        )

        for memory in semantic_memories:
            score = float(memory.get("score", 0.0))

            if score < 0.45:
                continue

            text = memory["text"]
            if len(text) > EPISODE_MAX_CHARS:
                text = text[:EPISODE_MAX_CHARS].rstrip() + " …"

            memories.append({
                "memory_key": (
                    f"past_conversation_{memory.get('kind', 'episodic')}"
                ),
                "memory_value": text,
                "memory_source": "semantic",
                "memory_score": score,
                "conversation_id": memory.get("conversation_id"),
            })

    except Exception:
        log.exception("Semantic memory retrieval failed")

    doc_names = [
        d["filename"]
        for d in db.list_documents(
            user["id"],
            status="indexed",
        )
    ]

    db.add_message(conv["id"], "user", req.message)

    return conv, history, memories, doc_names


def _persist(
    conv: dict,
    state: dict,
    started: float,
    user_id: str,
    user_message: str,
    history: list[dict],
    tracker: UsageTracker,
    memory_count: int,
) -> dict:
    latency_ms = int((time.perf_counter() - started) * 1000)

    meta = {
        "intent": state.get("intent"),
        "sources": state.get("sources", []),
        "notices": state.get("notices", []),
        "rewritten_queries": state.get("rewritten_queries", []),
        "evaluation": state.get("evaluation"),
        "trace": state.get("trace", []),
        "latency_ms": latency_ms,
        "usage": tracker.summary(),  # the answer's tokens; memory extraction runs after this is saved
    }

    msg = db.add_message(
        conv["id"],
        "assistant",
        state.get("answer", ""),
        tool=state.get("tool"),
        meta=meta,
    )

    usage_context = dict(
        conversation_id=conv["id"],
        tool=state.get("tool"),
        intent=state.get("intent"),
        # Routing, so misroutes can be found and costed: what the router chose and the path taken.
        message=user_message[:200],
        source_preference=state.get("source_preference"),
        route_reason=state.get("route_reason"),
        path=[step.get("node") for step in state.get("trace", [])],
        message_chars=len(user_message),
        history_messages=len(history),
        memories=memory_count,
        latency_ms=latency_ms,
    )
    _memory_worker.submit(_remember, user_id, conv["id"], user_message, history, dict(state), tracker,
                          usage_context)

    return {
        "conversation_id": conv["id"],
        "message_id": msg["id"],
        "answer": state.get("answer", ""),
        "tool": state.get("tool", "direct"),
        **meta,
    }


def _remember(user_id: str, conv_id: str, user_message: str, history: list[dict], state: dict,
              tracker: UsageTracker, usage_context: dict) -> None:
    """Background: update the user's facts, save the exchange as an episode, then log the request's tokens
    (including the memory model's). Failures are logged; the answer has already been saved and sent."""
    try:
        extract_and_save_memories(user_id=user_id, message=user_message, history=history, callbacks=[tracker])
    except Exception:
        log.exception("Key-value memory extraction failed")

    answer = (state.get("answer") or "").strip()
    question = (state.get("standalone_query") or user_message).strip()
    if (
        answer
        and len(question) >= 20
        and state.get("tool") not in ("greeting", "no_results")
        and state.get("intent") != "error"
    ):
        try:
            summary = summarize_exchange(question, answer, callbacks=[tracker])
            episode = summary or f"User asked: {question[:500]}\nAssistant answered: {answer[:800]}"
            save_memory(user_id, episode, kind="episodic", conversation_id=conv_id)
        except Exception:
            log.exception("Semantic memory persistence failed")

    record_usage(tracker, **usage_context)


def _error_state(e: Exception) -> dict:
    log.exception("Agent failed")
    text = str(e)

    if "rate_limit_exceeded" in text or "Rate limit reached" in text:
        match = re.search(r"try again in ([\dhms.]+)", text)

        wait = (
            re.sub(r"\.\d+", "", match.group(1)).rstrip(".")
            if match
            else None
        )

        daily = "per day" in text
        model = re.search(r"for model `([^`]+)`", text)

        which = (
            " (the main model and its lighter backup are both out)"
            if model and model.group(1) == settings.fast_llm_model
            else f" for `{model.group(1)}`"
            if model
            else ""
        )

        answer = (
            "The AI service's free usage limit has been reached"
            + (" for today" if daily else " for the moment")
            + which
            + ", so I can't answer right now. "
            + (
                ("Capacity frees up gradually; " if daily else "Please ")
                + f"try again in about {wait}."
                if wait
                else "Please try again shortly."
            )
        )

        return {
            "answer": answer,
            "tool": "no_results",
            "intent": "error",
            "trace": [{
                "node": "error",
                "detail": f"Groq rate limit reached{which}.",
                "data": {},
            }],
        }

    return {
        "answer": (
            "Sorry — something went wrong while processing your request. "
            "Please try again."
        ),
        "tool": "direct",
        "intent": "error",
        "trace": [{
            "node": "error",
            "detail": str(e)[:300],
            "data": {},
        }],
    }


# ----------------------------------------------------------------
# Chat

@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, user: dict = Depends(get_current_user)):
    conv, history, memories, doc_names = _prepare(req, user)
    started = time.perf_counter()
    tracker = UsageTracker()

    try:
        state = run_agent(
            req.message,
            user,
            history,
            memories,
            doc_names,
            callbacks=[tracker],
        )
    except Exception as e:
        state = _error_state(e)

    return _persist(
        conv,
        state,
        started,
        user["id"],
        req.message,
        history,
        tracker,
        len(memories),
    )


@router.post("/chat/stream")
def chat_stream(req: ChatRequest, user: dict = Depends(get_current_user)):
    """Stream metadata, status, trace steps, answer drafts, and final result."""
    conv, history, memories, doc_names = _prepare(req, user)
    started = time.perf_counter()
    tracker = UsageTracker()

    def sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"

    # The agent runs in its own thread and always finishes and saves its answer. The response only relays its
    # events, so a client that disconnects (a page reload, a closed tab) loses the live view, not the answer:
    # it is in the conversation when the chat is opened again.
    events_q: queue.Queue = queue.Queue()

    def produce():
        final = None
        try:
            for kind, payload in stream_agent(
                req.message,
                user,
                history,
                memories,
                doc_names,
                callbacks=[tracker],
            ):
                if kind == "final":
                    final = payload
                else:
                    events_q.put((kind, payload))

        except Exception as e:
            final = _error_state(e)
            events_q.put(("step", final["trace"][0]))

        if final is None:
            final = {
                "answer": (
                    "Sorry — the agent finished without returning a result. "
                    "Please try again."
                ),
                "tool": "no_results",
                "intent": "error",
                "trace": [{
                    "node": "error",
                    "detail": "Stream ended without a final state.",
                    "data": {},
                }],
            }

        try:
            events_q.put((
                "final",
                _persist(
                    conv,
                    final,
                    started,
                    user["id"],
                    req.message,
                    history,
                    tracker,
                    len(memories),
                ),
            ))
        finally:
            events_q.put(_STREAM_END)

    threading.Thread(target=produce, name=f"chat-{conv['id'][:8]}", daemon=True).start()

    def events():
        yield sse(
            "meta",
            {
                "conversation_id": conv["id"],
                "title": conv["title"],
            },
        )
        while True:
            item = events_q.get()
            if item is _STREAM_END:
                return
            kind, payload = item
            yield sse(kind, payload)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ----------------------------------------------------------------
# Conversations

@router.get("/conversations", response_model=list[ConversationOut])
def conversations(user: dict = Depends(get_current_user)):
    return db.list_conversations(user["id"])


@router.get(
    "/conversations/{conv_id}/messages",
    response_model=list[MessageOut],
)
def conversation_messages(
    conv_id: str,
    user: dict = Depends(get_current_user),
):
    if not db.get_conversation(user["id"], conv_id):
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    return db.list_messages(conv_id)


@router.delete("/conversations/{conv_id}", status_code=204)
def delete_conversation(
    conv_id: str,
    user: dict = Depends(get_current_user),
):
    if not db.get_conversation(user["id"], conv_id):
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    # Remove semantic episodes before deleting the SQLite conversation.
    delete_conversation_memories(user["id"], conv_id)
    db.delete_conversation(user["id"], conv_id)


@router.get("/stats")
def stats(user: dict = Depends(get_current_user)):
    return db.user_stats(user["id"])
