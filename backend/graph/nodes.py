"""LangGraph nodes for the multi-user agentic RAG workflow."""

import logging
import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from backend.config.llm import consume_backup_flag
from backend.config.settings import settings
from backend.evaluation.evaluator import evaluate_answer, grade_documents
from backend.evaluation.reviewer import verified_decision
from backend.graph.state import AgentState
from backend.rag import rerank
from backend.rag.retrieval import retrieve
from backend.tools import direct_answer, greeting, rag, web_search
from backend.tools import history_to_text, today

log = logging.getLogger(__name__)

HIERARCHY = ("rag", "direct", "web_search")

SOURCE_NAMES = {
    "rag": "your documents",
    "web_search": "a web search",
    "direct": "the model's own knowledge",
}

BACKUP_NOTICE = (
    "The main model's free usage limit was reached, so this answer was "
    "written by the lighter model and may be less detailed."
)

NO_DOCS_NOTICE = (
    "You asked for an answer from your uploaded documents, but no documents "
    "are available. I will try answering from the model's own knowledge "
    "instead; this answer will not be document-grounded."
)

FALLBACK_NOTICES = {
    ("rag", "web_search"): (
        "Information matching your question wasn't found in your documents, "
        "so it was resolved with a web search."
    ),
    ("rag", "direct"): (
        "Information matching your question wasn't found in your documents, "
        "so this answer comes from the model's own knowledge."
    ),
    ("web_search", "rag"): (
        "The web search didn't return usable results, so your documents "
        "were searched instead."
    ),
    ("web_search", "direct"): (
        "The web search didn't return a sufficiently reliable answer, "
        "so this answer comes from the model's own knowledge."
    ),
    ("direct", "web_search"): (
        "The model's own knowledge wasn't sufficient, so this was resolved "
        "with a web search."
    ),
    ("direct", "rag"): (
        "The model's own knowledge wasn't sufficient, so your documents "
        "were searched."
    ),
}


def _step(node: str, detail: str, **data) -> dict:
    """Create a trace step for the UI."""
    return {
        "id": uuid.uuid4().hex[:12],
        "node": node,
        "detail": detail,
        "data": data,
    }


def _with(items: list | None, item) -> list:
    """Append an item to a list only if it is not already present."""
    items = list(items or [])
    return items if item in items else items + [item]


def _normalize_query(query: str) -> str:
    """Normalize whitespace for query comparison."""
    return " ".join((query or "").split())


def _query_key(query: str) -> str:
    """Create a case-insensitive comparison key."""
    return _normalize_query(query).casefold()


def _notices_after_writing(state: AgentState) -> list[str]:
    """Preserve notices and include the backup-model notice when applicable."""
    notices = list(state.get("notices", []))

    if consume_backup_flag() and BACKUP_NOTICE not in notices:
        notices.append(BACKUP_NOTICE)

    return notices


def _source_order(state: AgentState) -> tuple[str, ...]:
    """Choose fallback order while respecting the requested source."""
    preference = state.get("source_preference", "auto")

    if preference == "web":
        # The requested web source is tried first. If it fails, use documents
        # where available, then model knowledge.
        return ("web_search", "rag", "direct")

    if preference == "documents":
        return ("rag", "direct", "web_search")

    if preference == "model_knowledge":
        return ("direct", "rag", "web_search")

    # Auto mode follows the default hierarchy.
    return HIERARCHY


def next_source(state: AgentState) -> str | None:
    """Return the next eligible source that has not been tried."""
    tried = set(state.get("tried_sources", []))
    docs_available = bool(state.get("doc_names"))

    for source in _source_order(state):
        if source == "rag" and not docs_available:
            continue

        if source not in tried:
            return source

    return None


# ============================================================
# 1. Intake router
# ============================================================

