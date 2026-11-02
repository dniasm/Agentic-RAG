// Conversations live here, outside any page component, so an answer that is still being written keeps
// streaming when you switch to Documents or another chat, and is there when you come back.
//
// Each entry: { messages, loaded, streaming, error }. Keyed by conversation id; a chat that has no id yet
// (its first message is in flight) is under NEW_KEY until the server names it.
import { useSyncExternalStore } from 'react';
import { apiService } from '../services/api';

export const NEW_KEY = 'new';
const EMPTY = Object.freeze({ messages: [], loaded: true, streaming: false, error: null });

const entries = new Map();
const listeners = new Set();

function emit() {
  listeners.forEach((l) => l());
}

function set(key, patch) {
  const prev = entries.get(key) || EMPTY;
  entries.set(key, { ...prev, ...patch });
  emit();
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useConversation(key) {
  return useSyncExternalStore(subscribe, () => entries.get(key) || EMPTY);
}

/** Streaming state for every conversation, for the sidebar's "answering" marks. */
export function useStreamingKeys() {
  const snapshot = useSyncExternalStore(subscribe, () => streamingSignature());
  return snapshot ? snapshot.split('|') : [];
}

let lastSignature = '';
function streamingSignature() {
  const sig = [...entries].filter(([, e]) => e.streaming).map(([k]) => k).sort().join('|');
  if (sig !== lastSignature) lastSignature = sig;
  return lastSignature;
}

export const fromApi = (m) => ({
  id: m.id,
  sender: m.role === 'user' ? 'user' : 'agent',
  text: m.content,
  tool: m.tool,
  sources: m.meta?.sources || [],
  notices: m.meta?.notices || [],
  trace: m.meta?.trace || [],
  rewrittenQueries: m.meta?.rewritten_queries || [],
  evaluation: m.meta?.evaluation || null,
  latencyMs: m.meta?.latency_ms,
  createdAt: m.created_at,
});

/** Load a saved conversation, unless it is already here (loaded, or an answer is streaming into it). */
export async function loadConversation(id) {
  const current = entries.get(id);
  if (current && (current.loaded || current.streaming || current.loading)) return;
  set(id, { messages: current?.messages || [], loaded: false, loading: true, error: null });
  try {
    const msgs = await apiService.getMessages(id);
    set(id, { messages: msgs.map(fromApi), loaded: true, loading: false });
  } catch (err) {
    set(id, { loaded: false, loading: false, error: err.message });
  }
}

export function forgetConversation(id) {
  entries.delete(id);
  emit();
}

/** Reset the new-chat slot (after its first answer has moved to a real id, or to start over). */
export function clearNewChat() {
  const e = entries.get(NEW_KEY);
  if (!e || !e.streaming) {
    entries.delete(NEW_KEY);
    emit();
  }
}

/**
 * Send a message. `conversationId` is null for a new chat.
 * onCreated(id): the server named the new conversation. onDone(): the answer is saved.
 */
export async function sendMessage(text, conversationId, { onCreated, onDone } = {}) {
  let key = conversationId || NEW_KEY;
  const stamp = Date.now();
  const prev = entries.get(key) || EMPTY;
  set(key, {
    loaded: true,
    streaming: true,
    error: null,
    messages: [
      ...prev.messages,
      { id: `u-${stamp}`, sender: 'user', text },
      { id: `a-${stamp}`, sender: 'agent', pending: true, steps: [], status: null, draft: '' },
    ],
  });

  const updatePending = (fn) => {
    const e = entries.get(key);
    if (!e) return;
    set(key, { messages: e.messages.map((m) => (m.pending ? fn(m) : m)) });
  };

  try {
    await apiService.streamMessage(text, conversationId, {
      onMeta: ({ conversation_id }) => {
        if (key === NEW_KEY && conversation_id) {
          entries.set(conversation_id, entries.get(NEW_KEY));
          entries.delete(NEW_KEY);
          key = conversation_id;
          emit();
          onCreated?.(conversation_id);
        }
      },
      onStatus: (status) => updatePending((m) => ({
        ...m,
        status: status.label,
        // A new answer-writing step starts: an earlier draft was rejected and is being rewritten.
        draft: ['generate_rag', 'generate_web', 'direct', 'greeting'].includes(status.node) ? '' : m.draft,
      })),
      onStep: (step) => updatePending((m) => ({ ...m, steps: [...m.steps, step] })),
      onDraft: ({ text: draft }) => updatePending((m) => ({ ...m, draft })),
      onFinal: (data) => updatePending(() => ({
        id: data.message_id,
        sender: 'agent',
        text: data.answer,
        tool: data.tool,
        sources: data.sources,
        notices: data.notices,
        trace: data.trace,
        rewrittenQueries: data.rewritten_queries,
        evaluation: data.evaluation,
        latencyMs: data.latency_ms,
      })),
    });
  } catch (err) {
    updatePending(() => ({
      id: `e-${Date.now()}`,
      sender: 'agent',
      error: true,
      text: err.status === 0 || err.message === 'Failed to fetch'
        ? 'Could not reach the server. Your question was not answered; send it again once the server is running.'
        : err.message,
    }));
  } finally {
    // A stream that ended without a final event leaves no spinner behind.
    updatePending((m) => (m.pending
      ? { id: `e-${Date.now()}`, sender: 'agent', error: true, text: 'The answer was interrupted. Send the question again.' }
      : m));
    set(key, { streaming: false });
    onDone?.(key);
  }
}
