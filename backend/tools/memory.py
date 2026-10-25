import json
import logging
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


def _may_contain_memory(message: str) -> bool:
    """Cheap filter to avoid an LLM call for ordinary messages."""
    text = message.casefold().replace("’", "'")
    return any(cue.replace("’", "'") in text for cue in MEMORY_CUES)

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

    try:
        # Existing memories let the extractor reuse keys when correcting
        # facts, rather than creating duplicate entries for the same fact.
        existing_memories = db.list_memories(user_id)

        existing_text = "\n".join(
            f"{m['memory_key']}: {m['memory_value']}"
            for m in existing_memories
        ) or "(no saved memories)"

        # Include recent context, but treat assistant messages as context,
        # not as proof that a fact about the user is true.
        recent_history = history[-6:]
        history_text = "\n".join(
            f"{m['role']}: {m['content']}"
            for m in recent_history
        ) or "(no previous conversation)"

        prompt = f"""
You are the persistent-memory manager for a personal AI assistant.

Extract useful information from the LATEST USER MESSAGE that will
improve future personalization, continuity, and assistance.

EXISTING MEMORIES:
{existing_text}

RECENT CONVERSATION:
{history_text}

LATEST USER MESSAGE:
{message}

Return zero or more structured memory operations using the required schema.

## 1. WHAT TO REMEMBER

Capture all distinct, useful personal facts expressed in the latest
user message, including:

- Identity and background: name, location, education, occupation,
  languages, roles, and relevant life circumstances.
- Preferences: favorites, likes, dislikes, rankings, communication style,
  learning preferences, and recurring choices.
- Interests and habits: hobbies, foods, drinks, entertainment, activities,
  everyday habits, and meaningful experiences.
- Goals and commitments: ambitions, plans, deadlines, and ongoing goals.
- Work and projects: technologies, tools, architecture, constraints,
  design decisions, hardware, and requirements likely to matter later.
- Continuity: names assigned to the assistant, recurring topics, and
  previous discussions the user wants to revisit.
- Changes and instructions: corrections, updated circumstances, and
  explicit requests to remember, update, or forget information.

A single message can contain multiple facts. Extract EVERY distinct,
useful fact rather than stopping after the first or most important one.

Casual preferences matter too. A favorite flower, preferred candy,
or fondness for coffee can help personalize future conversations even
if mentioned only once.

## 2. INTERPRET THE MESSAGE IN CONTEXT

Use the recent conversation to understand references, informal wording,
comparisons, corrections, and implied meanings that are clear from context.

Examples:
- "Coffee over tea, every time." expresses a preference.
- "Actually, I graduated last month." updates education status.
- "I like tulips, but roses are my favorite." expresses two distinct facts.
- "Keep explaining things that way." may establish a communication
  preference when the preceding context makes the meaning clear.
- "Let's revisit that Titanic question sometime." indicates an interest
  in returning to a previous discussion.

Do not require explicit phrases such as "remember this."

Only establish or update facts supported by the latest user message.
Use history to interpret that message, not as permission to repeatedly
extract every old fact.

Never treat assistant-generated statements as evidence about the user.
Do not turn hypothetical scenarios, fictional roleplay, quoted claims,
or assumptions into personal facts.

### Strict Personal-Fact Boundary

Before saving any fact, determine whose life, preference, experience, or circumstance it describes.

- Save facts about the user, not facts about the assistant or a hypothetical character.
- If the user asks the assistant to imagine being human, roleplay a character, or reason through a hypothetical scenario, treat the scenario's details as fictional or hypothetical unless the user independently establishes them as facts about themselves.
- Details such as a hypothetical spouse, children, occupation, location, possessions, or emergency circumstances must not be saved as user memories merely because they appear repeatedly in the conversation.
- Assistant-generated statements are never evidence of user attributes, even when the assistant repeats or elaborates on them.
- When the subject of a potentially personal fact is ambiguous, do not save it as a personal fact.
- Extract only facts supported by the latest user message, using history to interpret their meaning and ownership.

## 3. SELECT USEFUL FACTS

Prefer information that could help answer future questions, personalize
responses, or continue ongoing work.

Generally save personal preferences, favorites, dislikes, interests,
habits, meaningful experiences, and relevant background when clearly
expressed, even if casual or mentioned once.

Do not save every passing thought, isolated question, temporary activity,
or one-time instruction that has no likely future value.

Do not exaggerate or invent facts. Liking coffee does not necessarily
mean drinking it daily; mentioning a food does not automatically mean
it is a favorite.

When a fact is personal, specific, and plausibly useful later, lean
toward saving it rather than discarding it merely because its long-term
importance is uncertain.

Do not infer sensitive personal attributes or invent missing details.

## 4. MEMORY KEYS AND VALUES

Use concise, stable, lowercase keys, such as:
preferred_name, location, education, response_style, learning_preference,
favorite_flower, favorite_candy, favorite_drink, assistant_name,
active_project.

Reuse existing keys for the same underlying fact. Use separate keys
for genuinely distinct facts; group closely related facts only when
their individual meanings remain clear.

Keep values concise but informative enough to be understood without
the original conversation.

For project-specific facts, include the project name in the key or
value when necessary to prevent confusion between projects.

## 5. UPDATE EXISTING MEMORIES

Compare new facts with existing memories before choosing an action.

- ADD: Use action="add" for a new memory key that does not exist.
- APPEND: Use action="append" to add a distinct value to an existing
  multivalued memory. Preserve existing values and avoid duplicates.
- REPLACE: Use action="replace" only when the user clearly corrects
  a fact or changes an existing preference.
- NON-CONTRADICTORY PREFERENCES: Preserve compatible facts.
  "I also love lemonade" must not replace "coffee is my favorite drink."
- FAVORITES: Distinguish a collection of liked items from a singular
  favorite. For example, favorite_drinks may contain ["coffee",
  "lemonade"], while favorite_drink remains "coffee".
- AMBIGUITY: Do not overwrite a specific existing fact based on a guess.
- DUPLICATES: Do not append a value that is already present.
- Do not delete unrelated facts just because a related preference changes.

Do not delete unrelated facts just because a related preference changes.

## 6. FORGETTING

Use action="forget" only when the user clearly requests that a stored
fact be forgotten or deleted.

Identify the exact existing memory key. If the user requests forgetting
multiple facts, return an operation for each clearly identified key.

If the request is ambiguous, do not delete anything. Updating a fact
normally requires action="save", not "forget".

## 7. FINAL CHECK

Before returning, review the latest user message and verify that:
- Every distinct, useful fact has been considered.
- Multiple facts in one message have not been overlooked.
- Existing memories have been updated consistently.
- No unsupported personal claims or unnecessary duplicates were added.
- Explicit save, update, and forget instructions were handled correctly.

Return only the required structured output with facts containing the
appropriate key, value, and action: "add", "append", "replace", or
"forget".

If the message contains no useful new information, correction, update,
or explicit memory operation, return an empty facts list.

"""

        extractor = structured(MemoryExtraction, fast=True)
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

            if fact.action == "add":
                # Never overwrite an existing key with an add operation.
                if current is None and value:
                    db.save_memory(user_id, key, value)
                    memory_map[key] = value
                    log.info("Added memory key '%s'", key)

            elif fact.action == "append":
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

