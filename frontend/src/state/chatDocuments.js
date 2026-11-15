/** The documents a conversation renamed, edited or undid (GET /conversations/{id}/documents), fetched again
    whenever refreshKey changes: on each answer and each Undo, never on a timer. */
import { useEffect, useState } from 'react';
import { apiService } from '../services/api';

export function useChatDocuments(conversationId, refreshKey) {
  const [state, setState] = useState({ docs: conversationId ? null : [], error: null });
  useEffect(() => {
    if (!conversationId) { setState({ docs: [], error: null }); return undefined; }
    let cancelled = false;
    apiService.getConversationDocuments(conversationId)
      .then((docs) => { if (!cancelled) setState({ docs, error: null }); })
      .catch((err) => { if (!cancelled) setState((s) => ({ docs: s.docs, error: err.message })); });
    return () => { cancelled = true; };
  }, [conversationId, refreshKey]);
  return state;
}
