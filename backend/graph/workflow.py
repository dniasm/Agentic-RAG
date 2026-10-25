"""Agentic RAG workflow graph.

Source selection respects the user's intent:
- documents: RAG -> model knowledge -> web
- model knowledge: model knowledge -> web
- web: web -> model knowledge
- auto: documents (if available) -> model knowledge -> web

Every search retry is bounded. Failed sources are not retried after fallback,
and source-specific state is cleared when moving to another source.
"""

import time
from functools import lru_cache
from typing import Iterator

from langgraph.graph import END, START, StateGraph

from backend.config.settings import settings
from backend.graph import nodes
from backend.graph.state import AgentState


SOURCE_NODE = {
    "rag": "retrieve",
    "web_search": "web_search",
    "direct": "direct",
}

# The user's source preference determines the fallback order.
# Documents are only searched when available and appropriate for the request.
SOURCE_ORDERS = {
    "documents": ("rag", "direct", "web_search"),
    "model_knowledge": ("direct", "web_search"),
    "web": ("web_search", "direct"),
    "auto": ("rag", "direct", "web_search"),
}


def _source_order(state: AgentState) -> tuple[str, ...]:
    """Return the source order appropriate for this request."""
    preference = state.get("source_preference", "auto")
    order = SOURCE_ORDERS.get(preference, SOURCE_ORDERS["auto"])

    if not state.get("doc_names"):
        order = tuple(source for source in order if source != "rag")

    return order


def _budget(state: AgentState) -> int:
    """Maximum search rounds allowed for the current source."""
    if state.get("intent") == "web_search":
        return max(1, settings.max_web_attempts)

    if state.get("intent") == "rag":
        return max(1, settings.max_retrieval_attempts)

    return 1


def _next_source(state: AgentState) -> str | None:
    """Find the next untried source without revisiting exhausted sources."""
    tried = set(state.get("tried_sources", []))

    for source in _source_order(state):
        if source not in tried:
            return source

    return None


# ---------------------------------------------------------------------
# Conditional routing


def route_after_router(state: AgentState) -> str:
    intent = state.get("intent", "direct")

    if intent == "greeting":
        return "greeting"

    if intent == "direct":
        return "direct"

    if intent not in SOURCE_NODE:
        return "direct"

    # Rewrite unclear queries before sending them to a search engine.
    if not state.get("query_is_clear", True):
        return "rewrite"

    return SOURCE_NODE[intent]


def route_after_direct(state: AgentState) -> str:
    # A direct answer is returned only when the confidence gate accepts it.
    # If the gate refuses, the fallback node selects the next eligible source.
    return "finalize" if state.get("answer") else "fallback"


def route_after_rewrite(state: AgentState) -> str:
    if state.get("intent") == "web_search":
        return "web_search"

    return "retrieve"


def route_after_retrieve(state: AgentState) -> str:
    # If the retrieval node has established that the documents do not cover
    # the query, do not waste another round grading irrelevant passages.
    if state.get("docs_off_topic") or state.get("docs_search_failed"):
        return "fallback"

    return "grade"


def route_after_grade(state: AgentState) -> str:
    zone = state.get("match_zone")

    if zone:
        if zone == "high":
            return "generate_rag"

        # A partial cross-encoder match gets a bounded retry.
        if (
            zone == "mid"
            and state.get("attempts", 0)
            < min(_budget(state), 1 + settings.weak_match_retries)
        ):
            return "rewrite"

        # A low match is not useful enough to generate an answer from.
        return "fallback"

    if state.get("documents"):
        return "generate_rag"

    if state.get("attempts", 0) < _budget(state):
        return "rewrite"

    return "fallback"


def route_after_generate_rag(state: AgentState) -> str:
    if state.get("answer"):
        return "evaluate"

    if state.get("attempts", 0) < _budget(state):
        return "rewrite"

    return "fallback"


def route_after_web(state: AgentState) -> str:
    # A result list must be non-empty before answer generation.
    # Each search attempt replaces web_results, so old results cannot
    # accidentally make a failed retry look successful.
    if state.get("web_results"):
        return "generate_web"

    if state.get("attempts", 0) < _budget(state):
        return "rewrite"

    return "fallback"


def route_after_evaluate(state: AgentState) -> str:
    evaluation = state.get("evaluation") or {}

    if evaluation.get("sufficient") and evaluation.get("grounded", True):
        return "finalize"

    # Only search-based answers are retried here. Direct answers do not enter
    # this evaluation path in the current graph.
    if (
        state.get("tool") in ("rag", "web_search")
        and state.get("attempts", 0) < _budget(state)
    ):
        return "rewrite"

    return "fallback"


