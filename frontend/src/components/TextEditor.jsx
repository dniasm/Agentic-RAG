/** The whole text of a TXT or Markdown document, to edit and save as one change (Undo puts the old text back).
    A save over a change made meanwhile (a chat, a queued task, another tab) is refused, never overwritten. */
import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { apiService } from '../services/api';

/** doc: { id, title, ext }. onSaved(result), onCancel(). */
export default function TextEditor({ doc, onSaved, onCancel }) {
  const [opened, setOpened] = useState(null);   // { text, sha256 } as loaded
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = async () => {
    setError(null);
    try {
      const data = await apiService.getDocumentText(doc.id);
      setOpened(data);
      setText(data.text);
    } catch (err) {
      setError({ message: err.message });
    }
  };
  useEffect(() => { load(); }, [doc.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      onSaved(await apiService.saveDocumentText(doc.id, text, opened.sha256));
    } catch (err) {
      setError({ message: err.message, changedMeanwhile: err.status === 409 });
    } finally {
      setBusy(false);
    }
  };

  if (!opened) {
    return (
      <div className="flex-1 min-h-0 p-6 text-body text-muted">
        {error ? <span role="alert">{error.message}</span> : <span className="inline-flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />Loading…</span>}
      </div>
    );
  }
  const changed = text !== opened.text;
  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <label className="sr-only" htmlFor="text-editor">Text of {doc.title}</label>
      <textarea
        id="text-editor" className="flex-1 min-h-0 w-full resize-none bg-surface text-ink p-4 font-mono text-body outline-none"
        value={text} spellCheck disabled={busy}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 's' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); if (changed) save(); }
        }}
      />
      <div className="flex-none flex flex-wrap items-center gap-2 border-t border-line px-4 py-3">
        <button type="button" className="btn-primary" disabled={busy || !changed} onClick={save}>
          {busy ? 'Saving…' : 'Save'}
        </button>
        <button type="button" className="btn-ghost" disabled={busy} onClick={onCancel}>{changed ? 'Discard' : 'Close'}</button>
        {error && (
          <span className="text-body text-[var(--pill-negative-ink)]" role="alert">
            {error.message}{' '}
            {error.changedMeanwhile && (
              <button type="button" className="link-action" onClick={load}>Reopen it (what you typed here is lost)</button>
            )}
          </span>
        )}
        {!error && <span className="text-small text-muted">{doc.ext === '.md' ? 'Markdown' : 'Plain text'} · Ctrl+S saves</span>}
      </div>
    </div>
  );
}
