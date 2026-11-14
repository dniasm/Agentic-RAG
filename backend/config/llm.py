"""LLM factories (Groq)."""
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from functools import lru_cache

import groq
from langchain_core.runnables import RunnableLambda
from langchain_groq import ChatGroq

from backend.config.settings import settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- Groq keys
# Groq rate limits are per organisation and per model. With extra keys from other organisations
# (GROQ_API_KEY_2 … GROQ_API_KEY_4), a model whose per-minute or daily limit is used up on one key is called with
# the next one. The model-level backups further down (large -> small model, Qwen -> small model) only start when
# every key is out for that model.
def groq_keys() -> list[str]:
    keys = (settings.groq_api_key, settings.groq_api_key_2, settings.groq_api_key_3, settings.groq_api_key_4)
    return [k for k in keys if k]


_RETRY_IN = re.compile(r"try again in ((?:\d+(?:\.\d+)?(?:h|ms|m|s))+)")
_RETRY_PART = re.compile(r"(\d+(?:\.\d+)?)(h|ms|m|s)")
_UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}


def cooldown_seconds(error: Exception) -> float:
    """How long Groq says to wait ("Please try again in 7m12.5s"); 60 s if it doesn't say."""
    match = _RETRY_IN.search(str(error))
    if not match:
        return 60.0
    return max(1.0, sum(float(n) * _UNIT_SECONDS[u] for n, u in _RETRY_PART.findall(match.group(1))))


class KeyPool:
    """Remembers which key is rate-limited for which model until Groq's reset time, so calls go straight to a
    key that still has capacity instead of failing (and retrying) on an exhausted one first."""

    def __init__(self):
        self._lock = threading.Lock()
        self._until: dict[tuple[str, int], float] = {}
        self._last_error: dict[str, Exception] = {}

    def available(self, model: str, count: int) -> list[int]:
        now = time.monotonic()
        with self._lock:
            return [i for i in range(count) if self._until.get((model, i), 0) <= now]

    def mark(self, model: str, index: int, error: Exception) -> None:
        wait = cooldown_seconds(error)
        with self._lock:
            self._until[(model, index)] = time.monotonic() + wait
            self._last_error[model] = error
        log.warning("Groq key %d is rate-limited for %s for %.0f s: %s", index + 1, model, wait, str(error)[:160])

    def last_error(self, model: str) -> Exception:
        with self._lock:
            return self._last_error.get(model) or RuntimeError(f"No Groq key available for {model}")

    def reset(self) -> None:
        with self._lock:
            self._until.clear()
            self._last_error.clear()


key_pool = KeyPool()


def call_with_keys(model: str, factory: Callable[[int], object], call: Callable[[object], object]):
    """call(factory(key_index)) on the first key with capacity for `model`; on a rate limit, the next one.
    Re-raises the last groq.RateLimitError when every key is limited, so the callers' backups take over."""
    count = max(1, len(groq_keys()))
    for index in key_pool.available(model, count):
        try:
            return call(factory(index))
        except groq.RateLimitError as e:
            key_pool.mark(model, index, e)
    raise key_pool.last_error(model)


def _key(index: int) -> str:
    keys = groq_keys()
    return keys[index] if index < len(keys) else settings.groq_api_key


@lru_cache
def get_llm(temperature: float | None = None, key: int = 0) -> ChatGroq:
    """Stronger model used to write user-facing answers (LLM_TEMPERATURE unless given)."""
    return ChatGroq(
        model=settings.llm_model,
        temperature=settings.llm_temperature if temperature is None else temperature,
        api_key=_key(key),
        max_retries=2,
    )

@lru_cache
def get_fast_llm(key: int = 0) -> ChatGroq:
    """Cheap, deterministic model for routing / grading / rewriting / evaluation."""
    return ChatGroq(
        model=settings.fast_llm_model,
        temperature=0,
        api_key=_key(key),
        max_retries=2,
        reasoning_effort=settings.fast_llm_reasoning_effort or None,
    )