def route_after_fallback(state: AgentState) -> str:
    intent = state.get("intent")

    if intent == "exhausted":
        return "finalize" if state.get("best") else "no_results"

    return SOURCE_NODE.get(intent, "no_results")


# ---------------------------------------------------------------------
# Fallback node
#
# The fallback implementation lives here instead of calling
# nodes.fallback_node(), because that node's fixed hierarchy can select
# RAG after a user explicitly requested model knowledge or web search.


def fallback_node(state: AgentState) -> dict:
    current = state.get("intent", "direct")
    next_source = _next_source(state)

    if next_source is None:
        return {
            "intent": "exhausted",
            "trace": [
                nodes._step(
                    "fallback",
                    "All eligible sources have been tried.",
                    tried_sources=state.get("tried_sources", []),
                )
            ],
        }

    notice = nodes.FALLBACK_NOTICES.get((current, next_source))

    if current == "rag" and state.get("docs_search_failed"):
        notice = (
            "Your documents couldn't be searched because the search service "
            f"was unavailable, so this answer comes from "
            f"{nodes.SOURCE_NAMES[next_source]}."
        )

    notices = list(state.get("notices", []))
    if notice and notice not in notices:
        notices.append(notice)

    # Reset data belonging to the previous source. Preserve the best answer,
    # accumulated trace, rewritten-query history, and tried-source history.
    return {
        "intent": next_source,
        "attempts": 0,
        "search_queries": [state.get("standalone_query", state["query"])],
        "rewrite_reason": "",
        "answer": "",
        "tool": None,
        "sources": [],
        "evaluation": None,
        "documents": [],
        "web_results": [],
        "docs_off_topic": False,
        "docs_search_failed": False,
        "match_zone": "",
        "notices": notices,
        "trace": [
            nodes._step(
                "fallback",
                f"Nothing usable from {nodes.SOURCE_NAMES.get(current, current)} "
                f"→ trying {nodes.SOURCE_NAMES[next_source]}.",
                source=next_source,
            )
        ],
    }


# ---------------------------------------------------------------------
# Graph construction


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("router", nodes.router_node)
    graph.add_node("greeting", nodes.greeting_node)
    graph.add_node("direct", nodes.direct_node)
    graph.add_node("rewrite", nodes.rewrite_node)
    graph.add_node("retrieve", nodes.retrieve_node)
    graph.add_node("grade", nodes.grade_node)
    graph.add_node("generate_rag", nodes.generate_rag_node)
    graph.add_node("web_search", nodes.web_search_node)
    graph.add_node("generate_web", nodes.generate_web_node)
    graph.add_node("evaluate", nodes.evaluate_node)
    graph.add_node("fallback", fallback_node)
    graph.add_node("no_results", nodes.no_results_node)
    graph.add_node("finalize", nodes.finalize_node)

    graph.add_edge(START, "router")

    graph.add_conditional_edges(
        "router",
        route_after_router,
        ["greeting", "direct", "rewrite", "retrieve", "web_search"],
    )

    graph.add_edge("greeting", END)

    graph.add_conditional_edges(
        "direct",
        route_after_direct,
        ["finalize", "fallback"],
    )

    graph.add_conditional_edges(
        "rewrite",
        route_after_rewrite,
        ["retrieve", "web_search"],
    )

    graph.add_conditional_edges(
        "retrieve",
        route_after_retrieve,
        ["grade", "fallback"],
    )

    graph.add_conditional_edges(
        "grade",
        route_after_grade,
        ["generate_rag", "rewrite", "fallback"],
    )

    graph.add_conditional_edges(
        "generate_rag",
        route_after_generate_rag,
        ["evaluate", "rewrite", "fallback"],
    )

    graph.add_conditional_edges(
        "web_search",
        route_after_web,
        ["generate_web", "rewrite", "fallback"],
    )

    graph.add_edge("generate_web", "evaluate")

    graph.add_conditional_edges(
        "evaluate",
        route_after_evaluate,
        ["finalize", "rewrite", "fallback"],
    )

    graph.add_conditional_edges(
        "fallback",
        route_after_fallback,
        ["retrieve", "web_search", "direct", "finalize", "no_results"],
    )

    graph.add_edge("no_results", END)
    graph.add_edge("finalize", END)

    return graph.compile()


@lru_cache
def get_graph():
    return build_graph()


def _initial_state(
    query: str,
    user: dict,
    history: list[dict],
    memories: list[dict],
    doc_names: list[str],
) -> AgentState:
    return {
        "query": query,
        "user_id": user["id"],
        "username": user["username"],
        "history": history,
        "memories": memories,
        "doc_names": doc_names,
        "rewritten_queries": [],
        "tried_sources": [],
        "notices": [],
        "trace": [],
        "best": None,
        "evaluation": None,
        "search_queries": [],
        "attempts": 0,
        "documents": [],
        "web_results": [],
        "answer": "",
        "sources": [],
        "tool": None,
        "docs_off_topic": False,
        "docs_search_failed": False,
        "match_zone": "",
    }


