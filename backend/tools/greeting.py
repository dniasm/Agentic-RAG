"""Small-talk / greeting tool."""
from langchain_core.messages import SystemMessage, HumanMessage

from backend.config.llm import get_fast_llm
from backend.tools import history_to_messages

SYSTEM = """You are the friendly assistant of an Agentic RAG workspace. The user is making small talk
(greeting, thanks, goodbye, asking who you are or what you can do). Reply warmly in 1-3 sentences.
If it fits, mention you can: answer questions from their uploaded documents, search the web for
recent information, and answer general knowledge questions. The user's name is {username}.
They currently have {doc_count} document(s) indexed."""


def respond(query: str, history: list[dict], username: str, doc_count: int) -> str:
    messages = [
        SystemMessage(SYSTEM.format(username=username, doc_count=doc_count)),
        *history_to_messages(history[-4:]),
        HumanMessage(query),
    ]
    return get_fast_llm().invoke(messages).content.strip()