class Route(BaseModel):
    message_type: Literal["small_talk", "information_request"] = Field(
        description=(
            "small_talk only when the message has no information need at all."
        )
    )
    source_preference: Literal[
        "auto", "documents", "web", "model_knowledge"
    ] = Field(
        description=(
            "Choose documents for questions about uploaded material, web for "
            "current or explicitly online information, model_knowledge for "
            "ordinary self-contained questions, and auto only when the source "
            "genuinely cannot be determined."
        )
    )
    reasoning: str = Field(
        description="One short sentence explaining the classification."
    )
    query_is_clear: bool = Field(
        description=(
            "False if the query remains vague or ambiguous after considering "
            "conversation history."
        )
    )
    standalone_query: str = Field(
        description=(
            "Rewrite the request minimally so it is self-contained, preserving "
            "the user's intent and all supplied names, dates, and facts."
        )
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence in the routing decision."
    )
    confidence_required: bool = Field(
        description=(
            "True for factual, verifiable, or time-sensitive answers. False "
            "for subjective opinions, philosophical interpretation, and "
            "conversational reasoning."
        )
    )


ROUTER_PROMPT = """You are the intake step of a research assistant.
Do not answer the user. Classify the latest message using the conversation,
available documents, and supplied persistent memories.

Today is {today}.

SOURCE SELECTION
- model_knowledge: ordinary explanations, coding, calculations, reasoning,
  opinions, general questions, and follow-ups answerable without external
  information.
- documents: the user explicitly wants information from uploaded documents.
- web: current, recent, time-sensitive, or explicitly online information.
- auto: only when the appropriate source genuinely cannot be determined.

MESSAGE TYPE
- small_talk: greetings, thanks, casual conversation, and questions about
  the assistant itself.
- information_request: requests needing an informative answer, including
  follow-ups, factual questions, coding, calculations, and how-to questions.
- A challenge such as "why?", "are you sure?", or "that seems wrong" is an
  information_request when it asks for clarification or verification.

CONVERSATION CONTINUITY
Use relevant conversation history to resolve references and follow-ups.
Do not search externally for facts already established in the supplied
conversation or memories. Do not invent missing context.
Rewrite follow-ups so their intended meaning is preserved.

CONFIDENCE REQUIREMENT
Set confidence_required=false for subjective opinions, philosophical
interpretation, creative reasoning, and questions asking what you think.
Set it true for verifiable facts, numerical values, historical or scientific
claims, current information, and claims requiring source verification.

STANDALONE QUERY
Rewrite the latest request only as much as needed to make it self-contained.
Preserve its intent, language, names, dates, and numbers. Never add facts.
Set query_is_clear based on the request AND its conversation context.

Do not answer the user's question. Do not choose web merely because a
question is factual. Do not choose documents merely because documents exist.

Uploaded documents ({doc_count}): {doc_names}

Persistent user memories:
{memories}

Recent conversation:
{history}

Latest user message:
{query}
"""


