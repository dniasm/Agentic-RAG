// Dev: Vite proxies /api to the backend (see vite.config.js). Set VITE_API_URL to call it directly.
const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';
const TOKEN_KEY = 'agentic_rag_token';

export const auth = {
  getToken() {
    try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
  },
  setToken(token) {
    try { localStorage.setItem(TOKEN_KEY, token); } catch { /* storage unavailable */ }
  },
  clear() {
    try { localStorage.removeItem(TOKEN_KEY); } catch { /* storage unavailable */ }
  },
};

// Called when the backend rejects our token so the app can drop back to the login screen.
let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn; };

async function request(path, { method = 'GET', body, headers = {}, raw = false } = {}) {
  const token = auth.getToken();
  const isForm = body instanceof FormData;
  let res;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
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

  if (res.status === 401 && token) {
    auth.clear();
    onUnauthorized();
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const data = await res.json();
      if (typeof data.detail === 'string') detail = data.detail;
      else if (Array.isArray(data.detail)) detail = data.detail.map((d) => d.msg).join(', ');
    } catch { /* non-JSON error body */ }
    const err = new Error(detail);
    err.status = res.status; // callers can tell "bad token" (401) from "backend down" (503)
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

  // ---------- auth
  async login(username, password) {
    const data = await request('/auth/login', { method: 'POST', body: { username, password } });
    auth.setToken(data.access_token);
    return data.user;
  },
  async register(username, password) {
    const data = await request('/auth/register', { method: 'POST', body: { username, password } });
    auth.setToken(data.access_token);
    return data.user;
  },
  async me() {
    return request('/auth/me');
  },
  logout() {
    auth.clear();
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

  // ---------- stats
  async getStats() {
    return request('/stats');
  },
};
