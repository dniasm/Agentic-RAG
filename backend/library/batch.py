"""The queue: many document changes, run one at a time within Groq's tokens-per-minute limit.

The chat agent queues changes (queue_tasks). A background worker takes them in order. A structured change (rename,
replace, append) runs in code with no model call; an edit only waits for room for its summary refresh. A free-text
task runs through the same agent in its own session, once the minute has room for what the last one cost, and every
model call inside it waits for room too (limits.WINDOW). STATE holds the timer the chat page's meter shows.
"""
import logging
import threading
import time

from backend.library import agent, catalog, config, editing, library, limits, naming
from backend.library.logs import event

log = logging.getLogger(__name__)

_task_estimate = config.TASK_ESTIMATE_TOKENS   # what the next task is expected to cost, learned from the last
STATE: dict = {"waiting_until": None, "reason": "", "running_task": None}   # waiting_until is epoch seconds
_stop = threading.Event()
_wake = threading.Event()
_thread: threading.Thread | None = None


def _sleep(seconds: float) -> None:
    _stop.wait(seconds)


def wake() -> None:
    _wake.set()


def wait_for_room(max_used: int, reason: str, sleep=_sleep) -> None:
    """Block until at most `max_used` tokens are left in the minute's window."""
    while not _stop.is_set():
        seconds = limits.WINDOW.seconds_until(max_used)
        if seconds <= 0:
            break
        if STATE["waiting_until"] is None:
            log.info("waiting %.0fs: %s (%d of %d tokens used)", seconds, reason, limits.WINDOW.used(), limits.WINDOW.limit)
        STATE.update(waiting_until=time.time() + seconds, reason=reason)
        sleep(min(seconds, 1.0))
    STATE.update(waiting_until=None, reason="")


class PacedModel:
    """Wraps the agent's model: each call waits until the window has room for a call of the size seen last."""

    def __init__(self, inner, sleep=_sleep):
        self.inner, self.sleep, self.estimate = inner, sleep, config.CALL_ESTIMATE_TOKENS

    def invoke(self, messages):
        wait_for_room(limits.WINDOW.limit - self.estimate, "waiting for the token limit", self.sleep)
        reply = self.inner.invoke(messages)
        self.estimate = limits.tokens_of(reply) or self.estimate
        return reply

    def __getattr__(self, name):
        """Everything else is the wrapped model's: its "anything left?" check and its retry on the main model.
        Hiding them made a free-text task stop after its first change ("rename X and Y" renamed only X)."""
        if name == "inner":   # not set yet (copying, pickling): don't recurse
            raise AttributeError(name)
        return getattr(self.inner, name)


def _resolve(change: dict, owner: str = config.OWNER) -> tuple[dict | None, str]:
    """The document a change is about, in the owner's library: the one it was pinned to when queued, else named
    exactly or found uniquely. Returns (document, "") or (None, why not)."""
    if change.get("doc_id"):
        doc = catalog.get_document(change["doc_id"])
        if doc is None or doc["owner"] != owner:
            return None, f'"{change["document"]}" was removed from the library.'
        if doc["status"] == "indexed":
            return doc, ""
    named = [d for d in catalog.list_documents(status="indexed", owner=owner)
             if naming.same_name(d["title"], change["document"])]
    if len(named) > 1:
        return None, f'{len(named)} documents are called "{change["document"]}". Say which one.'
    if named:
        return named[0], ""
    found = library.find(change["document"], owner)
    if found["decision"] != "unique":
        options = ", ".join(f'"{c["title"]}"' for c in found.get("candidates", [])[:4])
        return None, (f'Could not tell which document "{change["document"]}" is ({found["decision"]})'
                      + (f": {options}." if options else "."))
    return catalog.get_document(found["candidates"][0]["doc_id"]), ""