def router_node(state: AgentState) -> dict:
    docs = state.get("doc_names", [])
    memories = state.get("memories", [])

    memory_text = "\n".join(
        f"{m['memory_key']}: {m['memory_value']}"
        for m in memories
    ) or "(none)"

    prompt = ROUTER_PROMPT.format(
        today=today(),
        doc_count=len(docs),
        doc_names=", ".join(docs[:40]) or "(none uploaded)",
        memories=memory_text,
        history=history_to_text(state.get("history", [])[-6:]),
        query=state["query"],
    )

    review_context = (
        "Review the intake classification. Correct genuine errors without "
        "replacing valid decisions with a generic default.\n"
        "Use model_knowledge for ordinary self-contained questions, documents "
        "when the user wants their uploaded material, and web for current or "
        "explicitly online information. Preserve conversational context.\n\n"
        f"Documents: {', '.join(docs[:20]) or '(none)'}\n"
        f"Memories:\n{memory_text}\n"
        f"Recent conversation:\n"
        f"{history_to_text(state.get('history', [])[-2:], max_chars=200)}\n"
        f"Latest message: {state['query']}"
    )

    review_steps: list[dict] = []

    try:
        r, review_steps = verified_decision(
            prompt,
            Route,
            "intake decision",
            review_context=review_context,
        )

        kind = r.message_type
        pref = r.source_preference
        clear = r.query_is_clear
        confidence_required = r.confidence_required
        standalone = r.standalone_query.strip()
        reason = r.reasoning

    except Exception as exc:
        log.warning("Router failed; using safe defaults: %s", exc)

        kind = "information_request"
        pref = "auto"
        clear = True
        standalone = state["query"]
        reason = "Router error; using the default source order."
        confidence_required = True

    standalone = standalone or state["query"]
    notices: list[str] = []

    if kind == "small_talk":
        intent = "greeting"
        detail = f"Small talk. {reason}"

    elif pref == "documents" and docs:
        intent = "rag"
        detail = f"User requested uploaded documents. {reason}"

    elif pref == "documents":
        intent = "direct"
        notices.append(NO_DOCS_NOTICE)
        detail = (
            "No uploaded documents are available; trying model knowledge "
            f"with an explicit limitation. {reason}"
        )

    elif pref == "model_knowledge":
        intent = "direct"
        detail = f"Answering from model knowledge. {reason}"

    elif pref == "web":
        intent = "web_search"
        detail = f"Using web search. {reason}"

    else:
        intent = "direct"
        detail = f"Using model knowledge first in auto mode. {reason}"

    if intent in ("rag", "web_search") and not clear:
        detail += " The query is unclear and should be rewritten before searching."

    log.info(
        "Router decision: intent=%s preference=%s clear=%s query=%r reason=%s",
        intent,
        pref,
        clear,
        standalone,
        reason,
    )

    return {
        "intent": intent,
        "source_preference": pref,
        "query_is_clear": clear,
        "confidence_required": confidence_required,
        "standalone_query": standalone,
        "route_reason": reason,
        "search_queries": [standalone],
        "attempts": 0,
        "tried_sources": [],
        "notices": notices,
        "rewrite_reason": "" if clear else "vague_query",
        "trace": [
            _step(
                "router",
                detail,
                intent=intent,
                source_preference=pref,
                query_is_clear=clear,
                standalone_query=standalone,
            ),
            *review_steps,
        ],
    }


# ============================================================
# 2. Greeting
# ============================================================

def greeting_node(state: AgentState) -> dict:
    text = greeting.respond(
        state["query"],
        state.get("history", []),
        state.get("username", "there"),
        len(state.get("doc_names", [])),
    )

    return {
        "answer": text,
        "tool": "greeting",
        "sources": [],
        "evaluation": None,
        "notices": [],
        "trace": [
            _step("greeting", "Responded conversationally.")
        ],
    }


# ============================================================
# 3. Model knowledge
# ============================================================

def direct_node(state: AgentState) -> dict:
    text, needs_web, gate = direct_answer.answer(
        state["standalone_query"],
        state.get("history", []),
        state.get("confidence_required", True),
        state.get("memories", []),
    )

    tried = _with(state.get("tried_sources"), "direct")
    notices = _notices_after_writing(state)

    gate_data = (
        {
            "confidence": gate.confidence,
            "time_sensitive": gate.time_sensitive,
            "references_user_material": gate.references_user_material,
            "gate_reason": gate.reason,
        }
        if gate
        else {"gate": "skipped"}
    )

    if needs_web:
        return {
            "answer": "",
            "tool": None,
            "sources": [],
            "evaluation": None,
            "tried_sources": tried,
            "notices": notices,
            "trace": [
                _step(
                    "direct_answer",
                    "The confidence gate rejected a direct answer.",
                    **gate_data,
                )
            ],
        }

    return {
        "answer": text,
        "tool": "direct",
        "sources": [],
        "evaluation": None,
        "tried_sources": tried,
        "notices": notices,
        "trace": [
            _step(
                "direct_answer",
                "Answered from model knowledge.",
                **gate_data,
            )
        ],
    }


