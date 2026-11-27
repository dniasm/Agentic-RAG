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


# ---- the WebSocket client (lib/realtimeSocket.js, lib/turn.js, lib/queue.js)

FAKE_WS = """
    const sockets = [];
    class FakeWS {
      constructor(url) { this.url = url; this.readyState = 0; this.sent = []; sockets.push(this); }
      send(t) { this.sent.push(t); }
      close() { this.readyState = 3; }
      say(msg) { this.onmessage({ data: JSON.stringify(msg) }); }
      drop(code = 1006) { this.readyState = 3; this.onclose({ code }); }
    }
    const timers = []; let clock = 0;
    const fakeTimers = {
      setTimeout: (fn, ms) => { timers.push({ fn, at: clock + ms }); return timers.length; },
      clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
    };
    const hello = (seq) => ({ seq: 0, channel: 'system', type: 'hello', data: { user: {}, seq } });
    const ev = (seq, channel, type, data) => ({ seq, channel, type, data });
"""


def test_the_socket_calls_the_real_timers_on_the_global_object():
    # Browsers throw "Illegal invocation" when setTimeout/clearTimeout run with any `this` but the window:
    # make node's timers just as strict and use the socket with its default timers.
    url = (SRC / "lib" / "realtimeSocket.js").as_uri()
    result = node(FAKE_WS + f"""
        for (const name of ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval']) {{
          const real = globalThis[name];
          globalThis[name] = function (...a) {{
            if (this !== globalThis && this !== undefined) throw new TypeError('Illegal invocation');
            return real(...a);
          }};
        }}
        const m = await import({json.dumps(url)});
        const s = new m.RealtimeSocket({{ makeUrl: () => 'ws://x', WS: FakeWS }});
        s.connect(); sockets[0].say(hello(1)); s.close();
        console.log(JSON.stringify({{ state: s.state }}));""")
    assert result["state"] == "closed"


def test_the_socket_resumes_from_its_last_event_and_gives_up_on_a_refusal():
    url = (SRC / "lib" / "realtimeSocket.js").as_uri()
    result = node(FAKE_WS + f"""
        const m = await import({json.dumps(url)});
        const s = new m.RealtimeSocket({{ makeUrl: (a) => m.socketUrl('/api', {{ protocol: 'http:', host: 'h:1' }}, a),
                                         WS: FakeWS, timers: fakeTimers, random: () => 0.5 }});
        const seen = []; s.subscribe((x) => seen.push(x.type));
        s.connect();
        const first = sockets[0].url;
        sockets[0].say(hello(7)); sockets[0].say(ev(8, 'queue', 'changed', {{}})); sockets[0].say(ev(9, 'queue', 'changed', {{}}));
        sockets[0].drop();                       // dropped: a retry is scheduled with backoff
        timers.pop().fn();                       // ... and runs
        const second = sockets[1].url;
        sockets[1].say(hello(9));
        sockets[1].drop(4401);                   // not signed in: no retry
        console.log(JSON.stringify({{ first, second, seen, state: s.state, retries: timers.length,
          delays: [0, 1, 2, 10].map((n) => m.backoffDelay(n, () => 0.5)),
          abs: m.socketUrl('https://api.example/api', {{ protocol: 'http:', host: 'x' }}),
          secure: m.socketUrl('/api', {{ protocol: 'https:', host: 'site' }}) }}));""")
    assert result["first"] == "ws://h:1/api/ws"
    assert result["second"] == "ws://h:1/api/ws?after=9"      # asks for everything after the last event it saw
    assert result["seen"] == ["hello", "changed", "changed", "hello"]
    assert result["state"] == "denied" and result["retries"] == 0
    assert result["delays"] == [500, 1000, 2000, 30000]
    assert result["abs"] == "wss://api.example/api/ws" or result["abs"] == "ws://api.example/api/ws"
    assert result["secure"] == "wss://site/api/ws"


