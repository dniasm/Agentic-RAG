"""Small-talk / greeting tool.

Small talk is written by the same large model and persona as every other answer (see
direct_answer.converse), so the assistant sounds like one character whether it is chatting or answering.
It used to run on the small model, which also routes every message and extracts memories, and that made
the small model's per-minute token limit the bottleneck for casual conversation.
"""
from backend.tools import direct_answer


def respond(query: str, history: list[dict], username: str, doc_count: int,
            memories: list[dict] | None = None) -> str:
    return direct_answer.converse(query, history, username, doc_count, memories)
