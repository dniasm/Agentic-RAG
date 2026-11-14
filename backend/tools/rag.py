"""Grounded answer generation over retrieved document chunks."""
from langchain_core.messages import HumanMessage, SystemMessage

from backend.config.llm import invoke_answer
from backend.tools import FORMAT_RULES, clean_answer, history_to_messages

NO_ANSWER = "NO_ANSWER_IN_DOCUMENTS"

SYSTEM = """You answer from passages of the user's own documents, given with each question.

- Use only what the passages say; never fill gaps with outside knowledge. Earlier messages are context for what the
  user means, not a source of facts.
- Cite at the end of every sentence or list item that uses a passage: [1], or [2][3] when several support it. A
  sentence that introduces a list or sums it up needs its citation too.
- Copy numbers, dates, amounts and names exactly as the passages write them.
- If the passages answer only part of the question, answer that part and say in one sentence which part the
  documents don't cover. If passages disagree, give both, each with its citation.
- If nothing in the passages answers the question, reply with exactly {NO_ANSWER} and nothing else.
- Refer to "the documents" (or a document by name), never to "excerpts" or "passages".

{FORMAT_RULES}""".replace("{NO_ANSWER}", NO_ANSWER).replace("{FORMAT_RULES}", FORMAT_RULES)

# The user turn's heading. Kept here so the prompt benchmark can swap it together with SYSTEM.
CONTEXT_INTRO = "Passages from the user's documents:"


def format_block(i: int, d: dict) -> str:
    """One numbered passage, as the answer writer (and the answer checker) see it."""
    page = f", page {d['page']}" if d.get("page") else ""
    return f"[{i}] (source: {d['source']}{page})\n{d['content']}"


def format_context(docs: list[dict]) -> str:
    return "\n\n".join(format_block(i, d) for i, d in enumerate(docs, start=1))


def generate(query: str, docs: list[dict], history: list[dict]) -> str | None:
    """Return the grounded answer, or None when the excerpts don't answer the question."""
    prompt = f"{CONTEXT_INTRO}\n\n{format_context(docs)}\n\n---\nQuestion: {query}"
    messages = [SystemMessage(SYSTEM), *history_to_messages(history), HumanMessage(prompt)]
    text = clean_answer(invoke_answer(messages))
    # The sentinel means "nothing at all" only when it IS the reply. The model sometimes writes a partial
    # answer and then adds the sentinel for the uncovered part; discarding the whole answer then sent the
    # user "not found" after they had watched the answer being written.
    # A correct answer can be very short ("BM stands for best matching [1]." is 37 characters), so length only
    # matters when the sentinel is there: then a few leftover characters are punctuation, not an answer.
    without = text.replace(NO_ANSWER, "").strip(" \n.:-—")
    if not without or (NO_ANSWER in text and len(without) < 40):
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