def test_a_turn_gets_its_events_in_order_and_ignores_other_turns_and_early_events_wait_for_the_post():
    sock = (SRC / "lib" / "realtimeSocket.js").as_uri()
    turn = (SRC / "lib" / "turn.js").as_uri()
    result = node(FAKE_WS + f"""
        const m = await import({json.dumps(sock)});
        const t = await import({json.dumps(turn)});
        const s = new m.RealtimeSocket({{ makeUrl: () => 'ws://x', WS: FakeWS, timers: fakeTimers }});
        s.connect(); sockets[0].say(hello(0));
        const got = [];
        const handlers = Object.fromEntries(['Meta', 'Status', 'Step', 'Draft', 'Final'].map((k) => [`on${{k}}`, (d) => got.push(k + ':' + (d.label || d.text || d.answer || ''))]));
        const f = t.followTurn(s, 'turn-mine-1', handlers, async () => [], {{ timers: fakeTimers }});
        const chat = (seq, type, data, id = 'turn-mine-1') => sockets[0].say(ev(seq, 'chat', type, {{ conversation_id: 'c', turn_id: id, ...data }}));
        chat(1, 'status', {{ node: 'n', label: 'early' }});            // before the POST answered
        chat(2, 'status', {{ node: 'n', label: 'other' }}, 'turn-else-9');
        const done = f.follow('c');
        chat(3, 'draft', {{ text: 'abc' }});
        chat(4, 'final', {{ message_id: 'm', answer: 'Done' }});
        chat(5, 'status', {{ node: 'n', label: 'late' }});               // after the end: ignored
        await done;
        console.log(JSON.stringify(got));""")
    assert result == ["Status:early", "Draft:abc", "Final:Done"]


def test_a_turn_whose_socket_cannot_replay_finds_its_saved_answer():
    sock = (SRC / "lib" / "realtimeSocket.js").as_uri()
    turn = (SRC / "lib" / "turn.js").as_uri()
    result = node(FAKE_WS + f"""
        const m = await import({json.dumps(sock)});
        const t = await import({json.dumps(turn)});
        const s = new m.RealtimeSocket({{ makeUrl: () => 'ws://x', WS: FakeWS, timers: fakeTimers }});
        s.connect(); sockets[0].say(hello(0));
        let tick; const tm = {{ ...fakeTimers, setInterval: (fn) => {{ tick = fn; return 1; }} }};
        const msgs = [{{ id: 'm0', content: 'old', meta: {{}} }}, {{ id: 'm1', content: 'The answer', tool: 'rag', meta: {{ turn_id: 'turn-lost-01', trace: [1] }} }}];
        const got = [];
        const f = t.followTurn(s, 'turn-lost-01', {{ onFinal: (d) => got.push(d) }}, async () => msgs, {{ timers: tm }});
        const done = f.follow('c');
        tick();                                  // socket open and no resync: nothing is read
        await Promise.resolve();
        const before = got.length;
        sockets[0].say({{ seq: 0, channel: 'system', type: 'resync', data: {{}} }});
        tick(); await done;
        console.log(JSON.stringify({{ before, got }}));""")
    assert result["before"] == 0 and len(result["got"]) == 1
    assert result["got"][0]["answer"] == "The answer" and result["got"][0]["message_id"] == "m1"
    assert result["got"][0]["trace"] == [1] and result["got"][0]["conversation_id"] == "c"


def test_a_pushed_task_change_updates_the_queue_panels_state():
    url = (SRC / "lib" / "queue.js").as_uri()
    result = node(f"""
        const m = await import({json.dumps(url)});
        const q = {{ batches: [{{ id: 'b1', tasks: [{{ id: 1, status: 'queued', reply: '' }}, {{ id: 2, status: 'queued', reply: '' }}] }}] }};
        const next = m.applyTaskEvent(q, {{ batch_id: 'b1', task_id: 1, status: 'done', reply: 'ok' }});
        console.log(JSON.stringify({{ next, unchanged: q.batches[0].tasks[0].status,
          unknown: m.applyTaskEvent(q, {{ batch_id: 'b9', task_id: 1, status: 'done' }}),
          summary: m.queueSummary(next) }}));""")
    assert [t["status"] for t in result["next"]["batches"][0]["tasks"]] == ["done", "queued"]
    assert result["next"]["batches"][0]["tasks"][0]["reply"] == "ok" and result["unchanged"] == "queued"
    assert result["unknown"] is None and result["summary"]["done"] == 1 and result["summary"]["active"] is True


