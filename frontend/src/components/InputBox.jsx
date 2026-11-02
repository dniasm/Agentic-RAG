import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowUp, Loader2 } from 'lucide-react';

// Drafts are kept per conversation for this browser tab, so switching pages or chats doesn't throw away
// what you were typing.
const draftKey = (key) => `agentic_rag_draft:${key}`;
const readDraft = (key) => {
  try { return sessionStorage.getItem(draftKey(key)) || ''; } catch { return ''; }
};
const writeDraft = (key, value) => {
  try {
    if (value) sessionStorage.setItem(draftKey(key), value);
    else sessionStorage.removeItem(draftKey(key));
  } catch { /* storage unavailable: the draft just won't persist */ }
};

const MAX_HEIGHT = 200;

export default function InputBox({ onSend, busy, conversationKey = 'new', placeholder = 'Ask a question' }) {
  const [text, setText] = useState(() => readDraft(conversationKey));
  const ref = useRef(null);

  useEffect(() => {
    setText(readDraft(conversationKey));
    ref.current?.focus();
  }, [conversationKey]);

  // When an answer finishes, the cursor goes back to the box so the next question can be typed straight away.
  useEffect(() => {
    if (!busy) ref.current?.focus();
  }, [busy]);

  // Grow with the text up to MAX_HEIGHT, then scroll inside.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [text]);

  const update = (value) => {
    setText(value);
    writeDraft(conversationKey, value);
  };

  const submit = () => {
    if (!text.trim() || busy) return;
    onSend(text.trim());
    update('');
  };

  const insertNewline = (el) => {
    const { selectionStart: start, selectionEnd: end } = el;
    const next = `${text.slice(0, start)}\n${text.slice(end)}`;
    update(next);
    requestAnimationFrame(() => { el.selectionStart = el.selectionEnd = start + 1; });
  };

  // Enter sends. Shift+Enter and Ctrl+Enter start a new line (Shift+Enter is the textarea's own behaviour).
  const onKeyDown = (e) => {
    if (e.key !== 'Enter' || e.nativeEvent.isComposing) return;
    if (e.shiftKey) return;
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) insertNewline(e.currentTarget);
    else submit();
  };

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); submit(); }}
      className="panel flex flex-col focus-within:border-control transition-colors"
    >
      <label htmlFor="composer" className="sr-only">Your question</label>
      <textarea
        id="composer"
        ref={ref}
        rows={1}
        value={text}
        onChange={(e) => update(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        aria-describedby="composer-keys"
        className="w-full resize-none bg-transparent px-4 pt-3.5 pb-1 text-reading text-ink placeholder:text-muted focus:outline-none"
        style={{ maxHeight: MAX_HEIGHT }}
      />
      <div className="flex items-center gap-3 px-3 pb-3 pt-1">
        <p id="composer-keys" className="flex-1 pl-1 text-small text-muted hidden sm:block">
          Enter to send · Shift+Enter or Ctrl+Enter for a new line
        </p>
        <span className="flex-1 sm:hidden" />
        <button type="submit" disabled={!text.trim() || busy} className="btn-primary h-8 px-3">
          {busy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <ArrowUp className="w-4 h-4" aria-hidden="true" />}
          {busy ? 'Answering' : 'Send'}
        </button>
      </div>
    </form>
  );
}
