// Dev: Vite proxies /api to the backend (see vite.config.js). Set VITE_API_URL to call it directly.
export const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';

// Logins used to keep a token in localStorage; the cookie replaced it. Drop the old one once.
try { localStorage.removeItem('agentic_rag_token'); } catch { /* storage unavailable */ }
// The login is an HttpOnly cookie the browser sends by itself (scripts can't read it). Called when the backend
// answers 401, so the app can drop back to the login screen.
let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn; };

async function request(path, { method = 'GET', body, headers = {}, raw = false } = {}) {
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
  if (raw) return res;
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
  async logout() {
    try { await request('/auth/logout', { method: 'POST' }); } catch { /* logged out locally either way */ }
  },

  // ---------- chat
  async sendMessage(message, conversationId = null) {
    return request('/chat', { method: 'POST', body: { message, conversation_id: conversationId } });
  },

  /**
   * Stream the agent run over SSE. Handlers:
   *   onMeta({conversation_id, title})  conversation registered
   *   onStatus({node, label})           a step of the agent has started
   *   onStep({node, detail, data})      a trace step, as soon as it happens
   *   onDraft({node, text})             the answer so far, while it is being written
   *   onFinal(response)                 the finished answer
   */
  async streamMessage(message, conversationId, { onMeta, onStatus, onStep, onDraft, onFinal }) {
    const res = await request('/chat/stream', {
      method: 'POST',
      body: { message, conversation_id: conversationId },
      raw: true,
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    const dispatch = (chunk) => {
      let event = 'message';
      let data = '';
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      if (!data) return;
      const payload = JSON.parse(data);
      if (event === 'meta') onMeta?.(payload);
      else if (event === 'status') onStatus?.(payload);
      else if (event === 'step') onStep?.(payload);
      else if (event === 'draft') onDraft?.(payload);
      else if (event === 'final') onFinal?.(payload);
    };

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        dispatch(buffer.slice(0, idx));
        buffer = buffer.slice(idx + 2);
      }
    }
    if (buffer.trim()) dispatch(buffer);
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
};
