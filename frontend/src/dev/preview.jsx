// Design preview: the real app with the API replaced by sample data, so every screen can be opened and
// inspected without a backend or a login. Dev server only (vite build ships index.html alone).
//   /preview.html#/chat/c1 · #/chat · #/documents · #/overview
//   ?state=empty (no documents, no usage) · ?state=loggedout · ?state=offline (login, server down)
//   ?theme=dark or ?theme=light
import React from 'react';
import ReactDOM from 'react-dom/client';
import '../index.css';
import { apiService, auth } from '../services/api';
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

auth.setToken(state === 'loggedout' || state === 'offline' ? '' : 'preview');
if (state === 'loggedout' || state === 'offline') auth.clear();

Object.assign(apiService, {
  checkHealth: async () => { if (state === 'offline') { const e = new Error('offline'); e.status = 0; throw e; } return { status: 'ok' }; },
  me: async () => fx.user,
  login: async () => fx.user,
  register: async () => fx.user,
  getConversations: async () => (empty ? [] : fx.conversations),
  getMessages: async (id) => fx.messages[id] || [],
  deleteConversation: async () => null,
  getDocuments: async () => (empty ? [] : fx.documents),
  deleteDocument: async () => null,
  uploadDocument: async (file) => { await wait(1500); return { status: 'success', filename: file.name, chunks_indexed: 42 }; },
  getStats: async () => (empty ? fx.emptyStats : fx.stats),
  async streamMessage(text, conversationId, { onMeta, onStatus, onStep, onDraft, onFinal }) {
    onMeta?.({ conversation_id: conversationId || 'c-preview', title: text.slice(0, 40) });
    const steps = [
      ['router', 'Reading your message…', 'Information request about the uploaded documents.'],
      ['retrieve', 'Searching your documents…', 'Attempt 1: hybrid search; 1 query, 20 candidate chunk(s).'],
      ['grade', 'Checking which passages are relevant…', 'Reranked 20 candidates locally: best relevance 0.74 -> strong match.'],
    ];
    for (const [node, label, detail] of steps) {
      onStatus?.({ node, label });
      await wait(500);
      onStep?.({ id: node, node: node === 'grade' ? 'grade_documents' : node, detail, data: {} });
    }
    onStatus?.({ node: 'generate_rag', label: 'Writing the answer from your documents…' });
    const answer = 'This is a preview answer written from sample data. In the app, the answer streams in here while it is written, and its sources and steps appear below it when it is finished [1].';
    for (let i = 20; i <= answer.length; i += 20) { onDraft?.({ node: 'generate_rag', text: answer.slice(0, i) }); await wait(60); }
    onFinal?.({
      message_id: `m-${Date.now()}`, answer, tool: 'rag', sources: fx.messages.c1[1].meta.sources.slice(0, 1),
      notices: [], trace: fx.messages.c1[1].meta.trace, rewritten_queries: [],
      evaluation: { sufficient: true, grounded: true, score: 5, missing: '' }, latency_ms: 2900,
    });
  },
});

const { default: App } = await import('../App.jsx');
ReactDOM.createRoot(document.getElementById('root')).render(<App />);
