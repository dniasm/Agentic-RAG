"""Persistent memory: key-value facts about the user, and short summaries of past exchanges.

Facts (SQLite, one row per value; see database.user_facts):
    The memory model (Qwen) reads the latest user message and reports what to REMEMBER or FORGET.
    It does not decide how storage changes: plan_changes() does, in code.
      - remember on a single-value key (preferred_name, location, favorite_*): the new value replaces the old
      - remember on any other key (hobbies, liked_foods, …): the value is added; existing values are kept
      - forget: removes that value, or the whole key for "*"
    A correction to a collection ("I don't play futsal any more") is a forget plus a remember.

Episodes (Voyage embeddings, backend/memory/store.py):
    Each answered exchange is summarised in one or two sentences, so recalled episodes cost the
    answer-writing models a few dozen tokens instead of ~800 characters each.
"""
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from backend.config.llm import get_memory_llm, structured
from backend.models import database as db

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- keys
# Keys that can only hold one value at a time: remembering a new value replaces the old one.
SINGLE_VALUE_KEYS = {
    "preferred_name", "full_name", "age", "birthday", "gender", "pronouns", "location", "hometown",
    "nationality", "occupation", "job_title", "employer", "education_level", "university", "major",
    "timezone", "response_style", "response_length", "assistant_name", "relationship_status",
}
SINGLE_VALUE_PREFIXES = ("favorite_", "preferred_", "current_")

# Common variants mapped to one key, so the same kind of fact never ends up under two keys.
KEY_ALIASES = {
    "name": "preferred_name", "user_name": "preferred_name", "nickname": "preferred_name",
    "city": "location", "current_city": "location", "current_location": "location", "lives_in": "location",
    "home_town": "hometown", "job": "occupation", "profession": "occupation", "work": "occupation",
    "school": "university", "college": "university", "field_of_study": "major",
    "hobby": "hobbies", "interest": "interests", "language": "languages",
    "assistant_nickname": "assistant_name",
}

MAX_KEY_CHARS = 60
MAX_VALUE_CHARS = 300


def normalize_key(key: str) -> str:
    """Lowercase snake_case, British "favourite" spelled one way, known variants mapped to one key."""
    k = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    k = k.replace("favourite", "favorite")
    return KEY_ALIASES.get(k, k)


def is_single_value(key: str) -> bool:
    return key in SINGLE_VALUE_KEYS or key.startswith(SINGLE_VALUE_PREFIXES)


# ---------------------------------------------------------------- extraction
# Field descriptions stay in full even though they repeat EXTRACT_PROMPT: with short ones (2026-11-03) the memory
# model saved nothing for "I'm Asmin and I live in Pokhara", ignored "forget all my hobbies", and filed
# "I'll call you Nova" as the user's own name.
class MemoryOp(BaseModel):
    key: str = Field(description="Short lowercase snake_case key, e.g. preferred_name, hobbies, liked_foods.")
    value: str = Field(description="One value. For forget: the value to remove, or * for the whole key.")
    action: Literal["remember", "forget"] = Field(
        description="remember: the user states this fact. forget: the user asks to forget it, "
                    "or says it is no longer true."
    )


class MemoryExtraction(BaseModel):
    facts: list[MemoryOp] = Field(default_factory=list,
                                  description="Memory operations from the latest user message; empty if none.")


MEMORY_CUES = (
    "i am ", "i'm ", "i’m ", "im ", "my name is ", "my age is ", "i live ", "i study ", "i'm studying ",
    "i’m studying ", "i work ", "i prefer ", "i like ", "i dislike ", "i hate ", "i love ", "i have ",
    "i've ", "i’ve ", "i want ", "i need ", "i use ", "call me ", "remember that ", "remember my ",
    "please remember ", "don't remember ", "do not remember ", "forget ", "from now on ", "going forward ",
    "your name is ", "call yourself ", "i'll call you ", "i’ll call you ", "i will call you ",
)

# Words that show the user is talking about themselves, about what to remember, or about how the
# assistant should answer from now on ("keep your answers short").
_PERSONAL = re.compile(
    r"\b(i|i'm|im|i've|i'd|i'll|me|my|mine|myself|we|our|"
    r"remember|forget|favou?rite|prefer|call me|your name|"
    r"your (?:answers?|replies|reply|responses?)|from now on|going forward)\b"
)


