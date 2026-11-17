"""A long hold of the library's one lock is logged (once, for the outermost call), so slow operations that make every
other account wait show up in events.log."""
from backend.library import library


def test_a_slow_hold_is_logged_once_for_the_outermost_call(monkeypatch):
    seen = []
    monkeypatch.setattr(library, "event", lambda name, **f: seen.append((name, f)))
    monkeypatch.setattr(library, "LOCK_SLOW_SECONDS", 0.0)

    @library._exclusive
    def inner():
        return "x"

    @library._exclusive
    def outer():
        return inner()

    assert outer() == "x"
    assert [(n, f["op"]) for n, f in seen] == [("lock_held", "outer")]


def test_a_quick_hold_isnt_logged(monkeypatch):
    seen = []
    monkeypatch.setattr(library, "event", lambda name, **f: seen.append(name))

    @library._exclusive
    def quick():
        return 1

    quick()
    assert seen == []