# ============================================================
# 4. Query rewriting
# ============================================================

class Rewrite(BaseModel):
    rewritten_query: str = Field(
        description="A clearer, more specific search query."
    )
    expansions: list[str] = Field(
        description=(
            "Up to three meaningfully different alternative queries, using "
            "different vocabulary or broader search phrasing."
        )
    )
    strategy: str = Field(
        description="One short sentence describing the rewrite."
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence that the rewritten query will help."
    )


REWRITE_PROMPT = """Improve a search query for {target}.
Today is {today}.

Original user question: {query}
Self-contained version: {standalone}
Recent conversation:
{history}
{docs_line}

Why rewriting is needed: {reason}
{feedback}

Queries already tried; do not repeat them:
{tried}

Attempt number: {attempt}

Attempt 1: resolve ambiguity, fix wording, and use precise terminology.
Later attempts: broaden the search, use different vocabulary, or split the
question into useful sub-questions.

Preserve the user's intent. Never invent facts or insert an expected answer.
{extra}
"""


_REASONS = {
    "vague_query": (
        "The query is vague or ambiguous and would retrieve poorly as written."
    ),
    "no_relevant_docs": (
        "The previous document search returned no relevant passages."
    ),
    "insufficient_answer": (
        "The evaluator judged the previous answer insufficient."
    ),
    "no_web_results": (
        "The web search returned no usable results."
    ),
    "weak_match": (
        "The documents contain related material but no clear answer."
    ),
}


def rewrite_node(state: AgentState) -> dict:
    is_web = state["intent"] == "web_search"

    standalone = _normalize_query(state["standalone_query"])
    previous_rewrites = state.get("rewritten_queries", [])

    tried = list(
        dict.fromkeys(
            q for q in [standalone, *previous_rewrites]
            if _normalize_query(q)
        )
    )

    reason_key = state.get("rewrite_reason") or "vague_query"
    missing = (state.get("evaluation") or {}).get("missing", "")

    prompt = REWRITE_PROMPT.format(
        target=(
            "a web search engine"
            if is_web
            else "semantic search over the user's documents"
        ),
        today=today(),
        query=state["query"],
        standalone=standalone,
        history=history_to_text(state.get("history", [])[-4:]),
        docs_line=(
            ""
            if is_web
            else f"User documents: {', '.join(state.get('doc_names', [])[:30])}"
        ),
        reason=_REASONS.get(reason_key, reason_key),
        feedback=(
            f"Evaluator feedback on missing information: {missing}"
            if missing
            else ""
        ),
        tried="; ".join(tried),
        attempt=max(1, state.get("attempts", 0) + 1),
        extra=(
            "Use concise search-engine keywords and preserve explicit dates."
            if is_web
            else "Phrase queries as statements that could appear in the documents."
        ),
    )

    review_context = (
        "Rewrite a search query while preserving the user's intent. "
        "Do not invent facts, include the expected answer, or repeat tried "
        "queries. Alternatives should use different vocabulary.\n\n"
        f"Original question: {state['query']}\n"
        f"Self-contained version: {standalone}\n"
        f"Reason: {_REASONS.get(reason_key, reason_key)}\n"
        f"Already tried: {'; '.join(tried)}\n"
        f"Evaluator feedback: {missing}"
    )

    review_steps: list[dict] = []

    try:
        r, review_steps = verified_decision(
            prompt,
            Rewrite,
            "query rewrite",
            review_context=review_context,
        )

        rewritten = _normalize_query(r.rewritten_query) or standalone
        expansions = [
            _normalize_query(q)
            for q in r.expansions
            if _normalize_query(q)
        ]
        strategy = r.strategy

    except Exception as exc:
        log.warning("Rewrite failed: %s", exc)
        rewritten = standalone
        expansions = []
        strategy = "Rewrite failed; retained the original query."

    # Web search uses at most two queries per round. RAG can use more.
    candidates = (
        [rewritten, *expansions[:1]]
        if is_web
        else [rewritten, *expansions[:3]]
    )

    # Deduplicate the candidates themselves.
    unique_candidates = list(
        dict.fromkeys(q for q in candidates if _normalize_query(q))
    )

    # Do not silently send duplicates when genuinely new queries exist.
    tried_keys = {_query_key(q) for q in tried}
    new_queries = [
        q for q in unique_candidates
        if _query_key(q) not in tried_keys
    ]

    if new_queries:
        search_queries = new_queries
    else:
        # Keep the graph's search input valid. The workflow must enforce the
        # attempt budget so repeated fallback queries cannot loop forever.
        search_queries = [standalone]
        strategy += " No new query was produced."

    return {
        "search_queries": search_queries,
        # AgentState uses operator.add for this field, so this appends the
        # rewrite to the accumulated history rather than replacing it.
        "rewritten_queries": [rewritten],
        "trace": [
            _step(
                "rewrite_query",
                f"{'Expanded' if expansions else 'Rewrote'} query "
                f"({reason_key.replace('_', ' ')}): {strategy}",
                rewritten=rewritten,
                expansions=expansions,
                search_queries=search_queries,
            ),
            *review_steps,
        ],
    }


