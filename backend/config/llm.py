"""LLM factories (Groq)."""
import json
import logging
import re
from contextvars import ContextVar
from functools import lru_cache

import groq
from langchain_core.runnables import RunnableLambda
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from backend.config.settings import settings

log = logging.getLogger(__name__)


@lru_cache
def get_llm() -> ChatGroq:
    """Stronger model used to write user-facing answers."""
    return ChatGroq(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        api_key=settings.groq_api_key,
        max_retries=2,
    )

@lru_cache
def get_nemotron_llm() -> ChatOpenAI:
    """Prospect model"""
    return ChatOpenAI(
        model = settings.open_router_model,
        base_url = settings.open_router_base_url,
        api_key = settings.open_router_api_key,
        temperature = settings.llm_temperature,
        max_retries = 2,
    )

@lru_cache
def get_fast_llm() -> ChatGroq:
    """Cheap, deterministic model for routing / grading / rewriting / evaluation."""
    return ChatGroq(
        model=settings.fast_llm_model,
        temperature=0,
        api_key=settings.groq_api_key,
        max_retries=2,
        reasoning_effort=settings.fast_llm_reasoning_effort or None,
    )


@lru_cache
def get_memory_llm() -> ChatGroq:
    """Qwen: extracts persistent memories. Separate rate limit from gpt-oss, and it answers without
    hidden reasoning, so a structured extraction costs a few dozen output tokens instead of hundreds."""
    return ChatGroq(
        model=settings.memory_llm_model,
        temperature=0,
        api_key=settings.groq_api_key,
        max_retries=2,
    )


@lru_cache
def get_judge_llm() -> ChatGroq:
    """Large model for reviews and answer evaluation: deterministic, with lighter hidden reasoning.
    Same verdicts in testing, ~60% fewer output tokens, which matters under the free tier's TPM limit."""
    return ChatGroq(
        model=settings.llm_model,
        temperature=0,
        api_key=settings.groq_api_key,
        max_retries=2,
        reasoning_effort=settings.judge_reasoning_effort or None,
    )


@lru_cache
def get_backup_writer() -> ChatGroq:
    """The small model, set up for writing answers, used when the large model's quota is exhausted.
    Each Groq model has its own rate limits, so the small model usually still has capacity."""
    return ChatGroq(
        model=settings.fast_llm_model,
        temperature=settings.llm_temperature,
        api_key=settings.groq_api_key,
        max_retries=2,
    )


# ---------------------------------------------------------------- answer writing with quota fallback
# Set when the backup model wrote an answer in the current graph step, so the node can tell the user.
_backup_used: ContextVar[bool] = ContextVar("backup_used", default=False)


def invoke_answer(messages) -> str:
    """Write a user-facing answer with the large model, falling back to the small model if the
    large model's rate limit (per-minute or daily) is exhausted."""
    try:
        return get_llm().invoke(messages).content
    except groq.RateLimitError as e:
        log.warning("Large model rate-limited, writing this answer with the small model: %s", str(e)[:200])
        _backup_used.set(True)
        return get_backup_writer().invoke(messages).content


def consume_backup_flag() -> bool:
    """True (once) if the last answer in this step was written by the backup model."""
    used = _backup_used.get()
    _backup_used.set(False)
    return used


# ---------------------------------------------------------------- structured output
# Groq guarantees schema-valid output for these models with strict json_schema (constrained decoding).
_STRICT_MODELS = {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}


def _plain_json(llm: ChatGroq, model_cls):
    """Fallback: ask for the JSON in plain text and validate it ourselves."""
    fields = json.dumps(model_cls.model_json_schema().get("properties", {}), ensure_ascii=False)

    def run(prompt):
        log.info("Structured output for %s fell back to plain JSON on %s", model_cls.__name__, llm.model_name)
        text = prompt if isinstance(prompt, str) else str(prompt)
        reply = llm.invoke(
            f"{text}\n\nReply with ONLY a JSON object (no prose, no code fences) with exactly these fields:\n{fields}"
        ).content
        match = re.search(r"\{.*\}", reply, re.S)
        if not match:
            raise ValueError("The model's reply contained no JSON object")
        return model_cls.model_validate_json(match.group(0))

    return RunnableLambda(run)


def _structured_for(llm: ChatGroq, model_cls):
    """Schema-constrained output first, then plain JSON validated with pydantic.

    gpt-oss: strict json_schema. Without `strict`, Groq only checks the output afterwards, and gpt-oss
    intermittently writes its reasoning where the JSON belongs (`json_validate_failed` /
    `output_parse_failed`); every failure re-sent the whole prompt. Strict mode constrains decoding instead.
    Other models (Qwen): tool calling. In testing Qwen's json_schema mode returned empty results.
    """
    if llm.model_name in _STRICT_MODELS:
        primary = llm.with_structured_output(model_cls, method="json_schema", strict=True)
    else:
        primary = llm.with_structured_output(model_cls, method="function_calling")
    return primary.with_fallbacks([_plain_json(llm, model_cls).with_retry(stop_after_attempt=2)])


def structured(model_cls, fast: bool = True, backup: bool = False, memory: bool = False):
    """Schema-constrained output, returned as an instance of `model_cls`.

    fast=True: the small model (routing and other short decisions).
    fast=False: the large model (reviews, evaluation, the knowledge gate).
    memory=True: the memory model (Qwen), falling back to the small model if Qwen is unavailable.

    backup=True (large-model calls only): if the large model is unavailable (e.g. its quota is used up),
    let the small model do the job instead. Used for answer evaluation, not for reviews: a model
    reviewing its own decision adds nothing, so reviews are simply skipped when the large model is out.
    """
    if memory:
        return _structured_for(get_memory_llm(), model_cls).with_fallbacks(
            [_structured_for(get_fast_llm(), model_cls)]
        )
    if fast:
        return _structured_for(get_fast_llm(), model_cls)
    chain = _structured_for(get_judge_llm(), model_cls)
    return chain.with_fallbacks([_structured_for(get_fast_llm(), model_cls)]) if backup else chain