# Always worth a memory call, wherever they appear in the message.
_COMMAND = re.compile(r"\b(remember|forget|call me|call yourself|i'll call you|i will call you|your name is|"
                      r"from now on|going forward)\b")
# A clause starting with these is a question, a hypothetical or a request ("how do I…", "what's my…", "if I were…",
# "give me…", "check my files…"): its "I"/"my" doesn't state a fact. Measured 2026-11-03: these made up most memory
# calls that found nothing (the old filter fired on any "I", "me", "my" or "we").
_NOT_A_STATEMENT = frozenset("""
    how what what's whats why when where which who who's whom whose can could should would will shall may might
    do does did is are was were am has have had if any explain show give tell pull look check summarise summarize
    list write help find search compare describe translate define generate make create fix debug convert calculate
    please
""".split())


def _may_contain_memory(message: str) -> bool:
    """Cheap filter to avoid an LLM call for messages with nothing personal to save. Runs when the message gives a
    memory command, or has a clause stating something personal ("I play futsal", "milk tea is my favourite",
    "I live in Pokhara, any good cafes?"); questions and requests that merely mention "I" or "my" are skipped."""
    text = message.casefold().replace("’", "'")
    if _COMMAND.search(text):
        return True
    for clause in re.split(r"[.!?;,:\n]+", text):
        words = re.sub(r"[^\w']+", " ", clause).split()
        if not words or words[0] in _NOT_A_STATEMENT:
            continue
        joined = " " + " ".join(words) + " "
        if any(" " + cue.replace("’", "'") in joined for cue in MEMORY_CUES) or _PERSONAL.search(joined):
            return True
    return False


EXTRACT_PROMPT = """You manage the long-term memory of a personal AI assistant.
List the durable facts the LATEST USER MESSAGE states about the user. Return an empty list if there are none.

SAVED MEMORIES (key: values):
{existing}

PREVIOUS EXCHANGE (only to understand references; never a source of facts):
{history}

LATEST USER MESSAGE:
{message}

REMEMBER
- Identity and background (name, location, occupation, education, languages), preferences, likes and
  dislikes, favourites, hobbies, habits, goals, ongoing projects, and how the user wants answers.
- Every distinct fact in the message, including casual ones ("coffee over tea" is a preference).
- Explicit requests to remember something.

DO NOT REMEMBER
- Questions, one-off tasks, passing remarks with no future value.
- Anything the assistant said, or details of hypotheticals, role-play, fiction, or other people's facts.
- Guesses: liking something does not make it a favourite.

ACTIONS
- remember: a fact the user states. One value per operation. If they state a new value for a fact they
  can only have one of (name, location, job, a favourite), just remember the new value.
- forget: the user asks you to forget something, or says it is no longer true. Value = the saved value to
  remove (spelled as in SAVED MEMORIES), or "*" for the whole key.
  "I don't play futsal any more, I play cricket now" -> forget hobbies "futsal"; remember hobbies "cricket".

KEYS: short lowercase snake_case. Reuse a saved key for the same kind of fact. Plural keys for collections
(hobbies, liked_foods, liked_drinks, interests, languages); singular for single facts (preferred_name,
location, occupation, favorite_drink, response_style).

Example: "I also like coffee, but milk tea is my favourite drink"
-> remember liked_drinks "coffee"; remember favorite_drink "milk tea"."""


def _grouped(user_id: str) -> dict[str, list[str]]:
    return {m["memory_key"]: list(m["values"]) for m in db.list_memories(user_id)}


def _find(values: list[str], target: str) -> str | None:
    """The stored spelling of `target` among `values` (ignoring case), or None."""
    t = target.strip().casefold()
    return next((v for v in values if v.strip().casefold() == t), None)