# ============================================================
# 5. RAG: retrieve -> grade -> generate
# ============================================================

def retrieve_node(state: AgentState) -> dict:
    queries = state.get("search_queries") or [
        state["standalone_query"]
    ]
    attempt = state.get("attempts", 0) + 1
    use_rerank = rerank.available()

    k = settings.rerank_candidates - 2 if use_rerank else None

    hits, failure = None, None

    for wait in (0, 2):
        if wait:
            time.sleep(wait)

        try:
            hits = retrieve(
                queries,
                state["user_id"],
                k=k,
                min_score=0.0 if use_rerank else None,
            )
            break
        except Exception as exc:
            failure = exc
            log.warning(
                "Document search failed%s: %s",
                ", retrying" if not wait else "",
                exc,
            )

    if hits is None:
        return {
            "documents": [],
            "attempts": attempt,
            "docs_off_topic": True,
            "docs_search_failed": True,
            "tried_sources": _with(state.get("tried_sources"), "rag"),
            "trace": [
                _step(
                    "retrieve",
                    "Your documents could not be searched because the "
                    "search service was unavailable.",
                    error=str(failure)[:200],
                )
            ],
        }

    top = max((h.get("score", 0.0) for h in hits), default=None)
    keyword_only = bool(hits) and all(
        h.get("dense_rank") is None for h in hits
    )

    mode = (
        "keyword search only (meaning search unavailable)"
        if keyword_only
        else f"{settings.retrieval_mode} search"
    )

    detail = (
        f"Attempt {attempt}: {mode}; {len(queries)} quer"
        f"{'y' if len(queries) == 1 else 'ies'}, "
        f"{len(hits)} candidate chunk(s)"
    )

    if use_rerank:
        return {
            "documents": hits,
            "attempts": attempt,
            "docs_off_topic": not hits,
            "docs_search_failed": False,
            "tried_sources": _with(state.get("tried_sources"), "rag"),
            "trace": [
                _step(
                    "retrieve",
                    detail + ("." if hits else "; no candidates found."),
                    queries=queries,
                    candidates=len(hits),
                    top_score=top,
                    keyword_only=keyword_only,
                )
            ],
        }

    off_topic = (
        keyword_only
        or top is None
        or top < settings.rag_skip_score
    )

    if off_topic:
        detail += (
            f"; best match {top:.2f} is below "
            f"{settings.rag_skip_score:.2f}."
            if top is not None
            else "; no useful candidates found."
        )
    else:
        detail += f" (best match {top:.2f})."

    return {
        "documents": hits,
        "attempts": attempt,
        "docs_off_topic": off_topic,
        "docs_search_failed": False,
        "tried_sources": _with(state.get("tried_sources"), "rag"),
        "trace": [
            _step(
                "retrieve",
                detail,
                queries=queries,
                candidates=len(hits),
                top_score=top,
                keyword_only=keyword_only,
            )
        ],
    }


