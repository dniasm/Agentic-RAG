import operator
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict, total=False):
    # --- inputs
    query: str                      # raw user message
    user_id: str
    username: str
    history: list[dict]             # [{"role": "user"|"assistant", "content": str}]
    memories: list[dict]
    doc_names: list[str]            # user's indexed documents (helps routing & rewriting)

    # --- routing
    intent: str                     # current source: greeting | rag | web_search | direct
    source_preference: str          # auto | documents | web | model_knowledge (only if the user asked)
    standalone_query: str           # query made self-contained using chat history
    query_is_clear: bool
    confidence_required: bool       # whether a direct answer must pass the factual knowledge gate
    route_reason: str
    tried_sources: list[str]        # sources already attempted, in order (rag / web_search / direct)
    notices: list[str]              # user-facing notes about which source answered and why

    # --- query transformation loop
    search_queries: list[str]       # queries used for the next retrieval / web search round
    rewritten_queries: Annotated[list[str], operator.add]
    rewrite_reason: str             # vague_query | no_relevant_docs | insufficient_answer | no_web_results
    attempts: int                   # search rounds executed for the CURRENT source

    # --- evidence
    documents: list[dict]           # graded, relevant chunks
    docs_off_topic: bool            # best document match was too weak: the documents don't cover the question
    docs_search_failed: bool        # document search itself failed (e.g. embedding service outage)
    match_zone: str                 # cross-encoder verdict: high (answer) | mid (retry once) | low (next source)
    web_results: list[dict]

    # --- output
    answer: str
    tool: str                       # greeting | rag | web_search | direct | no_results
    sources: list[dict]
    evaluation: dict[str, Any] | None
    best: dict[str, Any] | None     # best-scoring candidate across retries and sources
    trace: Annotated[list[dict], operator.add]
