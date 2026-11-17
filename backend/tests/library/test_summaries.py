"""The summary refresh after a change runs in the background, outside the library lock (backend/library/summaries.py):
the change doesn't wait for the model; a later change wins; one pending refresh per document; a failure keeps the
old summary."""
import threading

from backend.library import catalog, editing, summaries, titles


def _gate(monkeypatch, written=True, fail=False):
    """profile_document waits at a gate (so the test can act while the model 'runs') and counts its calls."""
    gate, calls = threading.Event(), []

    def profile(filename, text, existing, ask=None):
        calls.append(text[:30])
        gate.wait(10)
        if fail:
            raise RuntimeError("model down")
        return titles.DocProfile(title="T", summary=f"summary {len(calls)}", keywords=["k"]), written
    monkeypatch.setattr(titles, "profile_document", profile)
    return gate, calls


BIG = "Zebrafish are striped fish from South Asia. " * 30


def test_the_change_doesnt_wait_for_the_model(library_docs, monkeypatch):
    doc = library_docs["svm.txt"]
    gate, calls = _gate(monkeypatch)
    out = editing.edit(doc["id"], "append", text=BIG)                 # returns while the model is still "running"
    assert out["status"] == "edited" and not gate.is_set()
    gate.set()
    assert summaries.wait_idle()
    assert catalog.get_document(doc["id"])["summary"] == "summary 1" and len(calls) == 1


def test_a_later_change_wins_over_a_stale_refresh(library_docs, monkeypatch):
    doc = library_docs["svm.txt"]
    gate, calls = _gate(monkeypatch)
    editing.edit(doc["id"], "append", text=BIG)                      # refresh 1 starts, blocked at the gate
    old_summary = catalog.get_document(doc["id"])["summary"]
    catalog.update_document(doc["id"], sha256="changed-since")       # the document changed while the model ran
    gate.set()
    assert summaries.wait_idle()
    assert catalog.get_document(doc["id"])["summary"] == old_summary  # the stale result wasn't stored


def test_one_pending_refresh_per_document(library_docs, monkeypatch):
    first, second = library_docs["svm.txt"], library_docs["glove.txt"]
    gate, calls = _gate(monkeypatch)
    editing.edit(first["id"], "append", text=BIG)                    # the worker is busy with this one
    for word in ("Alpha", "Beta", "Gamma"):                          # three changes to another document meanwhile
        editing.edit(second["id"], "append", text=f"{word} section. " + BIG)
    gate.set()
    assert summaries.wait_idle()
    assert len(calls) == 2                                           # one refresh each, the newest for the second


def test_a_failed_refresh_keeps_the_old_summary(library_docs, monkeypatch):
    doc = library_docs["svm.txt"]
    gate, calls = _gate(monkeypatch, fail=True)
    gate.set()
    before = catalog.get_document(doc["id"])["summary"]
    editing.edit(doc["id"], "append", text=BIG)
    assert summaries.wait_idle()
    assert catalog.get_document(doc["id"])["summary"] == before