_ZONE_TEXT = {
    "high": "strong match",
    "mid": "partial match",
    "low": "no real match",
}


def grade_node(state: AgentState) -> dict:
    candidates = state.get("documents", [])

    if rerank.available() and candidates:
        ranked = rerank.score(
            state["standalone_query"],
            candidates,
        )

        if not ranked:
            return {
                "documents": [],
                "match_zone": "low",
                "rewrite_reason": "no_relevant_docs",
                "trace": [
                    _step(
                        "grade_documents",
                        "The reranker returned no usable passages.",
                    )
                ],
            }

        best = ranked[0]["relevance"]
        zone = rerank.zone(best)
        chosen = rerank.mmr_select(ranked) if zone != "low" else []

        detail = (
            f"Reranked {len(ranked)} candidates locally: "
            f"best relevance {best:.2f} -> {_ZONE_TEXT[zone]}"
        )

        if chosen:
            detail += f"; selected {len(chosen)} diverse passages using MMR."
        else:
            detail += " No passages selected."

        update = {
            "documents": chosen if zone == "high" else [],
            "match_zone": zone,
            "trace": [
                _step(
                    "grade_documents",
                    detail,
                    best_relevance=best,
                    zone=zone,
                    relevances=[
                        h["relevance"] for h in ranked[:8]
                    ],
                )
            ],
        }

        if zone == "mid":
            update["rewrite_reason"] = "weak_match"
        elif zone == "low":
            update["rewrite_reason"] = "no_relevant_docs"

        return update

    relevant, review_steps = grade_documents(
        state["standalone_query"],
        candidates,
    )

    update = {
        "documents": relevant,
        "trace": [
            _step(
                "grade_documents",
                f"{len(relevant)}/{len(candidates)} chunks judged relevant.",
                relevant=len(relevant),
                candidates=len(candidates),
            ),
            *review_steps,
        ],
    }

    if not relevant:
        update["rewrite_reason"] = "no_relevant_docs"

    return update


def generate_rag_node(state: AgentState) -> dict:
    docs = state.get("documents", [])

    text = rag.generate(
        state["standalone_query"],
        docs,
        state.get("history", []),
    )

    notices = _notices_after_writing(state)

    if not text:
        return {
            "answer": "",
            "tool": None,
            "sources": [],
            "evaluation": None,
            "documents": [],
            "rewrite_reason": "no_relevant_docs",
            "notices": notices,
            "trace": [
                _step(
                    "generate",
                    f"The {len(docs)} selected chunks did not answer the question.",
                )
            ],
        }

    return {
        "answer": text,
        "tool": "rag",
        "sources": rag.to_sources(docs),
        "notices": notices,
        "trace": [
            _step(
                "generate",
                f"Generated an answer from {len(docs)} document chunks.",
            )
        ],
    }


# ============================================================
# 6. Web search -> generate
# ============================================================