@lru_cache
def get_memory_llm(key: int = 0) -> ChatGroq:
    """Qwen: extracts persistent memories. Separate rate limit from gpt-oss, and it answers without
    hidden reasoning, so a structured extraction costs a few dozen output tokens instead of hundreds."""
    return ChatGroq(
        model=settings.memory_llm_model,
        temperature=0,
        api_key=_key(key),
        max_retries=2,
    )


@lru_cache
def get_judge_llm(key: int = 0) -> ChatGroq:
    """Large model for reviews and answer evaluation: deterministic, with lighter hidden reasoning.
    Same verdicts in testing, ~60% fewer output tokens, which matters under the free tier's TPM limit."""
    return ChatGroq(
        model=settings.llm_model,
        temperature=0,
        api_key=_key(key),
        max_retries=2,
        reasoning_effort=settings.judge_reasoning_effort or None,
    )


@lru_cache
def get_backup_writer(temperature: float | None = None, key: int = 0) -> ChatGroq:
    """The small model, set up for writing answers, used when the large model's quota is exhausted.
    Each Groq model has its own rate limits, so the small model usually still has capacity."""
    return ChatGroq(
        model=settings.fast_llm_model,
        temperature=settings.llm_temperature if temperature is None else temperature,
        api_key=_key(key),
        max_retries=2,
    )


def invoke_memory(prompt, config=None):
    """The memory model (Qwen), on the first Groq key with capacity."""
    return call_with_keys(settings.memory_llm_model, get_memory_llm, lambda llm: llm.invoke(prompt, config))


# ---------------------------------------------------------------- answer writing with quota fallback
# Set when the backup model wrote an answer in the current graph step, so the node can tell the user.
_backup_used: ContextVar[bool] = ContextVar("backup_used", default=False)


def invoke_answer(messages, temperature: float | None = None) -> str:
    """Write a user-facing answer with the large model (each Groq key in turn), falling back to the small model
    when the large model's rate limit (per-minute or daily) is used up on every key."""
    try:
        return call_with_keys(settings.llm_model, lambda k: get_llm(temperature, k),
                              lambda llm: llm.invoke(messages).content)
    except groq.RateLimitError as e:
        log.warning("Large model rate-limited, writing this answer with the small model: %s", str(e)[:200])
        _backup_used.set(True)
        return call_with_keys(settings.fast_llm_model, lambda k: get_backup_writer(temperature, k),
                              lambda llm: llm.invoke(messages).content)


def consume_backup_flag() -> bool:
    """True (once) if the last answer in this step was written by the backup model."""
    used = _backup_used.get()
    _backup_used.set(False)
    return used


# ---------------------------------------------------------------- structured output
# Groq guarantees schema-valid output for these models with strict json_schema (constrained decoding).
_STRICT_MODELS = {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}


_THINK = re.compile(r"<think>.*?</think>", re.S)


def _plain_json(llm: ChatGroq, model_cls):
    """Fallback: ask for the JSON in plain text and validate it ourselves.

    The full schema is sent, including nested definitions ($defs): with only the top-level properties, a list of
    objects (MemoryExtraction.facts) had no item format, and Qwen copied the schema back instead of filling it in."""
    schema = json.dumps(model_cls.model_json_schema(), ensure_ascii=False)

    def run(prompt):
        log.info("Structured output for %s fell back to plain JSON on %s", model_cls.__name__, llm.model_name)
        text = prompt if isinstance(prompt, str) else str(prompt)
        reply = llm.invoke(
            f"{text}\n\nReply with ONLY a JSON object (no prose, no code fences) holding your answer. It must "
            f"conform to this JSON Schema; return the data, not the schema:\n{schema}"
        ).content
        match = re.search(r"\{.*\}", _THINK.sub("", reply), re.S)
        if not match:
            raise ValueError("The model's reply contained no JSON object")
        return model_cls.model_validate_json(match.group(0))

    return RunnableLambda(run)


