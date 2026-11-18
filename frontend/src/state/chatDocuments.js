/** The documents a conversation renamed, edited or undid (GET /conversations/{id}/documents), fetched again
    whenever refreshKey changes: on each answer and each Undo. While tasks the chat queued are still to run
    (GET /conversations/{id}/pending), it looks again every POLL_MS so their changes show as they finish, and stops
    once none are left. */
import { useEffect, useState } from 'react';
import { apiService } from '../services/api';

const POLL_MS = 5000;

export function useChatDocuments(conversationId, refreshKey) {
  const [state, setState] = useState({ docs: conversationId ? null : [], error: null });
  useEffect(() => {
    if (!conversationId) { setState({ docs: [], error: null }); return undefined; }
    let cancelled = false;
    let timer = null;
    const load = async () => {
      try {
        const [docs, pending] = await Promise.all([
          apiService.getConversationDocuments(conversationId),
          apiService.getConversationPending(conversationId).catch(() => ({ tasks: 0 })),
        ]);
        if (cancelled) return;
        setState({ docs, error: null });
        if (pending.tasks > 0) timer = setTimeout(load, POLL_MS);
      } catch (err) {
        if (!cancelled) setState((s) => ({ docs: s.docs, error: err.message }));
      }
    };
    load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [conversationId, refreshKey]);
  return state;
}