def web_search_node(state: AgentState) -> dict:
    """Search using distinct queries and replace the current result set."""
    raw_queries = state.get("search_queries") or [
        state["standalone_query"]
    ]

    queries = list(
        dict.fromkeys(
            _normalize_query(q)
            for q in raw_queries
            if _normalize_query(q)
        )
    )[:2]

    results: list[dict] = []
    seen_urls: set[str] = set()
    failures: list[str] = []

    for query in queries:
        try:
            query_results = web_search.search(query) or []
        except Exception as exc:
            log.warning("Web search failed for %r: %s", query, exc)
            failures.append(str(exc)[:200])
            continue

        for result in query_results:
            if not isinstance(result, dict):
                continue

            url = result.get("url")
            content = result.get("content")

            if not url or not content:
                continue

            normalized_url = url.strip().rstrip("/").casefold()

            if normalized_url in seen_urls:
                continue

            seen_urls.add(normalized_url)
            results.append(result)

    results = results[: settings.web_results + 2]
    attempt = state.get("attempts", 0) + 1

    update = {
        # Replace old results even when the new search returns nothing.
        "web_results": results,
        "attempts": attempt,
        "tried_sources": _with(
            state.get("tried_sources"),
            "web_search",
        ),
        # Do not retain an old answer as the output of a failed search.
        "answer": "",
        "tool": None,
        "sources": [],
        "evaluation": None,
        "trace": [
            _step(
                "web_search",
                f"Attempt {attempt}: found {len(results)} usable web result(s).",
                queries=queries,
                urls=[r["url"] for r in results],
                failures=failures,
            )
        ],
    }

    if not results:
        update["rewrite_reason"] = "no_web_results"

    return update


def generate_web_node(state: AgentState) -> dict:
    """Generate only when the current search has returned results."""
    results = state.get("web_results", [])

    if not results:
        return {
            "answer": "",
            "tool": None,
            "sources": [],
            "evaluation": None,
            "rewrite_reason": "no_web_results",
            "trace": [
                _step(
                    "generate",
                    "Skipped web-answer generation because no results were available.",
                )
            ],
        }

    text = web_search.generate(
        state["standalone_query"],
        results,
        state.get("history", []),
    )

    if not text:
        return {
            "answer": "",
            "tool": None,
            "sources": [],
            "evaluation": None,
            "rewrite_reason": "insufficient_answer",
            "trace": [
                _step(
                    "generate",
                    "Web sources did not produce an answer.",
                )
            ],
        }

    return {
        "answer": text,
        "tool": "web_search",
        "sources": web_search.to_sources(results),
        "notices": _notices_after_writing(state),
        "trace": [
            _step(
                "generate",
                f"Generated a candidate answer from {len(results)} web source(s).",
            )
        ],
    }


# ============================================================
# 7. Evaluation
# ============================================================

def evaluate_node(state: AgentState) -> dict:
    tool = state.get("tool")

    if tool == "rag":
        context = rag.format_context(
            state.get("documents", [])
        )
    elif tool == "web_search":
        context = "\n\n".join(
            f"[{i}] {r.get('content', '')[:1200]}"
            for i, r in enumerate(
                state.get("web_results", []),
                start=1,
            )
        )
    else:
        context = None

    try:
        ev = evaluate_answer(
            state["standalone_query"],
            state["answer"],
            context,
        )
    except Exception as exc:
        # Fail closed for source-grounded answers: if evaluation itself fails,
        # do not claim that the answer has been verified.
        log.exception("Answer evaluation failed: %s", exc)

        ev = {
            "sufficient": False,
            "grounded": False if tool in ("rag", "web_search") else True,
            "score": 0,
            "missing": "The answer could not be evaluated reliably.",
        }

    candidate = {
        "answer": state.get("answer", ""),
        "tool": tool,
        "sources": state.get("sources", []),
        "evaluation": ev,
        "notices": list(state.get("notices", [])),
    }

    best = state.get("best")

    # Never store an answer known to be ungrounded as the best source-based
    # fallback. A direct answer has no retrieved context, so its evaluator
    # result must be interpreted according to the evaluator's own semantics.
    if ev.get("grounded", True):
        old_score = (
            (best or {}).get("evaluation", {}).get("score", 0)
        )
        new_score = ev.get("score", 0)

        if best is None or new_score > old_score:
            best = candidate

    ok = bool(
        ev.get("sufficient")
        and ev.get("grounded", True)
    )

    detail = (
        f"Answer {'sufficient' if ok else 'NOT sufficient'} "
        f"(score {ev.get('score')}/5, "
        f"grounded={ev.get('grounded')})."
    )

    if not ok and ev.get("missing"):
        detail += f" Missing: {ev['missing']}"

    update = {
        "evaluation": ev,
        "best": best,
        "trace": [
            _step(
                "evaluate",
                detail,
                **ev,
            )
        ],
    }

    if not ok:
        update["rewrite_reason"] = "insufficient_answer"

    return update


