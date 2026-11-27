/** One chat answer, followed over the WebSocket. POST /chat/stream starts it (202); its events then arrive on the
    socket tagged with the turn's id. Framework-free (backend/tests/test_frontend_logic.py runs it under node).

    The answer is never lost to a flaky socket: events missed while the socket was down are replayed when it
    reconnects; when the server can't replay (system/resync) or the socket stays down, the saved answer is read from
    GET /conversations/{id}/messages (the message carries meta.turn_id) every FALLBACK_MS until it is there. */

export const FALLBACK_MS = 3000;

export const newTurnId = () => (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}${Math.random()}`).replace(/[^A-Za-z0-9_-]/g, '');

/** The final-event payload rebuilt from a saved assistant message (what the socket's `final` would have said). */
export function finalFromMessage(conversationId, message) {
  const { turn_id, ...meta } = message.meta || {};
  return { ...meta, turn_id, conversation_id: conversationId, message_id: message.id, answer: message.content, tool: message.tool };
}

/**
 * Follow turn `turnId`. `socket` is a RealtimeSocket; `getMessages(conversationId)` reads the saved conversation.
 * Returns { follow(conversationId), stop() } — `follow` is called once the 202 names the conversation; the promise
 * it returns resolves after onFinal, and rejects with the server's message when the turn ends in a fatal error.
 * Events that arrive before `follow` (the socket is faster than the POST's response) are kept and delivered then.
 */
export function followTurn(socket, turnId, handlers, getMessages, { fallbackMs = FALLBACK_MS, timers = globalThis } = {}) {
  const { onMeta, onStatus, onStep, onDraft, onFinal } = handlers;
  let conversationId = null;
  let finished = false;
  let resynced = false;
  let finish;
  let fail;
  const done = new Promise((resolve, reject) => { finish = resolve; fail = reject; });
  const early = [];
  let off = () => {};
  let ticker = null;

  const end = () => { finished = true; off(); timers.clearInterval(ticker); };

  const dispatch = (msg) => {
    if (finished) return;
    const { type, data } = msg;
    if (type === 'meta') onMeta?.(data);
    else if (type === 'status') onStatus?.(data);
    else if (type === 'step') onStep?.(data);
    else if (type === 'draft') onDraft?.(data);
    else if (type === 'final') { end(); onFinal?.(data); finish(); }
    else if (type === 'error' && data.fatal) { end(); fail(new Error(data.message)); }
  };

  off = socket.subscribe((msg) => {
    if (msg.channel === 'system' && msg.type === 'resync') { resynced = true; return; }
    if (msg.channel !== 'chat' || msg.data?.turn_id !== turnId) return;
    if (conversationId) dispatch(msg); else early.push(msg);
  });

  const readSaved = async () => {
    if (finished || !conversationId) return;
    try {
      const saved = (await getMessages(conversationId)).find((m) => m.meta?.turn_id === turnId);
      if (saved) dispatch({ type: 'final', data: finalFromMessage(conversationId, saved) });
    } catch { /* the next tick tries again */ }
  };

  return {
    follow(id) {
      conversationId = id;
      early.splice(0).forEach(dispatch);
      ticker = timers.setInterval(() => { if (resynced || !socket.isOpen()) readSaved(); }, fallbackMs);
      return done;
    },
    stop() { end(); },
  };
}
