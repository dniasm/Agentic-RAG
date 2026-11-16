import json
from contextlib import contextmanager
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
    save_memory,
    search_memories,
    delete_conversation_memories,
)
from backend.library import activity, agent, catalog
from backend.models import database as db
from backend.models.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationOut,
    ConversationRename,
    MessageOut,
)
from backend.memory.batching import MemoryBatcher
from backend.tools.memory import extract_and_save_batch, summarize_exchange

log = logging.getLogger(__name__)

router = APIRouter(tags=["Chat"])

EPISODE_LIMIT = 3          # past-conversation memories added to each request
EPISODE_MAX_CHARS = 800    # characters kept from each (summaries are shorter; older episodes may not be)
EPISODE_RAW_MAX_CHARS = 400  # exchanges up to this long (question + answer) are saved as they are, not summarised

# Memory work (fact extraction, episode summary) runs after the reply is sent, one job at a time, so it
# adds no wait for the user and two quick messages can't update the same memory keys at once.
_memory_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="memory")
# Storing an episode can wait up to VOYAGE_SAVE_MAX_WAIT for a Voyage request slot (config/voyage.py), so it has its
# own worker: waiting on _memory_worker would hold up fact extraction, which the user's next message may wait for.
_episode_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="episodes")
MEMORY_FLUSH_WAIT_SECONDS = 30  # longest a new conversation waits for the user's pending facts to be saved


def _extract_batch(user_id: str, turns: list[dict]) -> None:
    """Runs on the memory worker. Batched extraction happens after the messages' own usage lines were logged,
    so its tokens get a line of their own."""
    tracker = UsageTracker()
    extract_and_save_batch(user_id, turns, callbacks=[tracker])
    if tracker.summary()["total"]["input"]:
        record_usage(tracker, tool="memory_batch", intent="memory", message=f"{len(turns)} message(s)",
                     batched_messages=len(turns))


_memory_batcher = MemoryBatcher(
    _extract_batch,
    idle_seconds=settings.memory_batch_idle_seconds,
    max_pending=settings.memory_batch_max,
    # The answer writer sees the last history_turns messages; flush before a waiting fact leaves that window
    # (one exchange early, since memory work runs after the reply).
    max_age=max(1, settings.history_turns // 2 - 1),
    schedule=lambda flush, user_id: _memory_worker.submit(flush, user_id),
)

# user_id -> conversation ids whose memory work is still queued or running on the worker
_queued_memory: dict[str, list[str]] = {}
_queued_lock = threading.Lock()


def _wait_for_memory(user_id: str, conversation_id: str) -> None:
    """Before a request in another conversation reads the user's memories, save the facts still waiting from
    the previous one. Within one conversation the history covers them, so nothing waits there."""
    with _queued_lock:
        queued_elsewhere = any(c != conversation_id for c in _queued_memory.get(user_id, []))
    if not (queued_elsewhere or _memory_batcher.has_pending(user_id, other_than_conversation=conversation_id)):
        return
    try:
        # FIFO worker: the queued memory jobs run first, then the flush.
        _memory_worker.submit(_memory_batcher.flush_user, user_id).result(timeout=MEMORY_FLUSH_WAIT_SECONDS)
    except Exception:
        log.exception("Waiting for pending memory extraction failed")


def flush_pending_memory(timeout: float = 60) -> None:
    """On shutdown: extract every user's pending messages."""
    try:
        _memory_worker.submit(_memory_batcher.flush_all).result(timeout=timeout)
    except Exception:
        log.exception("Flushing pending memory on shutdown failed")
_STREAM_END = object()  # marks the end of an answer's event stream


# ----------------------------------------------------------------
# Helpers

def _prepare(
    req: ChatRequest,
    user: dict,
) -> tuple[dict, list[dict], list[dict], list[str], dict]:
    if req.conversation_id:
        conv = db.get_conversation(user["id"], req.conversation_id)
        if not conv:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found",
            )
    else:
        conv = db.create_conversation(user["id"], req.message)

    recent = db.list_messages(conv["id"], limit=settings.history_turns)
    history = [{"role": m["role"], "content": m["content"]} for m in recent]
    last_answer = next((m for m in reversed(recent) if m["role"] == "assistant"), None)
    context = {
        "conversation_id": conv["id"],
        # the document agent waits for an answer to its question: the router is skipped
        "agent_pending": bool(last_answer) and last_answer.get("tool") == "manage_documents"
                         and agent.has_open_question(conv["id"]),
        # "rename that one": the documents the previous answer was based on
        "cited_doc_ids": [src["doc_id"] for src in (last_answer or {}).get("meta", {}).get("sources", [])
                          if src.get("doc_id")],
        # what the document agent's previous answer changed: "regenerate it", "udno" continue that work
        "agent_changed": _changes_in(last_answer),
        # a draft another part of the chat wrote, which the document agent never saw ("yup use that")
        "other_answer": last_answer["content"] if last_answer and last_answer.get("tool") != "manage_documents"
                        else None,
    }

    # Exact facts and preferences from SQLite.
    _wait_for_memory(user["id"], conv["id"])
    memories = db.list_memories(user["id"])

    # Relevant past conversations from semantic memory.
    try:
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

    doc_names = catalog.indexed_titles(user["id"])

    db.add_message(conv["id"], "user", req.message)

    return conv, history, memories, doc_names, context


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
        "actions": state.get("actions", []),   # document changes: Preview / Undo buttons, also after a reload
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
    with _queued_lock:
        _queued_memory.setdefault(user_id, []).append(conv["id"])
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
    """Background: queue the message for batched fact extraction, save the exchange as an episode, then log
    the request's tokens. Failures are logged; the answer has already been saved and sent."""
    try:
        _remember_exchange(user_id, conv_id, user_message, history, state, tracker, usage_context)
    finally:
        with _queued_lock:
            queued = _queued_memory.get(user_id, [])
            if conv_id in queued:
                queued.remove(conv_id)
            if not queued:
                _queued_memory.pop(user_id, None)