# ============================================================
# 8. Fallback
# ============================================================

def fallback_node(state: AgentState) -> dict:
    current = state.get("intent")
    nxt = next_source(state)

    if nxt is None:
        return {
            "intent": "exhausted",
            "trace": [
                _step(
                    "fallback",
                    "All eligible sources have been tried.",
                )
            ],
        }

    notice = FALLBACK_NOTICES.get((current, nxt))

    if current == "rag" and state.get("docs_search_failed"):
        notice = (
            "Your documents could not be searched because the search service "
            "was unavailable, so this answer comes from "
            f"{SOURCE_NAMES[nxt]}."
        )

    notices = list(state.get("notices", []))

    if notice and notice not in notices:
        notices.append(notice)

    return {
        "intent": nxt,
        "attempts": 0,
        "search_queries": [state["standalone_query"]],
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
            _step(
                "fallback",
                f"Nothing usable from {SOURCE_NAMES.get(current, current)}; "
                f"trying {SOURCE_NAMES[nxt]}.",
                source=nxt,
            )
        ],
    }


# ============================================================
# 9. Terminal nodes
# ============================================================

def no_results_node(state: AgentState) -> dict:
    tried = [
        SOURCE_NAMES[source]
        for source in state.get("tried_sources", [])
        if source in SOURCE_NAMES
    ]

    if len(tried) > 1:
        where = ", ".join(tried[:-1]) + f" or {tried[-1]}"
    elif tried:
        where = tried[0]
    else:
        where = "any source"

    text = (
        f"I couldn't find a reliable answer to this in {where}. "
        "Try rephrasing with more specific details, such as names, dates, "
        "or places, or upload a document that covers this topic."
    )

    return {
        "answer": text,
        "tool": "no_results",
        "sources": [],
        "evaluation": None,
        "notices": [],
        "trace": [
            _step(
                "no_results",
                "No source produced a usable answer.",
            )
        ],
    }


def finalize_node(state: AgentState) -> dict:
    """Return a sufficient answer or the best grounded fallback candidate."""
    if (
        state.get("tool") == "direct"
        and state.get("evaluation") is None
        and state.get("answer")
    ):
        return {
            "trace": [
                _step(
                    "finalize",
                    "Returning the direct answer that passed the confidence gate.",
                )
            ]
        }

    ev = state.get("evaluation")

    if (
        ev
        and ev.get("sufficient")
        and ev.get("grounded", True)
    ):
        return {
            "trace": [
                _step(
                    "finalize",
                    "Returning the sufficient answer.",
                )
            ]
        }

    chosen = state.get("best") or {
        "answer": state.get("answer", ""),
        "tool": state.get("tool"),
        "sources": state.get("sources", []),
        "evaluation": ev,
        "notices": state.get("notices", []),
    }

    answer = chosen.get("answer", "")
    chosen_tool = chosen.get("tool")

    note = {
        "rag": (
            "Note: your documents may not fully cover this question. "
            "The answer is limited to what they contain."
        ),
        "web_search": (
            "Note: the web results may not fully answer this question. "
            "Verify important claims against a primary source."
        ),
        "direct": (
            "Note: this answer comes from the model's own knowledge and "
            "could not be fully verified against a source."
        ),
    }.get(chosen_tool)

    if answer and note:
        answer = f"{answer}\n\n{note}"

    return {
        "answer": answer,
        "tool": chosen_tool,
        "sources": chosen.get("sources", []),
        "evaluation": chosen.get("evaluation"),
        "notices": chosen.get("notices", []),
        "trace": [
            _step(
                "finalize",
                "No source fully answered; returning the best available candidate.",
            )
        ],
    }