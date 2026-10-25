import React, { useEffect, useRef } from 'react';
import { Loader2, Database, Search, Bot, MessageCircle, UploadCloud } from 'lucide-react';
import Message from './Message';

const SUGGESTIONS = [
  { icon: Database, text: 'Summarize the key points of my documents' },
  { icon: Search, text: 'What are the top tech news headlines today?' },
  { icon: Bot, text: 'Explain how vector embeddings work' },
  { icon: MessageCircle, text: 'Hi! What can you do?' },
];

export default function ChatWindow({ messages, loadingHistory, onSuggestion, onGoToDocuments }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  if (loadingHistory) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-6 h-6 text-indigo-400 animate-spin" />
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="flex-1 overflow-y-auto p-6 flex flex-col items-center justify-center text-center">
        <h2 className="text-2xl font-bold text-white mb-2">How can I help?</h2>
        <p className="text-sm text-slate-400 max-w-lg mb-8">
          I route each question automatically: your uploaded documents (RAG), a live web search,
          my own knowledge, or just a chat. Vague questions get rewritten and retried until a good answer is found.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-2xl w-full">
          {SUGGESTIONS.map(({ icon: Icon, text }) => (
            <button
              key={text}
              onClick={() => onSuggestion(text)}
              className="text-left bg-slate-900 border border-slate-800 hover:border-indigo-500/50 rounded-xl p-4 text-sm text-slate-300 transition flex items-start space-x-3"
            >
              <Icon className="w-4 h-4 text-indigo-400 mt-0.5 flex-shrink-0" />
              <span>{text}</span>
            </button>
          ))}
        </div>
        <button
          onClick={onGoToDocuments}
          className="mt-6 text-xs text-slate-500 hover:text-indigo-400 inline-flex items-center space-x-1"
        >
          <UploadCloud className="w-3.5 h-3.5" />
          <span>Upload documents to the knowledge base</span>
        </button>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      {messages.map((msg) => (
        <Message key={msg.id} msg={msg} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
