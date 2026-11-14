"""One Groq token window for the whole app: the chat's calls count against the batch queue's pacing."""
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from backend.config.usage import UsageTracker
from backend.library import config, limits


def _reply(model: str, tokens: int) -> LLMResult:
    message = AIMessage("x", usage_metadata={"input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens},
                        response_metadata={"model_name": model})
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def _call(tracker, step, model, tokens, run_id):
    tracker.on_chat_model_start({}, [], run_id=run_id, metadata={"langgraph_node": step})
    tracker.on_llm_end(_reply(model, tokens), run_id=run_id)


def test_the_chats_calls_count_against_the_batch_windows(monkeypatch):
    main, backup = limits.TokenWindow(10_000), limits.TokenWindow(10_000)
    monkeypatch.setattr(limits, "WINDOW", main)
    monkeypatch.setattr(limits, "BACKUP_WINDOW", backup)
    tracker = UsageTracker()
    _call(tracker, "generate_rag", config.AGENT_MODEL, 1200, "r1")
    _call(tracker, "router", config.BACKUP_MODEL, 700, "r2")
    _call(tracker, "manage", config.AGENT_MODEL, 900, "r3")   # the agent records its own calls
    _call(tracker, "memory", "qwen/qwen3.8-27b", 500, "r4")    # another model, another limit
    assert (main.used(), backup.used()) == (1200, 700)
