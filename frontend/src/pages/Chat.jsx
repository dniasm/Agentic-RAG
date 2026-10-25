import React, { useEffect, useRef, useState } from 'react';
import ChatWindow from '../components/ChatWindow';
import InputBox from '../components/InputBox';
import { apiService } from '../services/api';

const fromApi = (m) => ({
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
});

export default function Chat({ conversationId, onConversationCreated, onMessageSent, onGoToDocuments }) {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  // Set when this component created the conversation mid-stream, so we don't reload and wipe the live messages.
  const createdHereRef = useRef(null);

  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      return;
    }
    if (createdHereRef.current === conversationId) return;

    let cancelled = false;
    setLoadingHistory(true);
    apiService.getMessages(conversationId)
      .then((msgs) => { if (!cancelled) setMessages(msgs.map(fromApi)); })
      .catch(() => { if (!cancelled) setMessages([]); })
      .finally(() => { if (!cancelled) setLoadingHistory(false); });
    return () => { cancelled = true; };
  }, [conversationId]);

  const updatePending = (fn) =>
    setMessages((prev) => prev.map((m) => (m.pending ? fn(m) : m)));

  const handleSend = async (text) => {
    setMessages((prev) => [
      ...prev,
      { id: `u-${Date.now()}`, sender: 'user', text },
      { id: `a-${Date.now()}`, sender: 'agent', pending: true, steps: [], status: null, draft: '' },
    ]);
    setLoading(true);

    try {
      await apiService.streamMessage(text, conversationId, {
        onMeta: ({ conversation_id }) => {
          if (!conversationId) {
            createdHereRef.current = conversation_id;
            onConversationCreated(conversation_id);
          }
        },
        onStatus: (status) => updatePending((m) => ({
          ...m,
          status: status.label,
          // A new answer-writing step starts: the previous draft (if any) was rejected and is being redone.
          draft: ['generate_rag', 'generate_web', 'direct', 'greeting'].includes(status.node) ? '' : m.draft,
        })),
        onStep: (step) => updatePending((m) => ({ ...m, steps: [...m.steps, step] })),
        onDraft: ({ text }) => updatePending((m) => ({ ...m, draft: text })),
        onFinal: (data) =>
          updatePending(() => ({
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
      onMessageSent?.();
    } catch (err) {
      updatePending(() => ({
        id: `e-${Date.now()}`,
        sender: 'agent',
        text: `Error: ${err.message === 'Failed to fetch' ? 'Could not connect to API server.' : err.message}`,
        error: true,
      }));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-slate-950 overflow-hidden">
      <ChatWindow
        messages={messages}
        loadingHistory={loadingHistory}
        onSuggestion={handleSend}
        onGoToDocuments={onGoToDocuments}
      />

      <div className="p-4 border-t border-slate-900 bg-slate-950/80 backdrop-blur">
        <InputBox onSend={handleSend} busy={loading} conversationKey={conversationId || 'new'} />
      </div>
    </div>
  );
}
