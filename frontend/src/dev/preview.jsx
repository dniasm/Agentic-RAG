// Design preview: the real app with the API replaced by sample data, so every screen can be opened and
// inspected without a backend or a login. Dev server only (vite build ships index.html alone).
//   /preview.html#/chat/c1 · #/chat · #/documents · #/overview
//   ?state=empty (no documents, no usage) · ?state=loggedout · ?state=offline (login, server down)
//   ?theme=dark or ?theme=light
import React from 'react';
import ReactDOM from 'react-dom/client';
import '../index.css';
import { apiService } from '../services/api';
import { realtime } from '../services/realtime';
import { followTurn, newTurnId } from '../lib/turn';
import * as fx from './fixtures';

const params = new URLSearchParams(window.location.search);
const state = params.get('state') || 'full';
// ?theme=dark | light: preview a theme without touching the saved choice's default (system).
if (['dark', 'light'].includes(params.get('theme'))) {
  try { localStorage.setItem('agentic_rag_theme', params.get('theme')); } catch { /* ignore */ }
  document.documentElement.setAttribute('data-theme', params.get('theme'));
}
const empty = state === 'empty';
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// Signed in or not is the login cookie now (no token in the page): logged out / offline is me() refusing.
const signedOut = state === 'loggedout' || state === 'offline';


// A fake WebSocket: the app's `realtime` object answers hello and carries the events the server would send.
const listeners = new Set();
let seq = 0;
const emit = (channel, type, data, request_id = '') => {
  const msg = { seq: ++seq, channel, type, request_id, data, ts: new Date().toISOString() };
  listeners.forEach((fn) => fn(msg));
};
Object.assign(realtime, {
  connect() { emit('system', 'hello', { seq: 0 }); },
  close() {},
  isOpen: () => true,
  ready: async () => true,
  subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
});

async function playTurn(turnId, conversationId, text) {
  const tag = (data) => ({ turn_id: turnId, ...data });
  await wait(100);
  emit('chat', 'meta', tag({ conversation_id: conversationId, title: text.slice(0, 40) }), turnId);
  const steps = [
    ['router', 'Reading your message…', 'Information request about the uploaded documents.'],
    ['retrieve', 'Searching your documents…', 'Attempt 1: hybrid search; 1 query, 20 candidate chunk(s).'],
    ['grade', 'Checking which passages are relevant…', 'Reranked 20 candidates locally: best relevance 0.74 -> strong match.'],
  ];
  for (const [node, label, detail] of steps) {
    emit('chat', 'status', tag({ node, label }), turnId);
    await wait(500);
    emit('chat', 'step', tag({ id: node, node: node === 'grade' ? 'grade_documents' : node, detail, data: {} }), turnId);
  }
  emit('chat', 'status', tag({ node: 'generate_rag', label: 'Writing the answer from your documents…' }), turnId);
  const answer = 'This is a preview answer written from sample data. In the app, the answer streams in here while it is written, and its sources and steps appear below it when it is finished [1].';
  for (let i = 20; i <= answer.length; i += 20) { emit('chat', 'draft', tag({ node: 'generate_rag', text: answer.slice(0, i) }), turnId); await wait(60); }
  emit('chat', 'final', tag({
    conversation_id: conversationId, message_id: `m-${Date.now()}`, answer, tool: 'rag', sources: fx.messages.c1[1].meta.sources.slice(0, 1),
    notices: [], trace: fx.messages.c1[1].meta.trace, rewritten_queries: [],
    evaluation: { sufficient: true, grounded: true, score: 5, missing: '' }, latency_ms: 2900,
  }), turnId);
}

// An upload is indexed in the background: library events move its chip along.
async function playIndexing(docId) {
  await wait(600);
  emit('library', 'document', { doc_id: docId, stage: 'reading' });
  await wait(900);
  emit('library', 'document', { doc_id: docId, stage: 'embedding', done: 4, total: 12 });
  await wait(900);
  emit('library', 'document', { doc_id: docId, stage: 'indexed' });
}

Object.assign(apiService, {
  checkHealth: async () => { if (state === 'offline') { const e = new Error('offline'); e.status = 0; throw e; } return { status: 'ok' }; },
  me: async () => { if (signedOut) { const e = new Error('Not signed in'); e.status = 401; throw e; } return fx.user; },
  authState: async () => {
    if (state === 'offline') { const e = new Error('offline'); e.status = 0; throw e; }
    return { user: signedOut ? null : fx.user, signups: 'invite' };
  },
  getQueue: async () => ({ batches: [] }),
  login: async () => fx.user,
  register: async () => fx.user,
  getConversations: async () => (empty ? [] : fx.conversations),
  getMessages: async (id) => fx.messages[id] || [],
  deleteConversation: async () => null,
  renameConversation: async (id, title) => ({ id, title }),
  getDocuments: async () => (empty ? [] : fx.documents),
  deleteDocument: async () => null,
  uploadDocument: async (file, { conversationId } = {}) => {
    await wait(1000);
    const id = `p-${Date.now()}`;
    playIndexing(id);
    return { status: 'processing', filename: file.name, chunks_indexed: 0, document: { id, status: 'processing', error: null, conversationId } };
  },
  getStats: async () => (empty ? fx.emptyStats : fx.stats),
  myUsage: async () => (empty ? fx.emptyUsage : fx.usage),
  // Same path as the app: events arrive on the (fake) socket tagged with the turn's id and followTurn reads them.
  async streamMessage(text, conversationId, handlers) {
    const turnId = newTurnId();
    const id = conversationId || 'c-preview';
    const turn = followTurn(realtime, turnId, handlers, async () => []);
    try {
      handlers.onMeta?.({ conversation_id: id, title: text.slice(0, 40) });
      const done = turn.follow(id);
      playTurn(turnId, id, text);
      await done;
    } finally {
      turn.stop();
    }
  },
});

const { default: App } = await import('../App.jsx');
ReactDOM.createRoot(document.getElementById('root')).render(<App />);