def test_a_file_added_from_the_chat_box_is_checked_and_its_chip_follows_the_library_events():
    rules = (SRC / "lib" / "uploadRules.js").as_uri()
    chips = (SRC / "lib" / "uploadChips.js").as_uri()
    result = node(f"""
        const r = await import({json.dumps(rules)});
        const m = await import({json.dumps(chips)});
        const run = (acts) => acts.reduce(m.uploadsReducer, m.initialUploads);
        const add = {{ type: 'add', cid: 1, key: 'new', name: 'a.pdf' }};
        const status = (s) => s.chips[0].status;
        const slow = run([add, {{ type: 'uploaded', cid: 1, docId: 'd1', docStatus: 'processing' }},
                          {{ type: 'event', docId: 'd1', stage: 'embedding', done: 2, total: 9 }}]);
        const early = run([add, {{ type: 'event', docId: 'd1', stage: 'indexed' }},
                           {{ type: 'uploaded', cid: 1, docId: 'd1', docStatus: 'processing' }}]);
        const failed = run([add, {{ type: 'uploaded', cid: 1, docId: 'd1', docStatus: 'processing' }},
                            {{ type: 'event', docId: 'd1', stage: 'failed', error: 'Over the daily limit' }}]);
        const polled = run([add, {{ type: 'uploaded', cid: 1, docId: 'd1', docStatus: 'processing' }},
                            {{ type: 'docs', docs: [{{ id: 'd1', status: 'indexed' }}] }}]);
        const moved = run([add, {{ type: 'rekey', from: 'new', to: 'c5' }}]);
        console.log(JSON.stringify({{
          ok: r.checkFile({{ name: 'A.PDF', size: 10 }}), badType: r.checkFile({{ name: 'a.exe', size: 1 }}),
          noExt: r.checkFile({{ name: 'README', size: 1 }}), big: r.checkFile({{ name: 'a.txt', size: 21 * 1024 * 1024 }}),
          uploading: status(run([add])), slow: [status(slow), slow.chips[0].detail], early: status(early),
          failed: [status(failed), failed.chips[0].detail], polled: status(polled),
          rejected: run([add, {{ type: 'rejected', cid: 1, error: 'Quota' }}]).chips[0].detail,
          removed: run([add, {{ type: 'remove', cid: 1 }}]).chips.length,
          moved: m.chipsFor(moved, 'c5').length, other: m.chipsFor(moved, 'new').length,
        }}));""")
    assert result["ok"] is None and "only PDF" in result["badType"] and "only PDF" in result["noExt"]
    assert "larger than 20 MB" in result["big"]
    assert result["uploading"] == "uploading" and result["slow"] == ["indexing", "Indexing 2/9"]
    assert result["early"] == "ready" and result["failed"] == ["failed", "Over the daily limit"]
    assert result["polled"] == "ready" and result["rejected"] == "Quota" and result["removed"] == 0
    assert result["moved"] == 1 and result["other"] == 0


def test_a_chat_opens_at_its_latest_message_once_the_messages_are_there():
    url = (SRC / "lib" / "uploadChips.js").as_uri()
    result = node(f"""
        const {{ shouldJumpToBottom: j }} = await import({json.dumps(url)});
        console.log(JSON.stringify({{
          loading: j({{ key: 'c1', positionedFor: null, messageCount: 0 }}),
          arrived: j({{ key: 'c1', positionedFor: null, messageCount: 12 }}),
          streaming: j({{ key: 'c1', positionedFor: 'c1', messageCount: 13 }}),
          switched: j({{ key: 'c2', positionedFor: 'c1', messageCount: 4 }}),
        }}));""")
    assert result == {"loading": False, "arrived": True, "streaming": False, "switched": True}
