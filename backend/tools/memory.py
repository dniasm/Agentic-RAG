import json
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from backend.config.llm import structured
from backend.models import database as db

log = logging.getLogger(__name__)


class MemoryFact(BaseModel):
    key: str = Field(description="The memory key to modify.")
    value: str = Field(
        description="The value to add, append, replace, or forget."
    )
    action: Literal["add", "append", "replace", "forget"] = Field(
        description=(
            "add: create a new memory key; "
            "append: add a value to an existing collection; "
            "replace: change an existing memory value; "
            "forget: remove a memory value or key."
        )
    )


class MemoryExtraction(BaseModel):
    facts: list[MemoryFact] = Field(
        default_factory=list,
        description="Memory operations inferred from the user's message."
    )


MEMORY_CUES = (
    "i am ",
    "i'm ",
    "i’m ",
    "im ",
    "my name is ",
    "my age is ",
    "i live ",
    "i study ",
    "i'm studying ",
    "i’m studying ",
    "i work ",
    "i prefer ",
    "i like ",
    "i dislike ",
    "i hate ",
    "i love ",
    "i have ",
    "i've ",
    "i’ve ",
    "i want ",
    "i need ",
    "i use ",
    "call me ",
    "remember that ",
    "remember my ",
    "please remember ",
    "don't remember ",
    "do not remember ",
    "forget ",
    "from now on ",
    "going forward ",
    "your name is ",
    "call yourself ",
    "i'll call you ",
    "i’ll call you ",
    "i will call you ",
)


# Words that show the user is talking about themselves, or about what to remember.
_PERSONAL = re.compile(
    r"\b(i|i'm|im|i've|i'd|i'll|me|my|mine|myself|we|our|"
    r"remember|forget|favou?rite|prefer|call me|your name)\b"
)


def _may_contain_memory(message: str) -> bool:
    """Cheap filter to avoid an LLM call for messages with nothing personal in them
    ("thanks!", "what is BM25?")."""
    text = message.casefold().replace("’", "'")
    return any(cue.replace("’", "'") in text for cue in MEMORY_CUES) or bool(_PERSONAL.search(text))