def run_change(change: dict, before_applying=None, giving_up_names: frozenset = frozenset(),
               owner: str = config.OWNER) -> tuple[str, str, list]:
    """A structured change, done in code with the agent's own rules and no model call: (status, reply, actions).
    The document must be named exactly or found uniquely; anything doubtful is left for the user, not guessed.
    before_applying(doc) runs once the document is known, just before anything changes. giving_up_names: ids of
    documents that later changes in the same batch rename away, whose current names a rename may take now."""
    doc, problem = _resolve(change, owner)
    if doc is None:
        return "not_done", problem, []
    if before_applying:
        before_applying(doc)
    guard = agent.Guard({}, 1)
    guard.owner = owner
    guard.resolved[doc["id"]] = {"turn": 1, "by": "name"}
    actions: list = []
    if change["action"] == "rename":
        args = {"doc_id": doc["id"], "new_title": change["new_title"]}
        holders = library.name_conflicts(change["new_title"], exclude=doc["id"])
        if holders and all(h["id"] in giving_up_names for h in holders):
            # the name's holders are renamed away later in this batch (a swap or a cycle): take it now; for a moment
            # two documents share it, as the user's batch intends
            guard.pending.append({"doc_id": doc["id"], "new_title": change["new_title"], "turn": 0,
                                  "reason": "name_conflict"})
            args["confirmed"] = True
        result = agent.run_tool("rename_document", args, guard, actions)
    elif change["action"] == "undo":
        result = agent.run_tool("undo_edit", {"doc_id": doc["id"]}, guard, actions)
    else:
        result = agent.run_tool("edit_document", {"doc_id": doc["id"], "operation": change["action"],
                                                  "find": change.get("find", ""), "text": change.get("text", ""),
                                                  "replace_all": change.get("replace_all", False)}, guard, actions)
    if result.get("status") in agent.DONE_STATUSES:
        return "done", agent.compose_reply([result]), actions
    if result.get("status") == "unchanged":
        return "not_done", f'"{doc["title"]}" already has that name.', []
    if result.get("reason") == "pdf_conversion":
        return "not_done", (f'"{doc["title"]}" is a PDF, and this change cannot be made in it while keeping its look '
                            f'({result.get("why", "")}). Make it in the chat, where you can agree to a Markdown copy.'), []
    if result.get("status") == "needs_confirmation":
        others = ", ".join(f'"{c["title"]}"' for c in result["conflicts_with"])
        return "not_done", f'Another document already has the name "{result["new_title"]}" ({others}). Not renamed.', []
    return "not_done", result.get("next") or result.get("error") or str(result.get("status")), []


def _renamed_later(task: dict) -> frozenset:
    """Documents that queued renames after this task, in the same batch, will rename away."""
    batch = catalog.get_batch(task["batch_id"]) or {"tasks": []}
    return frozenset(t["payload"]["doc_id"] for t in batch["tasks"]
                     if t["id"] != task["id"] and t["status"] == "queued" and t["payload"]
                     and t["payload"]["action"] == "rename" and t["payload"].get("doc_id"))


def _refresh_expected(change: dict, owner: str = config.OWNER) -> bool:
    """Whether a queued change will need room for a summary refresh. A rename never does; a small edit doesn't.
    When the document can't be named exactly here, assume it will (on the safe side)."""
    if change["action"] == "rename":
        return False
    named = [d for d in catalog.list_documents(status="indexed", owner=owner)
             if naming.same_name(d["title"], change["document"])]
    if len(named) != 1:
        return True
    try:
        if change["action"] == "undo":
            return editing.undo_refresh_expected(named[0])
        return editing.refresh_expected(named[0], change["action"], change.get("find", ""), change.get("text", ""),
                                        change.get("replace_all", False))
    except Exception:
        log.warning("Couldn't tell whether %s needs a summary refresh", change["document"], exc_info=True)
        return True


def run_task(task: dict, sleep=_sleep, model=None) -> None:
    owner = (catalog.get_batch(task["batch_id"]) or {}).get("owner") or config.OWNER   # every task runs as its user
    if task.get("payload"):   # a structured change: no agent call; only larger edits cost tokens (the summary refresh)
        if _refresh_expected(task["payload"], owner):
            wait_for_room(limits.WINDOW.limit - config.EDIT_REFRESH_TOKENS, "waiting for room for the summary refresh", sleep)
        current = catalog.get_task(task["id"])
        if _stop.is_set() or not current or current["status"] != "queued":
            return
        catalog.update_task(task["id"], status="running", started_at=catalog.now())
        STATE["running_task"] = task["id"]
        def checkpoint(doc: dict) -> None:   # the document as it was, so a restart can tell whether this already ran
            catalog.update_task(task["id"], checkpoint={"doc_id": doc["id"], "sha256": doc["sha256"], "title": doc["title"]})
        try:
            status, reply, actions = run_change(task["payload"], before_applying=checkpoint,
                                                giving_up_names=_renamed_later(task), owner=owner)
        except Exception as e:
            log.exception("Queued change %s failed", task["id"])
            status, reply, actions = "failed", f"{type(e).__name__}: {str(e)[:200]}", []
        finally:
            STATE["running_task"] = None
        catalog.update_task(task["id"], status=status, reply=reply, actions=actions, finished_at=catalog.now())
        event("task_end", task=task["id"], batch=task["batch_id"], kind="change", status=status, said=reply[:160])
        return
    global _task_estimate
    wait_for_room(limits.WINDOW.limit - _task_estimate, "waiting for room in the token minute", sleep)
    current = catalog.get_task(task["id"])
    if _stop.is_set() or not current or current["status"] != "queued":   # stopped, or cancelled while waiting
        return
    catalog.update_task(task["id"], status="running", started_at=catalog.now())
    STATE["running_task"] = task["id"]
    try:
        result = agent.chat(f"batch-{task['batch_id']}-{task['id']}", task["instruction"],
                            model=PacedModel(model or agent.default_model(), sleep), batch=True, owner=owner)
        status = "failed" if result["failed"] else "done" if result["actions"] else "not_done"
        catalog.update_task(task["id"], status=status, reply=result["reply"], actions=result["actions"],
                            tokens=result["tokens"], finished_at=catalog.now())
        event("task_end", task=task["id"], batch=task["batch_id"], kind="free_text", status=status,
              tokens=result["tokens"], said=result["reply"][:160])
        if result["tokens"]:
            _task_estimate = int(result["tokens"] * 1.15)
    except Exception as e:
        log.exception("Queued task %s failed", task["id"])
        catalog.update_task(task["id"], status="failed", reply=f"{type(e).__name__}: {str(e)[:200]}",
                            finished_at=catalog.now())
    finally:
        STATE["running_task"] = None