# Safety net only. The conditional edges enforce normal retry limits.
_RECURSION_LIMIT = 60


def run_agent(
    query: str,
    user: dict,
    history: list[dict],
    memories: list[dict],
    doc_names: list[str],
) -> AgentState:
    return get_graph().invoke(
        _initial_state(query, user, history, memories, doc_names),
        {"recursion_limit": _RECURSION_LIMIT},
    )


# ---------------------------------------------------------------------
# Streaming interface. Keep event names compatible with the frontend.


STATUS_LABELS = {
    "router": "Reading your message…",
    "rewrite": "Rewording the question…",
    "retrieve": "Searching your documents…",
    "grade": "Checking which passages are relevant…",
    "generate_rag": "Writing the answer from your documents…",
    "web_search": "Searching the web…",
    "generate_web": "Writing the answer from web results…",
    "direct": "Answering from the model's own knowledge…",
    "greeting": "Replying…",
    "evaluate": "Checking the answer…",
    "fallback": "Trying the next source…",
}

DRAFT_NODES = {"generate_rag", "generate_web", "direct", "greeting"}
_SENTINELS = ("NO_ANSWER_IN_DOCUMENTS", "NEEDS_WEB_SEARCH")
_DRAFT_EVERY_CHARS = 30
_DRAFT_EVERY_SECONDS = 0.15

# These keys have append reducers in AgentState. Updates streamed from
# LangGraph are partial updates, so reconstruct the accumulated values locally.
_APPEND_KEYS = ("trace", "rewritten_queries")


def _looks_like_sentinel(text: str) -> bool:
    value = text.strip()
    return (
        any(sentinel.startswith(value) or value.startswith(sentinel)
            for sentinel in _SENTINELS)
        if value
        else True
    )


def stream_agent(
    query: str,
    user: dict,
    history: list[dict],
    memories: list[dict],
    doc_names: list[str],
    callbacks: list | None = None,
) -> Iterator[tuple[str, dict]]:
    """Yield status, trace, draft, and final events for the UI."""
    from backend.tools import clean_answer

    state: dict = _initial_state(query, user, history, memories, doc_names)
    sent_steps: set[str] = set()
    draft, draft_node, last_len, last_time = "", None, 0, 0.0

    def flush_draft(force: bool = False):
        nonlocal last_len, last_time

        now = time.monotonic()
        grew = len(draft) - last_len

        if draft_node and not _looks_like_sentinel(draft) and (
            force
            or grew >= _DRAFT_EVERY_CHARS
            or (grew > 0 and now - last_time >= _DRAFT_EVERY_SECONDS)
        ):
            last_len, last_time = len(draft), now
            return (
                "draft",
                {"node": draft_node, "text": clean_answer(draft)},
            )

        return None

    config = {"recursion_limit": _RECURSION_LIMIT}
    if callbacks:
        config["callbacks"] = callbacks

    stream = get_graph().stream(
        state,
        config,
        stream_mode=["tasks", "custom", "messages", "updates"],
    )

    for mode, chunk in stream:
        if mode == "tasks":
            if "input" in chunk:
                name = chunk["name"]

                if name in DRAFT_NODES:
                    draft, draft_node, last_len = "", name, 0
                    last_time = time.monotonic()

                if name in STATUS_LABELS:
                    yield "status", {
                        "node": name,
                        "label": STATUS_LABELS[name],
                    }

            elif chunk.get("name") == draft_node:
                event = flush_draft(force=True)
                if event:
                    yield event
                draft_node = None

        elif mode == "custom":
            step = chunk.get("step") if isinstance(chunk, dict) else None

            if step and step.get("id") not in sent_steps:
                sent_steps.add(step.get("id"))
                yield "step", step

        elif mode == "messages":
            message, metadata = chunk

            if (
                metadata.get("langgraph_node") == draft_node
                and isinstance(message.content, str)
                and message.content
            ):
                draft += message.content
                event = flush_draft()
                if event:
                    yield event

        elif mode == "updates":
            for node_update in chunk.values():
                if not node_update:
                    continue

                for step in node_update.get("trace", []):
                    step_id = step.get("id")
                    if step_id not in sent_steps:
                        sent_steps.add(step_id)
                        yield "step", step

                for key, value in node_update.items():
                    if key in _APPEND_KEYS:
                        state[key] = state.get(key, []) + value
                    else:
                        state[key] = value

    yield "final", state