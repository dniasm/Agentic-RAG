"""Decision verification loop: the small model proposes, the large model reviews.

    small model ── proposal ──► large model reviews
         ▲                          │
         └──── feedback (rejected) ─┘      … up to MAX_REVIEW_ROUNDS, stops when approved

The small model stays the one that *decides* (cheap, fast); the large model acts as a senior
reviewer that either approves the decision or explains what is wrong and what would be better.

To stay within the free tier's tokens-per-minute limit:
- the reviewer gets a compact `review_context` (just what's needed to judge), not the full prompt;
- with REVIEW_ONLY_UNCERTAIN, a decision is reviewed only when `needs_review(decision)` says so
  (by default: when the small model's own confidence isn't "high").
"""
import json
import logging
import uuid
from typing import Callable

from pydantic import BaseModel, Field

from backend.config.llm import structured
from backend.config.settings import settings

log = logging.getLogger(__name__)


class Review(BaseModel):
    approved: bool = Field(description="True only if the decision is correct and no clearly better decision exists.")
    feedback: str = Field(
        description="If not approved: concretely what is wrong and what the better decision is. Empty if approved."
    )


REVIEW_PROMPT = """You are a senior reviewer in an AI question-answering pipeline. A junior assistant
made the decision below. Check it critically.

=== WHAT THE JUNIOR HAD TO DECIDE ===
{context}
=== END ===

=== THE JUNIOR'S DECISION ===
{proposal}
=== END ===

Judge the SUBSTANCE of the decision: is it correct and the best choice given the rules and information
above? Do not judge formatting or JSON structure; the output format is enforced automatically.
Approve only if the decision is correct and you cannot see a clearly better one.
If you do not approve, explain concretely what is wrong and what the better decision would be.
Judge only against the information given; do not invent facts."""

REVISE_SUFFIX = """

=== FEEDBACK ON YOUR PREVIOUS ATTEMPT ===
Your previous decision was:
{previous}

A senior colleague suggested this improvement:
{feedback}

Now produce your revised decision for the ORIGINAL TASK above, using exactly the same fields as before
({fields}). Do not write a review; write the decision itself."""


def _dump(obj: BaseModel) -> str:
    return json.dumps(obj.model_dump(), ensure_ascii=False, indent=2)


def emit_live(step: dict) -> None:
    """Send a trace step to the UI immediately (when running under graph.stream with "custom" mode)."""
    try:
        from langgraph.config import get_stream_writer
        get_stream_writer()({"step": step})
    except Exception:
        pass  # not inside a streaming graph run: the step is still returned in the node's trace


def _review_step(detail: str, **data) -> dict:
    step = {"id": uuid.uuid4().hex[:12], "node": "review", "detail": detail, "data": data}
    emit_live(step)
    return step


def _default_needs_review(decision: BaseModel) -> bool:
    return getattr(decision, "confidence", "low") != "high"


def verified_decision(
    task_prompt: str,
    model_cls: type[BaseModel],
    label: str,
    review_context: str | None = None,
    needs_review: Callable[[BaseModel], bool] | None = None,
) -> tuple[BaseModel, list[dict]]:
    """Get a decision from the small model, then (if warranted) run the review loop with the large model.

    Returns (final_decision, trace_steps). Raises only if the very first small-model call fails,
    so callers keep their existing fallbacks; reviewer failures are logged and the current decision kept.
    """
    proposal = structured(model_cls).invoke(task_prompt)
    steps: list[dict] = []
    if not settings.verify_decisions:
        return proposal, steps

    check = needs_review or _default_needs_review
    if settings.review_only_uncertain and not check(proposal):
        steps.append(_review_step(f"Review skipped: the small model is confident in its {label}.",
                                  skipped=True))
        return proposal, steps

    context = review_context or task_prompt
    for round_no in range(1, settings.max_review_rounds + 1):
        try:
            review: Review = structured(Review, fast=False).invoke(
                REVIEW_PROMPT.format(context=context, proposal=_dump(proposal))
            )
        except Exception as e:
            log.warning("Review of %s failed, keeping decision: %s", label, e)
            break

        if review.approved:
            steps.append(_review_step(f"Large model approved the {label} (round {round_no}).",
                                      round=round_no, approved=True))
            break

        steps.append(_review_step(f"Large model requested a revision of the {label} (round {round_no}): "
                                  f"{review.feedback}", round=round_no, approved=False, feedback=review.feedback))
        try:
            revised = structured(model_cls).invoke(
                task_prompt + REVISE_SUFFIX.format(previous=_dump(proposal), feedback=review.feedback,
                                                   fields=", ".join(model_cls.model_fields))
            )
        except Exception as e:
            log.warning("Revision of %s failed, keeping decision: %s", label, e)
            break
        if revised == proposal:
            steps.append(_review_step(f"Small model kept its {label} unchanged; stopping review.", round=round_no))
            break
        proposal = revised
        steps.append(_review_step(f"Small model revised the {label}.", round=round_no,
                                  revised=proposal.model_dump()))

    return proposal, steps