def _memory_values(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [v for v in parsed if isinstance(v, str)]
    except (json.JSONDecodeError, TypeError):
        pass

    return [raw] if raw else []

def _encode_memory_values(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii = False)


def _contains_value(values: list[str], target: str) -> bool:
    return any(
        value.strip().casefold() == target.strip().casefold()
        for value in values
    )

def extract_and_save_memories(
    user_id: str,
    message: str,
    history: list[dict],
    callbacks: list | None = None,
) -> None:
    """
    Extract durable user-specific memories from the latest user message.

    Previous conversation history helps resolve corrections and references.
    Only the latest user message can establish a new fact.
    """

    if not message or not message.strip():
        return

    if not _may_contain_memory(message):
        log.info("Memory extraction skipped: nothing personal in the message")
        return

    try:
        # Existing memories let the extractor reuse keys when correcting
        # facts, rather than creating duplicate entries for the same fact.
        existing_memories = db.list_memories(user_id)

        existing_text = "\n".join(
            f"{m['memory_key']}: {m['memory_value']}"
            for m in existing_memories
        ) or "(no saved memories)"

        # Only the previous exchange, shortened: enough to resolve references
        # ("that one", "actually, ...") without re-sending long answers.
        recent_history = history[-2:]
        history_text = "\n".join(
            f"{m['role']}: {' '.join(m['content'].split())[:400]}"
            for m in recent_history
        ) or "(no previous conversation)"

        prompt = f"""You manage the persistent memory of a personal AI assistant.
Return memory operations for durable, useful facts the LATEST USER MESSAGE states about the user.
Return an empty list if it states none.

EXISTING MEMORIES:
{existing_text}

PREVIOUS EXCHANGE (context only; never a source of facts):
{history_text}

LATEST USER MESSAGE:
{message}

WHAT TO SAVE
- Identity and background (name, location, occupation, education, languages), preferences, likes and
  dislikes, favourites, hobbies, habits, goals, ongoing projects, and how the user wants answers.
- Every distinct fact in the message, including casual ones ("coffee over tea" is a preference).
- Explicit requests to remember, update or forget something.

WHAT NOT TO SAVE
- Questions, one-off tasks, passing remarks with no future value.
- Anything said by the assistant, or details of hypotheticals, role-play or fiction.
- Guesses: liking something does not make it a favourite.

KEYS: short, stable, lowercase snake_case (preferred_name, location, occupation, hobbies, liked_drinks,
favorite_drink, response_style). Reuse an existing key for the same kind of fact. Use plural keys for
collections (hobbies, liked_foods, liked_drinks, interests) and singular keys for single facts
(preferred_name, favorite_drink). One value per operation.

ACTIONS
- add: a key that does not exist yet.
- append: another value for an existing collection. Keep the existing values.
- replace: ONLY when the user corrects or changes a single fact ("actually I moved to Kathmandu").
- forget: ONLY when the user asks to forget something; value = the item to remove, or "*" for the whole key.
Liking something new never replaces an existing like.

Examples (the operations depend on what EXISTING MEMORIES already contain):
- No drink memories yet; "I also like coffee, but milk tea is my favourite drink"
  -> liked_drinks add "coffee"; favorite_drink add "milk tea". Both facts are saved.
- Existing "hobbies: trekking"; "I play futsal on weekends too" -> hobbies append "futsal".
- Existing "location: Pokhara"; "I moved to Kathmandu last month" -> location replace "Kathmandu"."""

        extractor = structured(MemoryExtraction, memory=True)
        result = extractor.invoke(
            prompt,
            config={
                "callbacks": callbacks or [],
                "metadata": {"usage_step": "memory_extract"},
            },
        )

        log.info(
            "Memory extraction returned %d operation(s): %s",
            len(result.facts),
            [
                {
                    "key": fact.key,
                    "value": fact.value,
                    "action": fact.action,
                }
                for fact in result.facts
            ],
        )

        
        # Track changes during this extraction too, so multiple operations
        # targeting the same key see the latest value.
        memory_map = {
            m["memory_key"]: m["memory_value"]
            for m in existing_memories
        }

        for fact in result.facts:
            key = fact.key.strip().lower()
            value = fact.value.strip()

            if not key or len(key) > 80 or len(value) > 1000:
                log.warning("Skipping invalid memory extraction.")
                continue

            current = memory_map.get(key)

            if fact.action == "add" and current is None:
                if value:
                    db.save_memory(user_id, key, value)
                    memory_map[key] = value
                    log.info("Added memory key '%s'", key)

            elif fact.action in ("add", "append"):
                # "add" on a key that already exists is treated as append: never
                # overwrite, and never silently drop the new value either.
                if not value:
                    continue

                values = _memory_values(current) if current is not None else []

                # Append only genuinely new values.
                if not _contains_value(values, value):
                    values.append(value)
                    encoded = _encode_memory_values(values)
                    db.save_memory(user_id, key, encoded)
                    memory_map[key] = encoded
                    log.info("Appended value to memory key '%s'", key)
                else:
                    log.info("Skipped duplicate value for memory key '%s'", key)

            elif fact.action == "replace":
                if value and current != value:
                    db.save_memory(user_id, key, value)
                    memory_map[key] = value
                    log.info("Replaced memory key '%s'", key)

            elif fact.action == "forget":
                if current is None:
                    continue

                values = _memory_values(current)

                # Remove just the requested value when the key contains
                # multiple values. Otherwise, delete the matching memory.
                if len(values) > 1 and value and value != "*":
                    remaining = [
                        item for item in values
                        if item.strip().casefold() != value.casefold()
                    ]

                    if len(remaining) != len(values):
                        if remaining:
                            encoded = _encode_memory_values(remaining)
                            db.save_memory(user_id, key, encoded)
                            memory_map[key] = encoded
                        else:
                            db.delete_memory(user_id, key)
                            memory_map.pop(key, None)

                        log.info("Removed value from memory key '%s'", key)

                elif value in ("", "*") or _contains_value(values, value):
                    db.delete_memory(user_id, key)
                    memory_map.pop(key, None)
                    log.info("Deleted memory key '%s'", key)


    except Exception:
        # A memory failure must not turn an otherwise successful chat
        # response into an error.
        log.exception("Persistent memory extraction failed")

