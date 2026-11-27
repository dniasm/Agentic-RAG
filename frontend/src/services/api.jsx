import { followTurn, newTurnId } from '../lib/turn';
import { API_BASE_URL } from './config';
import { realtime } from './realtime';

export { API_BASE_URL };

// Logins used to keep a token in localStorage; the cookie replaced it. Drop the old one once.
try { localStorage.removeItem('agentic_rag_token'); } catch { /* storage unavailable */ }
// The login is an HttpOnly cookie the browser sends by itself (scripts can't read it). Called when the backend
// answers 401, so the app can drop back to the login screen.
let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn; };

async function request(path, { method = 'GET', body, headers = {} } = {}) {
  const isForm = body instanceof FormData;
  let res;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method,
      credentials: 'include', // the login cookie, also when VITE_API_URL points at another origin
      headers: {
        ...(body && !isForm ? { 'Content-Type': 'application/json' } : {}),
        ...headers,
      },
      body: body ? (isForm ? body : JSON.stringify(body)) : undefined,
    });
  } catch {
    // Network-level failure (nothing answered at all). status 0 = "unreachable".
    const err = new Error('Cannot reach the backend server.');
    err.status = 0;
    throw err;
  }

  if (res.status === 401 && !path.startsWith('/auth/')) onUnauthorized();
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    let data = null;
    try {
      const body = await res.json();
      if (typeof body.detail === 'string') detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d) => d.msg).join(', ');
      else if (body.detail && typeof body.detail === 'object') {   // { message, ...what the caller can act on }
        data = body.detail;
        if (typeof data.message === 'string') detail = data.message;
      }
    } catch { /* non-JSON error body */ }
    const err = new Error(detail);
    err.status = res.status; // callers can tell "not logged in" (401) from "backend down" (503)
    err.data = data;         // e.g. a rename's name conflict: { conflicts, title }
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

export const apiService = {
  // ---------- health
  async checkHealth() {
    return request('/health');
  },

  // ---------- auth (the cookie is set and cleared by the server)
  async authState() {
    return request('/auth/state'); // { user | null, signups: 'first' | 'invite' | 'closed' }
  },
  async login(username, password) {
    return (await request('/auth/login', { method: 'POST', body: { username, password } })).user;
  },
  async register(username, password, invite = '') {
    return (await request('/auth/signup', { method: 'POST', body: { username, password, invite } })).user;
  },
  async me() {
    return request('/auth/me');
  },

  // ---------- admin (accounts in ADMIN_USERNAMES; 404 for everyone else)
  async adminUsage() {
    return request('/admin/usage');
  },
  async logout() {
    try { await request('/auth/logout', { method: 'POST' }); } catch { /* logged out locally either way */ }
  },

  // ---------- chat
  async sendMessage(message, conversationId = null) {
    return request('/chat', { method: 'POST', body: { message, conversation_id: conversationId } });
  },

  /**
   * Start an answer (POST, 202) and follow it over the WebSocket (lib/turn.js). Handlers:
   *   onMeta({conversation_id, title})  conversation registered
   *   onStatus({node, label})           a step of the agent has started
   *   onStep({node, detail, data})      a trace step, as soon as it happens
   *   onDraft({node, text})             the answer so far, while it is being written
   *   onFinal(response)                 the finished answer
   */
  async streamMessage(message, conversationId, handlers) {
    // The answer's events come over the WebSocket, tagged with this turn's id; the POST only starts it (202).
    // Wait briefly for the socket first so no early event is missed (a replay on reconnect covers the rest).
    const turnId = newTurnId();
    await realtime.ready();
    const turn = followTurn(realtime, turnId, handlers, (id) => request(`/conversations/${id}/messages`));
    try {
      const started = await request('/chat/stream', {
        method: 'POST',
        body: { message, conversation_id: conversationId, turn_id: turnId },
      });
      handlers.onMeta?.({ conversation_id: started.conversation_id, title: started.title });
      await turn.follow(started.conversation_id);
    } finally {
      turn.stop();
    }
  },

  // ---------- conversations
  async getConversations() {
    return request('/conversations');
  },
  async getMessages(conversationId) {
    return request(`/conversations/${conversationId}/messages`);
  },
  async renameConversation(conversationId, title) {
    return request(`/conversations/${conversationId}`, { method: 'PATCH', body: { title } });
  },
  async deleteConversation(conversationId) {
    return request(`/conversations/${conversationId}`, { method: 'DELETE' });
  },

  // ---------- documents
  async getDocuments() {
    return request('/documents');
  },
  async uploadDocument(file) {
    const form = new FormData();
    form.append('file', file);
    return request('/documents/upload', { method: 'POST', body: form });
  },
  async deleteDocument(docId) {
    return request(`/documents/${docId}`, { method: 'DELETE' });
  },
  /** Rename a document. A name another document has is refused (409, err.data.conflicts) unless confirmed. */
  async renameDocument(docId, title, confirmed = false) {
    return request(`/library/documents/${docId}/rename`, { method: 'POST', body: { title, confirmed } });
  },
  /** Replace a passage selected in the preview. 409: err.data.ambiguous (select more, or replaceAll) or
      err.data.needs_conversion (the PDF's look can't be kept: convertPdf after the user agrees). */
  async editDocument(docId, { find, text, replaceAll = false, convertPdf = false }) {
    return request(`/library/documents/${docId}/edit`, {
      method: 'POST', body: { find, text, replace_all: replaceAll, convert_pdf: convertPdf },
    });
  },
  /** A TXT / MD document's whole text for the editor: { text, sha256 }. */
  async getDocumentText(docId) {
    return request(`/library/documents/${docId}/text`);
  },
  /** Save the editor's text; 409 when the document changed since it was opened (sha256). */
  async saveDocumentText(docId, text, sha256) {
    return request(`/library/documents/${docId}/text`, { method: 'PUT', body: { text, sha256 } });
  },
  /** Batches of queued tasks still running or queued in the last hour: { batches: [{ id, created_at, tasks }] }. */
  async getQueue() {
    return request('/library/queue');
  },
  async cancelBatch(batchId) {
    return request(`/library/batches/${batchId}`, { method: 'DELETE' });
  },
  /** Take back a document's latest edit (the Undo under an edit in a chat). */
  async undoEdit(docId, editId) {   // with editId, refused (409) unless that edit is still the latest
    return request(`/library/documents/${docId}/undo`, { method: 'POST', body: { edit_id: editId ?? null } });
  },
  /** Bring back the edit undone most recently (the Redo under an undone edit); with editId, only that one. */
  async redoEdit(docId, editId) {
    return request(`/library/documents/${docId}/redo`, { method: 'POST', body: { edit_id: editId ?? null } });
  },
  /** The documents a conversation renamed, edited or undid, newest first. */
  async getConversationDocuments(conversationId) {
    return request(`/conversations/${conversationId}/documents`);
  },
  /** How many tasks the conversation queued are still to run: { tasks }. */
  async getConversationPending(conversationId) {
    return request(`/conversations/${conversationId}/pending`);
  },

  // ---------- stats
  async getStats() {
    return request('/stats');
  },

  async myUsage() {
    return request('/usage/me');
  },
};