def run_pending(sleep=_sleep, model=None) -> int:
    """Run every queued task now, in order. Returns how many ran. A task that raises is marked failed and the rest
    still run, so one bad task never stalls the queue."""
    n = 0
    while not _stop.is_set() and (task := catalog.next_queued_task()):
        try:
            run_task(task, sleep, model)
        except Exception as e:
            log.exception("Queued task %s failed", task["id"])
            catalog.update_task(task["id"], status="failed", reply=f"{type(e).__name__}: {str(e)[:200]}",
                                finished_at=catalog.now())
        n += 1
    return n


def recover_interrupted() -> None:
    """Tasks the server was running when it stopped. A structured change whose document has changed since it began
    (other content, or another title) most likely finished before the stop, so it is not run again: an append run
    twice would add its text twice. Anything else is queued again."""
    for task in catalog.tasks_with_status("running"):
        cp = task["checkpoint"]
        doc = catalog.get_document(cp["doc_id"]) if cp else None
        if cp and doc is None:
            catalog.update_task(task["id"], status="not_done", finished_at=catalog.now(),
                                reply="The document was removed while this change was running.")
        elif cp and (doc["sha256"] != cp["sha256"] or doc["title"] != cp["title"]):
            catalog.update_task(task["id"], status="done", finished_at=catalog.now(),
                                reply="Finished before the server stopped (the document had changed since this task "
                                      "began), so it was not run again. Check the document if in doubt.")
            event("task_recovered", task=task["id"], doc=doc["id"])
        else:
            catalog.update_task(task["id"], status="queued", started_at=None, checkpoint=None)


def _loop() -> None:
    recover_interrupted()
    event("queue_worker_started")
    while not _stop.is_set():
        try:
            if not run_pending():
                _wake.wait(5)
                _wake.clear()
        except Exception:   # the catalog itself failed: try again shortly
            log.exception("Queue worker error")
            _stop.wait(2)


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="batch", daemon=True)
    _thread.start()


def stop() -> None:
    event("queue_worker_stopped")
    _stop.set()
    _wake.set()


def _wait_seconds() -> float:
    waiting = STATE["waiting_until"]
    return round(max(0.0, waiting - time.time()), 1) if waiting else 0


def status_light(owner: str | None = None) -> dict:
    """The live numbers only (no task lists): cheap enough to poll every second."""
    return {**limits.WINDOW.snapshot(), "backup_used": limits.BACKUP_WINDOW.used(), "wait_seconds": _wait_seconds(),
            "reason": STATE["reason"], "running_task": STATE["running_task"], "queued": catalog.count_queued_tasks(owner)}


def summary(owner: str | None = None) -> dict:
    """What the agent tells the user when asked how the queue is going."""
    out = []
    for b in catalog.list_batches(3, owner=owner):
        counts: dict = {}
        for t in b["tasks"]:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        out.append({"batch_id": b["id"], "total": len(b["tasks"]), "counts": counts,
                    "needs_attention": [{"change": t["instruction"][:100], "said": t["reply"][:160], "status": t["status"]}
                                        for t in b["tasks"] if t["status"] in ("not_done", "failed")][:15]})
    return {"batches": out, "tokens": limits.WINDOW.snapshot()}


def status(owner: str | None = None) -> dict:
    """The queue panel: batches still running or queued in the last hour, with their tasks (polled every few
    seconds while anything runs; the meter reads status_light)."""
    return {"batches": catalog.active_or_recent_batches(owner=owner)}
