// src/components/InputBox.jsx
import React, { useEffect, useRef, useState } from 'react';
import { Send } from 'lucide-react';

// Drafts are kept per conversation for this browser tab, so switching pages or chats
// doesn't throw away what you were typing.
const draftKey = (key) => `agentic_rag_draft:${key}`;
const readDraft = (key) => {
  try { return sessionStorage.getItem(draftKey(key)) || ''; } catch { return ''; }
};
const writeDraft = (key, value) => {
  try {
    if (value) sessionStorage.setItem(draftKey(key), value);
    else sessionStorage.removeItem(draftKey(key));
  } catch { /* storage unavailable: draft just won't persist */ }
};

export default function InputBox({ onSend, busy, conversationKey = 'new' }) {
  const [text, setText] = useState(() => readDraft(conversationKey));
  const inputRef = useRef(null);

  // Switching conversations loads that conversation's draft and puts the cursor back in the box.
  useEffect(() => {
    setText(readDraft(conversationKey));
    inputRef.current?.focus();
  }, [conversationKey]);

  // When an answer finishes, return focus to the box so you can keep typing.
  useEffect(() => {
    if (!busy) inputRef.current?.focus();
  }, [busy]);

  const handleChange = (e) => {
    setText(e.target.value);
    writeDraft(conversationKey, e.target.value);
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!text.trim() || busy) return;
    onSend(text);
    setText('');
    writeDraft(conversationKey, '');
    inputRef.current?.focus();
  };

  return (
    <form onSubmit={handleSubmit} className="relative flex items-center">
      {/* The box stays editable while the agent works (you can type your next question);
          only sending waits. A disabled input would lose keyboard focus. */}
      <input
        ref={inputRef}
        type="text"
        value={text}
        onChange={handleChange}
        autoFocus
        placeholder={busy ? 'Agent is working… you can type your next question' : 'Ask anything...'}
        className="w-full bg-slate-900 border border-slate-800 focus:border-indigo-500 rounded-xl py-3 pl-4 pr-12 text-sm text-slate-100 placeholder-slate-500 focus:outline-none transition shadow-inner"
      />
      <button
        type="submit"
        disabled={!text.trim() || busy}
        className="absolute right-2 p-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition disabled:opacity-30"
      >
        <Send className="w-4 h-4" />
      </button>
    </form>
  );
}
