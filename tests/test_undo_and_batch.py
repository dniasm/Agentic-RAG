"""Undoing edits, the tokens-per-minute window, and the batch queue (fake clock: nothing really waits)."""
import pytest
from langchain_core.messages import AIMessage

from renamer import batch, catalog, config, editing, library, limits
from tests.test_agent import turn
from tests.test_editing import chunk_texts, stored


# ---------------------------------------------------------------- undo
def test_undo_restores_file_index_and_catalog(library_docs):
    doc = library_docs["svm.txt"]
    original = stored(doc)
    editing.edit(doc["id"], "replace", find="classifier", text="sorting machine")
    out = editing.undo(doc["id"])
    assert out["status"] == "undone" and out["edits_left"] == 0
    assert stored(doc) == original
    assert "classifier" in chunk_texts(doc["id"]) and "sorting machine" not in chunk_texts(doc["id"])
    assert catalog.get_document(doc["id"])["sha256"] == doc["sha256"]
    assert catalog.edit_history(doc["id"]) == []


def test_undo_goes_back_one_edit_at_a_time(library_docs):
    doc = library_docs["svm.txt"]
    editing.edit(doc["id"], "replace", find="classifier", text="one")
    editing.edit(doc["id"], "replace", find="Kernels", text="two")
    editing.undo(doc["id"])
    assert "one" in stored(doc) and "Kernels" in stored(doc)
    editing.undo(doc["id"])
    assert "classifier" in stored(doc)
    with pytest.raises(editing.EditError, match="nothing to undo"):
        editing.undo(doc["id"])


def test_undo_after_a_rename_restores_the_text_under_the_new_name(library_docs):
    doc = library_docs["svm.txt"]
    editing.edit(doc["id"], "replace", find="classifier", text="gadget")
    library.rename(doc["id"], "SVM Primer")
    editing.undo(doc["id"])
    assert "classifier" in (config.LIBRARY_DIR / "SVM Primer.txt").read_text(encoding="utf-8")


def test_agent_undo_needs_the_document_identified(library_docs):
    svm = library_docs["svm.txt"]
    editing.edit(svm["id"], "replace", find="classifier", text="gadget")
    _, results = turn("u1", [("undo_edit", {"doc_id": svm["id"]})])
    assert results[-1]["status"] == "needs_clarification" and "gadget" in stored(svm)
    out, results = turn("u2", [("find_documents", {"description": "svm.txt"})], [("undo_edit", {"doc_id": svm["id"]})])
    assert results[-1]["status"] == "undone" and out["actions"][0]["type"] == "undo"
    assert "classifier" in stored(svm)


# ---------------------------------------------------------------- token window
class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s
        self.slept += s


class NeverStopped:
    def is_set(self):
        return False

    def wait(self, seconds):
        pass


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(limits, "WINDOW", limits.TokenWindow(8000, 60.0, clock=c))
    monkeypatch.setattr(batch, "_stop", NeverStopped())
    monkeypatch.setattr(batch, "_task_estimate", config.TASK_ESTIMATE_TOKENS)
    return c


def test_window_frees_tokens_as_they_age(clock):
    w = limits.WINDOW
    w.record(3000)
    clock.t += 20
    w.record(2000)
    assert w.used() == 5000
    assert w.seconds_until(0) == pytest.approx(60)          # fully free when the newest ages out
    assert w.seconds_until(2000) == pytest.approx(40)       # once the first 3000 have aged out
    assert w.seconds_until(9000) == 0
    clock.t += 41
    assert w.used() == 2000
    clock.t += 20
    assert w.used() == 0 and w.snapshot()["free_in"] == 0


# ---------------------------------------------------------------- batch
class UsageModel:
    """Scripted tool calls; every call 'uses' `tokens`. Remembers the window's state at each call."""

    def __init__(self, steps, tokens=3000):
        self.steps, self.tokens, self.seen = list(steps), tokens, []

    def invoke(self, messages):
        self.seen.append(limits.WINDOW.used())
        calls = self.steps.pop(0) if self.steps else []
        return AIMessage("done" if not calls else "", tool_calls=[
            {"name": n, "args": a, "id": f"c{i}{n}"} for i, (n, a) in enumerate(calls)],
            usage_metadata={"input_tokens": self.tokens - 100, "output_tokens": 100, "total_tokens": self.tokens})


def test_agent_queues_tasks(library_docs):
    tasks = [f"In {n}, replace the word the with THE" for n in ("svm.txt", "glove.txt", "w2v.txt")]
    out, results = turn("q1", [("queue_tasks", {"tasks": tasks})])
    assert results[-1]["status"] == "queued" and results[-1]["tasks"] == 3
    assert out["actions"][0] == {"type": "queue", "batch_id": results[-1]["batch_id"], "tasks": 3}
    assert [t["status"] for t in catalog.get_batch(results[-1]["batch_id"])["tasks"]] == ["queued"] * 3


