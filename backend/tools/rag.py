"""Grounded answer generation over retrieved document chunks."""
from langchain_core.messages import HumanMessage, SystemMessage

from backend.config.llm import invoke_answer
from backend.tools import FORMAT_RULES, clean_answer, history_to_messages

NO_ANSWER = "NO_ANSWER_IN_DOCUMENTS"

SYSTEM = """You answer questions using ONLY the provided document excerpts from the user's knowledge base.

Rules:
- Ground every claim in the excerpts. Cite them inline with their bracket number, e.g. [1] or [2][3].
- If the excerpts only partially answer the question, answer what you can and state clearly
  what is not covered by the documents.
- If the excerpts contain nothing that answers the question, reply with exactly {NO_ANSWER} and nothing else.
- Never use outside knowledge to fill gaps.
- Be concise and well structured.

{FORMAT_RULES}""".replace("{NO_ANSWER}", NO_ANSWER).replace("{FORMAT_RULES}", FORMAT_RULES)


def format_block(i: int, d: dict) -> str:
    """One numbered passage, as the answer writer (and the answer checker) see it."""
    page = f", page {d['page']}" if d.get("page") else ""
    return f"[{i}] (source: {d['source']}{page})\n{d['content']}"


def format_context(docs: list[dict]) -> str:
    return "\n\n".join(format_block(i, d) for i, d in enumerate(docs, start=1))


def generate(query: str, docs: list[dict], history: list[dict]) -> str | None:
    """Return the grounded answer, or None when the excerpts don't answer the question."""
    prompt = f"Document excerpts:\n\n{format_context(docs)}\n\n---\nQuestion: {query}"
    messages = [SystemMessage(SYSTEM), *history_to_messages(history), HumanMessage(prompt)]
    text = clean_answer(invoke_answer(messages))
    # The sentinel means "nothing at all" only when it IS the reply. The model sometimes writes a partial
    # answer and then adds the sentinel for the uncovered part; discarding the whole answer then sent the
    # user "not found" after they had watched the answer being written.
    without = text.replace(NO_ANSWER, "").strip(" \n.:-—")
    if len(without) < 40:
        return None
    if NO_ANSWER in text:
        text = text.replace(NO_ANSWER, "The documents don't cover the rest of this question.").strip()
    return text


def to_sources(docs: list[dict]) -> list[dict]:
    return [
        {
            "type": "document",
            "title": d["source"],
            "document": d["source"],
            "page": d.get("page"),
            "score": d.get("score"),
            "snippet": d["content"][:280],
        }
        for d in docs
    ]