def plan_changes(existing: dict[str, list[str]], ops: list[MemoryOp]) -> list[tuple[str, str, str | None]]:
    """Decide the storage changes for a list of operations, in code.

    existing: {key: [values]} as currently stored. Returns ("add" | "set" | "delete", key, value) tuples;
    "delete" with value None removes the whole key. Operations are applied in order to a working copy,
    so several operations on the same key in one message see each other's effects."""
    state = {k: list(v) for k, v in existing.items()}
    changes: list[tuple[str, str, str | None]] = []
    for op in ops:
        key = normalize_key(op.key)
        value = " ".join(op.value.split())
        if not key or len(key) > MAX_KEY_CHARS or len(value) > MAX_VALUE_CHARS:
            log.warning("Skipping invalid memory operation: %r", op)
            continue

        if op.action == "remember":
            if not value or value == "*":
                continue
            current = state.get(key, [])
            if _find(current, value):
                continue  # already known
            if is_single_value(key):
                changes.append(("set", key, value))
                state[key] = [value]
            else:
                changes.append(("add", key, value))
                state[key] = current + [value]

        else:  # forget
            if value in ("", "*"):
                if key in state:
                    changes.append(("delete", key, None))
                    del state[key]
                continue
            # The model may file the value under a different key than the one it was saved under.
            for k in [key] + [k for k in state if k != key]:
                stored = _find(state.get(k, []), value)
                if stored:
                    changes.append(("delete", k, stored))
                    state[k] = [v for v in state[k] if v is not stored]
                    if not state[k]:
                        del state[k]
                    break
    return changes


def apply_changes(user_id: str, changes: list[tuple[str, str, str | None]]) -> None:
    for action, key, value in changes:
        if action == "add":
            db.add_fact(user_id, key, value)
        elif action == "set":
            db.set_fact(user_id, key, value)
        elif action == "delete":
            db.delete_fact(user_id, key, value)
        log.info("Memory %s %s%s", action, key, f" = {value!r}" if value else "")


def extract_and_save_memories(user_id: str, message: str, history: list[dict],
                              callbacks: list | None = None) -> list[tuple[str, str, str | None]]:
    """Extract facts from the latest user message and store them. Returns the changes made.
    Previous messages only help resolve references: only the latest user message can establish a fact."""
    if not message or not message.strip():
        return []
    if not _may_contain_memory(message):
        log.info("Memory extraction skipped: nothing personal in the message")
        return []
    try:
        existing = _grouped(user_id)
        existing_text = "\n".join(f"{k}: {'; '.join(v)}" for k, v in existing.items()) or "(none)"
        history_text = "\n".join(
            f"{m['role']}: {' '.join(m['content'].split())[:400]}" for m in history[-2:]
        ) or "(no previous conversation)"
        result = structured(MemoryExtraction, memory=True).invoke(
            EXTRACT_PROMPT.format(existing=existing_text, history=history_text, message=message),
            config={"callbacks": callbacks or [], "metadata": {"usage_step": "memory_extract"}},
        )
        log.info("Memory extraction returned %d operation(s): %s", len(result.facts),
                 [op.model_dump() for op in result.facts])
        changes = plan_changes(existing, result.facts)
        apply_changes(user_id, changes)
        return changes
    except Exception:
        # A memory failure must not turn a successful chat response into an error.
        log.exception("Persistent memory extraction failed")
        return []


# ---------------------------------------------------------------- episode summaries
EPISODE_PROMPT = """Summarise this exchange between a user and an AI assistant in one or two sentences,
so it can be recalled in a later conversation. Write in the third person ("The user asked …; the assistant
explained …"). Keep the topic, any names, numbers, decisions and conclusions; drop greetings and filler.
Reply with the summary only.

USER: {question}

ASSISTANT: {answer}"""

_THINK = re.compile(r"<think>.*?</think>", re.S)


def summarize_exchange(question: str, answer: str, callbacks: list | None = None) -> str | None:
    """A one- or two-sentence summary of an exchange (memory model), or None if it couldn't be written."""
    try:
        reply = get_memory_llm().invoke(
            EPISODE_PROMPT.format(question=question[:1500], answer=answer[:3000]),
            config={"callbacks": callbacks or [], "metadata": {"usage_step": "episode_summary"}},
        ).content
        summary = " ".join(_THINK.sub("", reply).split())
        return summary[:600] or None
    except Exception:
        log.exception("Episode summary failed")
        return None