_PARAMETER = re.compile(r"<parameter=(\w+)>\s*(.*?)\s*</parameter>", re.S)


def _recover_tool_call(primary, model_cls):
    """Tool calling, keeping answers the model got right but filed under a misspelled tool name.

    Measured 2026-11-03: Qwen called "MemoryExclusion", "MemoryExression" or "_unique_placeholder" instead of
    "MemoryExtraction" in 3 of 8 identical calls. Groq rejects those (tool_use_failed) but returns the generation,
    which usually still holds the right arguments; reading them costs no extra call. A call with no arguments
    can't be recovered and goes on to the next fallback."""

    def run(prompt, config):
        try:
            return primary.invoke(prompt, config)
        except groq.BadRequestError as e:
            body = getattr(e, "body", None) or {}
            generation = (body.get("error", body) if isinstance(body, dict) else {}).get("failed_generation") or ""
            arguments = {}
            for name, raw in _PARAMETER.findall(generation):
                try:
                    arguments[name] = json.loads(raw)
                except ValueError:
                    arguments[name] = raw
            if not arguments:
                raise
            log.info("Recovered %s from a misnamed tool call on %s", model_cls.__name__, generation[:60])
            return model_cls.model_validate(arguments)

    return RunnableLambda(run)


def _structured_for(llm: ChatGroq, model_cls):
    """Schema-constrained output first, then plain JSON validated with pydantic.

    gpt-oss: strict json_schema. Without `strict`, Groq only checks the output afterwards, and gpt-oss
    intermittently writes its reasoning where the JSON belongs (`json_validate_failed` /
    `output_parse_failed`); every failure re-sent the whole prompt. Strict mode constrains decoding instead.
    Other models (Qwen): tool calling. In testing Qwen's json_schema mode returned empty results.
    A rate limit is not a format problem: it skips the plain-JSON fallback (which would hit the same limit)
    and goes to the next key.
    """
    if llm.model_name in _STRICT_MODELS:
        primary = llm.with_structured_output(model_cls, method="json_schema", strict=True)
    else:
        primary = _recover_tool_call(llm.with_structured_output(model_cls, method="function_calling"), model_cls)
    fallback = _plain_json(llm, model_cls).with_retry(stop_after_attempt=2)

    def run(prompt, config):
        try:
            return primary.invoke(prompt, config)
        except groq.RateLimitError:
            raise
        except Exception:
            return fallback.invoke(prompt, config)

    return RunnableLambda(run)


def _structured_on_keys(factory: Callable[[int], ChatGroq], model_cls):
    """_structured_for on the first Groq key with capacity for the model, then the next key on a rate limit."""
    model = factory(0).model_name

    def run(prompt, config):
        return call_with_keys(model, lambda k: _structured_for(factory(k), model_cls),
                              lambda chain: chain.invoke(prompt, config))

    return RunnableLambda(run)


def structured(model_cls, fast: bool = True, backup: bool = False, memory: bool = False):
    """Schema-constrained output, returned as an instance of `model_cls`. Each model is tried on every Groq key
    (call_with_keys) before the model-level backup below.

    fast=True: the small model (routing and other short decisions).
    fast=False: the large model (reviews, evaluation, the knowledge gate).
    memory=True: the memory model (Qwen), falling back to the small model if Qwen is unavailable.

    backup=True (large-model calls only): if the large model is unavailable (e.g. its quota is used up),
    let the small model do the job instead. Used for answer evaluation, not for reviews: a model
    reviewing its own decision adds nothing, so reviews are simply skipped when the large model is out.
    """
    if memory:
        return _structured_on_keys(get_memory_llm, model_cls).with_fallbacks(
            [_structured_on_keys(get_fast_llm, model_cls)]
        )
    if fast:
        return _structured_on_keys(get_fast_llm, model_cls)
    chain = _structured_on_keys(get_judge_llm, model_cls)
    return chain.with_fallbacks([_structured_on_keys(get_fast_llm, model_cls)]) if backup else chain