def _remember_exchange(user_id: str, conv_id: str, user_message: str, history: list[dict], state: dict,
                       tracker: UsageTracker, usage_context: dict) -> None:
    try:
        # Facts are extracted in batches (see _memory_batcher); this only queues the message.
        _memory_batcher.add(user_id, conv_id, user_message, history)
    except Exception:
        log.exception("Queueing the message for memory extraction failed")

    answer = (state.get("answer") or "").strip()
    question = (state.get("standalone_query") or user_message).strip()
    if (
        answer
        and len(question) >= 20
        and state.get("tool") not in ("greeting", "no_results", "manage_documents")
        and state.get("intent") != "error"
    ):
        try:
            raw = f"User asked: {question[:500]}\nAssistant answered: {answer[:800]}"
            # A short exchange stored as it is is about the size of its summary, so the memory call is skipped
            # (measured 2026-11-14: 116 of 281 stored exchanges were this short).
            short = len(question) + len(answer) <= EPISODE_RAW_MAX_CHARS
            summary = None if short else summarize_exchange(question, answer, callbacks=[tracker])
            episode = summary or raw
            _episode_worker.submit(_save_episode, user_id, episode, conv_id)
        except Exception:
            log.exception("Semantic memory persistence failed")

    record_usage(tracker, **usage_context)


def _save_episode(user_id: str, episode: str, conv_id: str) -> None:
    try:
        save_memory(user_id, episode, kind="episodic", conversation_id=conv_id)
    except Exception:
        log.exception("Saving the conversation episode failed")


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

_conversation_locks: dict[str, threading.Lock] = {}
_conversation_locks_guard = threading.Lock()


def _conversation_lock(conv_id: str | None) -> threading.Lock:
    """One message at a time per conversation, on every route: two quick messages must not race on its
    history or on the document agent's session. A new conversation has no id yet and needs none."""
    if not conv_id:
        return threading.Lock()
    with _conversation_locks_guard:
        return _conversation_locks.setdefault(conv_id, threading.Lock())

BUSY_WAIT_SECONDS = 2.0   # a second message waits this long for the first, then gets a 409


def _take(lock: threading.Lock) -> None:
    if not lock.acquire(timeout=BUSY_WAIT_SECONDS):
        raise HTTPException(status_code=409, detail="Still working on your previous message in this chat.")


def _changes_in(answer: dict | None) -> list[str]:
    """What a document agent's answer changed, in words ('edited "CV"'); empty for any other answer."""
    if not answer or answer.get("tool") != "manage_documents":
        return []
    words = {"edit": "edited", "rename": "renamed", "undo": "undid an edit to", "redo": "redid an edit to"}
    out = []
    for a in (answer.get("meta") or {}).get("actions") or []:
        title = a.get("new_title") or a.get("title")
        if a.get("type") in words and title:
            out.append(f'{words[a["type"]]} "{title}"')
    return out


@contextmanager
def _turn(conv_id: str | None):
    lock = _conversation_lock(conv_id)
    _take(lock)
    try:
        yield
    finally:
        lock.release()


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, user: dict = Depends(get_current_user)):
    with _turn(req.conversation_id):
        conv, history, memories, doc_names, context = _prepare(req, user)
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
                context=context,
            )
        except Exception as e:
            state = _error_state(e)

        return _persist(conv, state, started, user["id"], req.message, history, tracker, len(memories))


@router.post("/chat/stream")
def chat_stream(req: ChatRequest, user: dict = Depends(get_current_user)):
    """Stream metadata, status, trace steps, answer drafts, and final result."""
    lock = _conversation_lock(req.conversation_id)
    _take(lock)   # released by the worker thread when the answer is saved
    try:
        conv, history, memories, doc_names, context = _prepare(req, user)
    except BaseException:
        lock.release()
        raise
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
                context=context,
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
            lock.release()
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


@router.get("/conversations/{conv_id}/documents")
def conversation_documents(conv_id: str, user: dict = Depends(get_current_user)):
    """The documents this conversation renamed, edited or undid, newest first (the chat's side panel)."""
    if not db.get_conversation(user["id"], conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return activity.documents_changed_in(db.list_tool_messages(conv_id, "manage_documents"), user["id"],
                                         catalog.tasks_run_for(conv_id))


@router.get("/conversations/{conv_id}/pending")
def conversation_pending(conv_id: str, user: dict = Depends(get_current_user)):
    """How many tasks this conversation queued are still to run: the side panel looks again while any are."""
    if not db.get_conversation(user["id"], conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"tasks": catalog.pending_tasks_for(conv_id)}


@router.patch("/conversations/{conv_id}", response_model=ConversationOut)
def rename_conversation(
    conv_id: str,
    body: ConversationRename,
    user: dict = Depends(get_current_user),
):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title can't be empty")
    if not db.get_conversation(user["id"], conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.rename_conversation(user["id"], conv_id, title)
    return db.get_conversation(user["id"], conv_id)


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
