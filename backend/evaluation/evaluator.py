"""LLM-as-judge components used inside the agent loop: document grading and answer evaluation."""
import logging
from typing import Literal

from pydantic import BaseModel, Field

from backend.config.llm import structured
from backend.config.settings import settings
from backend.evaluation.reviewer import verified_decision

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- document grading
class DocGrades(BaseModel):
    relevant_ids: list[int] = Field(
        description="Numbers of the excerpts that contain information useful for answering the question."
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="How sure you are. Use high only when relevance is clear-cut for every excerpt."
    )


GRADE_PROMPT = """You are grading retrieved excerpts for relevance to a user's question.
An excerpt is relevant if it contains facts, definitions, or context that would help answer the
question (even partially). Keyword overlap alone is not enough.
If the question is about a document as a whole (summarize it, its main point, overview, what it is
about, key takeaways), excerpts that carry the document's main content ARE relevant.

Question: {query}

Excerpts:
{excerpts}

Return the numbers of the relevant excerpts (empty list if none)."""


def grade_documents(query: str, docs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (relevant_docs, review_trace_steps). The small model grades; the large model reviews."""
    if not docs:
        return [], []
    excerpts = "\n\n".join(f"[{i}] {d['content'][:900]}" for i, d in enumerate(docs, start=1))
    # The reviewer gets short previews instead of full passages: enough to judge, far fewer tokens.
    previews = "\n".join(f"[{i}] (similarity {d.get('score', 0):.2f}) {' '.join(d['content'].split())[:250]}…"
                         for i, d in enumerate(docs, start=1))
    review_context = (f"Task: pick which excerpts help answer the question. Excerpts about a document as a whole "
                      f"count as relevant for summary/overview questions. Keyword overlap alone is not enough.\n\n"
                      f"Question: {query}\n\nExcerpt previews:\n{previews}")
    top_score = max((d.get("score", 0) for d in docs), default=0)

    def needs_review(g: DocGrades) -> bool:
        # Review when unsure, or when everything was rejected despite decent matches:
        # a false "nothing relevant" would wrongly send the user to the web.
        return g.confidence != "high" or (not g.relevant_ids and top_score >= settings.rag_skip_score)

    try:
        result, steps = verified_decision(GRADE_PROMPT.format(query=query, excerpts=excerpts), DocGrades,
                                          "passage selection", review_context=review_context,
                                          needs_review=needs_review)
        keep = {i for i in result.relevant_ids if 1 <= i <= len(docs)}
        return [d for i, d in enumerate(docs, start=1) if i in keep], steps
    except Exception as e:
        # Fail open: better to let the generator see the context than to drop everything.
        log.warning("Document grading failed, keeping all docs: %s", e)
        return docs, []


# ---------------------------------------------------------------- answer evaluation
class AnswerEval(BaseModel):
    sufficient: bool = Field(description="True if the answer fully and directly addresses the question.")
    grounded: bool = Field(description="True if every claim is supported by the provided context (True if no context).")
    score: int = Field(ge=1, le=5, description="Overall quality 1 (useless) to 5 (excellent).")
    missing: str = Field(description="What information is missing or wrong; empty string if nothing.")


EVAL_PROMPT = """You are a strict evaluator of an AI assistant's answer.

Question: {query}

{context_block}Answer:
{answer}

Judge:
- sufficient: does the answer actually resolve the question? An answer that says the information
  is not available, is vague, or only addresses part of the question is NOT sufficient.
- grounded: is every factual claim supported by the context above? (If no context is given, True.)
- score: 1-5 overall quality.
- missing: briefly describe what is missing or unsupported, to guide a better search."""


def evaluate_answer(query: str, answer: str, context: str | None = None) -> dict:
    context_block = f"Context the answer must be based on:\n{context[:6000]}\n\n" if context else ""
    try:
        # The large model judges: a weaker model should not grade a stronger model's answer.
        # backup=True: if the large model's quota is exhausted, the small model evaluates instead.
        r = structured(AnswerEval, fast=False, backup=True).invoke(
            EVAL_PROMPT.format(query=query, answer=answer, context_block=context_block)
        )
        return r.model_dump()
    except Exception as e:
        log.warning("Answer evaluation failed, accepting answer: %s", e)
        return {"sufficient": True, "grounded": True, "score": 3, "missing": "", "error": str(e)}
