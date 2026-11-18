"""The front end's logic that runs without a page, through node: what the chat's document actions allow
(lib/chatActions.js) and how the preview keeps one browser-history entry (state/previewStore.js), so a phone's Back
closes the preview instead of leaving the site.

    python -m pytest backend/tests/test_frontend_logic.py
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")


def node(script: str) -> dict:
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", cwd=SRC.parent)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_what_the_chats_undo_and_redo_buttons_apply_to():
    url = (SRC / "lib" / "chatActions.js").as_uri()
    result = node(f"""
        const m = await import({json.dumps(url)});
        const docs = [{{ latest_edit_id: 5, redo_edit_id: null }}, {{ latest_edit_id: null, redo_edit_id: 9 }},
                      {{ latest_edit_id: 7, redo_edit_id: 3 }}];
        console.log(JSON.stringify({{
          undoable: [...m.undoableEdits(docs)].sort(),
          undoableAfterUndo: [...m.undoableEdits(docs, new Set([7]))].sort(),
          redoable: [...m.redoableEdits(docs)].sort(),
          none: [...m.undoableEdits(null)],
          rename: m.describeAction({{ type: 'rename', old_title: 'A', new_title: 'B' }}),
          edits: m.describeAction({{ type: 'edit', title: 'T', operation: 'replace', changes: 2 }}),
        }}));""")
    assert result["undoable"] == [5, 7] and result["undoableAfterUndo"] == [5]
    assert result["redoable"] == [3, 9] and result["none"] == []
    assert result["rename"] == "Renamed “A” to “B”" and result["edits"] == "Edited “T” (replace, 2 changes)"


# A stand-in for the browser: history entries, back() firing popstate, and the window's listeners.
HISTORY = """
    const listeners = {};
    const entries = [{ state: null }];
    let at = 0;
    const history = {
      get state() { return entries[at].state; },
      get length() { return entries.length; },
      pushState(state) { entries.splice(at + 1); entries.push({ state }); at += 1; },
      replaceState(state) { entries[at] = { state }; },
      back() { if (at > 0) { at -= 1; (listeners.popstate || []).forEach((f) => f({})); } },
    };
    globalThis.window = { history, addEventListener: (t, f) => (listeners[t] ||= []).push(f) };
"""


def store(steps: str, start_state: str = "null") -> dict:
    url = (SRC / "state" / "previewStore.js").as_uri()
    return node(HISTORY + f"""
        entries[0].state = {start_state};
        const s = await import({json.dumps(url)});
        let shown = null;
        const peek = () => ({{ entries: entries.length, at, state: history.state }});
        const log = [];
        {steps}
        console.log(JSON.stringify(log));""")


def test_opening_the_preview_adds_one_entry_and_another_document_replaces_it():
    log = store("""
        s.openPreview({ id: 'a' }); log.push(peek());
        s.openPreview({ id: 'b' }); log.push(peek());""")
    assert log[0] == {"entries": 2, "at": 1, "state": {"preview": "a"}}
    assert log[1] == {"entries": 2, "at": 1, "state": {"preview": "b"}}   # replaced, not a second entry


def test_back_closes_the_preview_and_close_goes_back_over_its_entry():
    log = store("""
        s.openPreview({ id: 'a' }); history.back(); log.push(peek());
        s.openPreview({ id: 'a' }); s.closePreview(); log.push(peek());""")
    assert log[0]["at"] == 0 and log[0]["state"] is None        # Back: the preview closed, the site stays
    assert log[1]["at"] == 0 and log[1]["state"] is None        # X: no stale entry left behind


def test_back_with_an_editor_open_keeps_the_preview():
    log = store("""
        s.openPreview({ id: 'a' });
        let editorOpen = true;
        s.onPreviewBack(() => { if (editorOpen) { editorOpen = false; return false; } return true; });
        history.back(); log.push({ ...peek(), editorOpen });     // the editor closed; the preview's entry is back
        history.back(); log.push(peek());                       // then the preview""")
    assert log[0]["state"] == {"preview": "a"} and log[0]["editorOpen"] is False
    assert log[1]["state"] is None


def test_a_reload_with_a_stale_entry_clears_it():
    log = store("log.push(peek());", start_state="{ preview: 'old' }")
    assert log[0]["state"] is None   # nothing is open after a reload: the entry would make Back a dead press
