import React, { useEffect, useRef, useState } from 'react';
import { ArrowRight, Loader2 } from 'lucide-react';
import InputBox from '../components/InputBox';
import Message from '../components/Message';
import { hrefFor } from '../lib/route';
import { NEW_KEY, clearNewChat, loadConversation, sendMessage, useConversation } from '../state/chatStore';

function suggestionsFor(documents) {
  const first = documents?.[0]?.filename;
  return [
    first ? `Summarise ${first}` : 'Explain how retrieval-augmented generation works',
    first && documents.length > 1
      ? `Compare what ${first} and ${documents[1].filename} say about their main topic`
      : 'What is the difference between BM25 and dense retrieval?',
    'What are the latest developments in open-source language models?',
  ];
}

function NewChat({ documents, onSuggestion }) {
  const count = documents?.length ?? 0;
  return (
    <div className="max-w-[760px] mx-auto w-full min-h-full flex flex-col justify-center py-10">
      <h2 className="text-title font-medium tracking-[-0.01em]">What would you like to know?</h2>
      <p className="mt-2 text-reading text-muted max-w-[620px]">
        {count > 0
          ? `Questions about your ${count} document${count === 1 ? '' : 's'} are answered from them, with page references. `
          : 'Upload documents and questions about them are answered from them, with page references. '}
        Anything recent goes to a web search, and the rest comes from the model's own knowledge. Each answer
        says which one it used.
      </p>

      <ul className="mt-8 panel" aria-label="Suggested questions">
        {suggestionsFor(documents).map((s) => (
          <li key={s} className="border-t border-rule first:border-t-0">
            <button
              type="button"
              onClick={() => onSuggestion(s)}
              className="w-full flex items-center gap-3 px-4 h-12 text-left text-reading hover:bg-ground transition-colors first:rounded-t-card last:rounded-b-card"
            >
              <span className="flex-1 truncate">{s}</span>
              <ArrowRight className="w-4 h-4 text-muted flex-none" aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>

      {count === 0 && (
        <p className="mt-4 text-body text-muted">
          No documents yet.{' '}
          <a href={hrefFor('documents')} className="font-medium text-ink underline-offset-4 hover:underline">Upload one</a>
          {' '}to ask questions about it.
        </p>
      )}
    </div>
  );
}

export default function Chat({ conversationId, title, documents, onConversationCreated, onAnswered, onDelete }) {
  const key = conversationId || NEW_KEY;
  const conv = useConversation(key);
  const scrollRef = useRef(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  useEffect(() => {
    if (conversationId) loadConversation(conversationId);
    else clearNewChat();
  }, [conversationId]);

  // Follow the answer as it is written, unless the reader has scrolled up to read something earlier.
  const lastText = conv.messages.at(-1);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160;
    if (nearBottom || lastText?.sender === 'user') el.scrollTop = el.scrollHeight;
  }, [conv.messages.length, lastText?.draft, lastText?.text]); // eslint-disable-line react-hooks/exhaustive-deps

  const send = (text) => sendMessage(text, conversationId, { onCreated: onConversationCreated, onDone: onAnswered });

  const empty = conv.messages.length === 0;

  return (
    <div className="h-full flex flex-col">
      {/* On a phone the top bar already names the chat, so this row is only read by screen readers there. */}
      <div className="sr-only lg:not-sr-only flex-none border-b border-line lg:px-8">
       <div className="max-w-[760px] mx-auto flex items-center gap-4 h-14">
        <h1 className="flex-1 truncate text-section font-medium">{title}</h1>
        {conversationId && !confirmingDelete && (
          // hidden on a phone: there a chat is deleted from the menu's chat list
          <button className="hidden lg:inline-flex btn-ghost h-8 -mr-3 text-muted hover:text-ink" onClick={() => setConfirmingDelete(true)}>
            Delete chat
          </button>
        )}
        {conversationId && confirmingDelete && (
          <div className="flex items-center gap-2 text-body">
            <span className="text-muted hidden sm:inline">Delete this chat and its messages?</span>
            <button className="btn-danger h-8" onClick={() => onDelete(conversationId)}>Delete</button>
            <button className="btn-ghost h-8" onClick={() => setConfirmingDelete(false)}>Cancel</button>
          </div>
        )}
       </div>
      </div>

      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-4 sm:px-6 lg:px-8">
        {conv.loading && empty && (
          <p className="max-w-[760px] mx-auto pt-10 flex items-center gap-2 text-body text-muted">
            <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> Loading the conversation…
          </p>
        )}
        {conv.error && (
          <p className="max-w-[760px] mx-auto mt-10 rounded-control px-3 py-2.5 text-body bg-[var(--pill-negative-bg)] text-[var(--pill-negative-ink)]">
            Couldn't load this conversation: {conv.error}{' '}
            <button className="font-medium underline underline-offset-4" onClick={() => loadConversation(conversationId)}>Retry</button>
          </p>
        )}
        {empty && !conv.loading && !conv.error && <NewChat documents={documents} onSuggestion={send} />}
        {!empty && (
          <div className="max-w-[760px] mx-auto flex flex-col gap-8 py-8">
            {conv.messages.map((m) => <Message key={m.id} msg={m} />)}
          </div>
        )}
      </div>

      <div className="flex-none px-4 sm:px-6 lg:px-8 pb-4 pt-2">
        <div className="max-w-[760px] mx-auto">
          <InputBox onSend={send} busy={conv.streaming} conversationKey={key} />
        </div>
      </div>
    </div>
  );
}
