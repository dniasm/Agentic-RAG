"""Per-request token accounting: which model and which step spent the tokens.

Attach a UsageTracker as a LangChain callback (graph runs pass it through their config; calls made inside
a node inherit it). Calls made outside the graph can name their step with the "usage_step" metadata key,
e.g. `chain.invoke(prompt, config={"callbacks": [tracker], "metadata": {"usage_step": "memory_extract"}})`.

Each finished request is logged in one line and appended to backend/data/token_usage.jsonl, so real
chats can be analysed afterwards.
"""
import json
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler

from backend.config.settings import settings

log = logging.getLogger(__name__)

_file_lock = threading.Lock()


def _blank() -> dict:
    return {"calls": 0, "input": 0, "output": 0, "reasoning": 0}


class UsageTracker(BaseCallbackHandler):
    def __init__(self):
        self._lock = threading.Lock()
        self._step_of_run: dict = {}
        self.by_model: dict[str, dict] = defaultdict(_blank)
        self.by_step: dict[str, dict] = defaultdict(_blank)

    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        metadata = metadata or {}
        with self._lock:
            self._step_of_run[run_id] = metadata.get("usage_step") or metadata.get("langgraph_node") or "other"

    def on_llm_end(self, response, *, run_id, **kwargs):
        with self._lock:
            step = self._step_of_run.pop(run_id, "other")
            for generations in response.generations:
                for g in generations:
                    msg = getattr(g, "message", None)
                    usage = getattr(msg, "usage_metadata", None) or {}
                    model = (getattr(msg, "response_metadata", None) or {}).get("model_name", "?")
                    reasoning = (usage.get("output_token_details") or {}).get("reasoning", 0) or 0
                    for bucket in (self.by_model[model], self.by_step[step]):
                        bucket["calls"] += 1
                        bucket["input"] += usage.get("input_tokens", 0)
                        bucket["output"] += usage.get("output_tokens", 0)
                        bucket["reasoning"] += reasoning

    def on_llm_error(self, error, *, run_id, **kwargs):
        with self._lock:
            self._step_of_run.pop(run_id, None)

    def summary(self) -> dict:
        with self._lock:
            by_model = {k: dict(v) for k, v in self.by_model.items()}
            by_step = {k: dict(v) for k, v in self.by_step.items()}
        return {
            "total": {
                "input": sum(m["input"] for m in by_model.values()),
                "output": sum(m["output"] for m in by_model.values()),
            },
            "by_model": by_model,
            "by_step": by_step,
        }


def record(tracker: UsageTracker, **context) -> dict:
    """Log the request's usage in one line and append it to token_usage.jsonl."""
    s = tracker.summary()
    models = ", ".join(f"{m.split('/')[-1]} {v['input']}+{v['output']}" for m, v in s["by_model"].items())
    steps = ", ".join(f"{k} {v['input']}+{v['output']}" for k, v in s["by_step"].items())
    log.info("Token usage (in+out): total %d+%d | %s | %s",
             s["total"]["input"], s["total"]["output"], models or "no LLM calls", steps)
    try:
        path = Path(settings.database_path).parent / "token_usage.jsonl"
        row = {"at": datetime.now(timezone.utc).isoformat(), **context, **s}
        with _file_lock, path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        log.exception("Could not write token usage log")
    return s
