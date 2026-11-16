/** Rename a document in place: the link opens a field with the current name; Enter saves, Escape cancels. A name
    another document already has asks first ("Use it anyway?"). */
import React, { useEffect, useRef, useState } from 'react';
import { apiService } from '../services/api';

/** doc: { id, title }. onRenamed(result) after a rename went through; onError(message) when it didn't. */
export default function RenameAction({ doc, onRenamed, onError }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(doc.title);
  const [conflict, setConflict] = useState(null);   // the other documents with this name, until confirmed
  const [busy, setBusy] = useState(false);
  const input = useRef(null);

  useEffect(() => { if (editing) input.current?.select(); }, [editing]);

  const close = () => { setEditing(false); setConflict(null); setTitle(doc.title); };

  const save = async (confirmed = false) => {
    const wanted = title.trim();
    if (!wanted || wanted === doc.title) return close();
    setBusy(true);
    try {
      const result = await apiService.renameDocument(doc.id, wanted, confirmed);
      setEditing(false);
      setConflict(null);
      onRenamed?.(result);
    } catch (err) {
      if (err.status === 409 && err.data?.conflicts) setConflict(err.data.conflicts);
      else onError?.(`${doc.title}: ${err.message}`);
    } finally {
      setBusy(false);
    }
  };

  if (!editing) {
    return (
      <button type="button" className="link-action" onClick={() => { setTitle(doc.title); setEditing(true); }}>
        Rename
      </button>
    );
  }
  return (
    <form
      className="inline-flex flex-wrap items-center justify-end gap-2"
      onSubmit={(e) => { e.preventDefault(); save(Boolean(conflict)); }}
    >
      <label className="sr-only" htmlFor={`rename-${doc.id}`}>New name for {doc.title}</label>
      <input
        id={`rename-${doc.id}`} ref={input} className="input h-8 w-56" value={title} disabled={busy}
        onChange={(e) => { setTitle(e.target.value); setConflict(null); }}
        onKeyDown={(e) => { if (e.key === 'Escape') close(); }}
      />
      {conflict && <span className="text-small text-warning">Another document has this name. Use it anyway?</span>}
      <button type="submit" className="link-action disabled:opacity-50" disabled={busy}>
        {busy ? 'Saving…' : conflict ? 'Yes, use it' : 'Save'}
      </button>
      <button type="button" className="text-muted hover:text-ink" onClick={close} disabled={busy}>Cancel</button>
    </form>
  );
}