def test_queue_limits(library_docs):
    _, results = turn("q2", [("queue_tasks", {"tasks": []})])
    assert results[-1]["status"] == "error"
    _, results = turn("q3", [("queue_tasks", {"tasks": ["x"] * (config.BATCH_MAX_TASKS + 1)})])
    assert results[-1]["status"] == "error"
    _, results = turn("q4", [("queue_tasks", {"tasks": ["x" * (config.BATCH_TASK_MAX_CHARS + 1)]})])
    assert results[-1]["status"] == "error"


def test_small_tasks_share_a_minute_and_the_third_waits(library_docs, clock):
    """3,000 tokens a task: two fit in the 8,000-token minute at once; the third waits for room, about a minute."""
    names = ["svm.txt", "glove.txt", "w2v.txt"]
    steps = []
    for n in names:
        steps += [[("find_documents", {"description": n})],
                  [("edit_document", {"doc_id": library_docs[n]["id"], "operation": "append", "text": f"Note {n}."})]]
    created = catalog.add_batch("s", [f"Append a note to {n}" for n in names])
    model = UsageModel(steps, tokens=1500)
    batch.run_pending(sleep=clock.sleep, model=model)
    assert [t["status"] for t in catalog.get_batch(created["id"])["tasks"]] == ["done"] * 3
    assert all(used + 1500 <= 8000 for used in model.seen)
    assert model.seen[2] == 3000                      # the second task started straight after the first
    assert 55 < clock.slept < 65                      # only the third had to wait


def test_batch_waits_for_the_minute_to_clear_between_tasks(library_docs, clock):
    names = ["svm.txt", "glove.txt", "w2v.txt"]
    steps = []
    for n in names:
        doc_id = library_docs[n]["id"]
        steps += [[("find_documents", {"description": n})],
                  [("edit_document", {"doc_id": doc_id, "operation": "append", "text": f"Added to {n}."})]]
    created = catalog.add_batch("s", [f"Append a note to {n}" for n in names])
    model = UsageModel(steps)
    assert batch.run_pending(sleep=clock.sleep, model=model) == 3

    tasks = catalog.get_batch(created["id"])["tasks"]
    assert [t["status"] for t in tasks] == ["done"] * 3
    assert all(t["tokens"] == 6000 for t in tasks)                      # 2 model calls of 3000: find, edit
    assert model.seen[0::2] == [0, 0, 0]                                # each task began with the window empty
    assert all(used + 3000 <= 8000 for used in model.seen)              # and no call went past the limit
    assert clock.slept >= 60 * 2                                        # at least a minute between tasks
    for n in names:
        assert f"Added to {n}." in stored(library_docs[n])
    assert batch.STATE["waiting_until"] is None


def test_task_that_changes_nothing_is_reported_not_done(library_docs, clock):
    created = catalog.add_batch("s", ["Edit the quantum document"])
    batch.run_pending(sleep=clock.sleep, model=UsageModel([[("find_documents", {"description": "quantum computing"})]]))
    task = catalog.get_batch(created["id"])["tasks"][0]
    assert task["status"] == "not_done" and task["reply"] == "done"


def test_cancelled_tasks_do_not_run(library_docs, clock):
    created = catalog.add_batch("s", ["a", "b"])
    assert catalog.cancel_batch(created["id"]) == 2
    assert batch.run_pending(sleep=clock.sleep, model=UsageModel([])) == 0
    assert [t["status"] for t in catalog.get_batch(created["id"])["tasks"]] == ["cancelled"] * 2


def test_tasks_left_running_by_a_stopped_server_are_queued_again(library_docs):
    created = catalog.add_batch("s", ["a"])
    task = catalog.get_batch(created["id"])["tasks"][0]
    catalog.update_task(task["id"], status="running")
    catalog.requeue_running_tasks()
    assert catalog.get_task(task["id"])["status"] == "queued"


def test_a_queued_task_cannot_queue_more(library_docs, clock):
    created = catalog.add_batch("s", ["a"])
    batch.run_pending(sleep=clock.sleep, model=UsageModel([[("queue_tasks", {"tasks": ["b"]})]]))
    assert len(catalog.list_batches()) == 1
    assert catalog.get_batch(created["id"])["tasks"][0]["status"] == "not_done"


def test_live_meter_numbers_follow_the_window(library_docs, clock):
    catalog.add_batch("s", ["a", "b"])
    limits.WINDOW.record(5000)
    live = batch.status_light()
    assert live["used"] == 5000 and live["limit"] == 8000 and live["queued"] == 2
    assert live["free_in"] == pytest.approx(60)
    clock.t += 45
    assert batch.status_light()["free_in"] == pytest.approx(15)       # counts down as the minute passes
    clock.t += 15
    assert batch.status_light()["used"] == 0
