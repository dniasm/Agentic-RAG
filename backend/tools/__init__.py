import re
from datetime import date

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


def history_to_messages(history: list[dict]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for m in history:
        if m["role"] == "user":
            out.append(HumanMessage(m["content"]))
        elif m["role"] == "assistant":
            out.append(AIMessage(m["content"]))
    return out


def history_to_text(history: list[dict], max_chars: int = 600) -> str:
    if not history:
        return "(no prior conversation)"
    return "\n".join(f"{m['role'].upper()}: {m['content'][:max_chars]}" for m in history)


def today() -> str:
    return date.today().strftime("%B %d, %Y")


_CITE = re.compile(r"【(\d+)(?:†[^】]*)?】")


def clean_answer(text: str) -> str:
    """Normalize model-specific citation markers (e.g. gpt-oss's 【1†L1-L3】) to [1]."""
    return _CITE.sub(r"[\1]", text).strip()



FORMAT_RULES = (
    "Use concise, natural prose by default. Answer the exact question first. "
    "For simple questions and conversational follow-ups, usually respond in "
    "1–3 sentences. Expand only when the question genuinely requires detail. "
    "Do not add headings, tables, lists, alternative viewpoints, objections, "
    "or a separate conclusion unless they materially improve the answer. "
    "For hypothetical choices, state your choice directly before explaining "
    "the decisive reason. For follow-up questions, address the specific "
    "challenge or new information rather than restating the previous answer. "
    "Use GitHub-flavored Markdown when useful. No HTML. Cite sources only as [n]."
)
