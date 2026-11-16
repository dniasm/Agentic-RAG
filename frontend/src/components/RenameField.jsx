/** Rename a document in place: a field with the current name; Enter saves, Escape cancels. A name another document
    already has asks first ("Use it anyway?"). Shown by its row when Rename is chosen from the row's menu. */
import React, { useEffect, useRef, useState } from 'react';
import { apiService } from '../services/api';

/** doc: { id, title }. onRenamed(result) after a rename went through; onError(message) when it didn't;
    onClose() when the field is done (saved, cancelled, or nothing changed). */
export default function RenameField({ doc, onRenamed, onError, onClose }) {
  const [title, setTitle] = useState(doc.title);
  const [conflict, setConflict] = useState(false);   // another document has this name, until confirmed
  const [busy, setBusy] = useState(false);
  const input = useRef(null);

  useEffect(() => { input.current?.select(); }, []);

  const save = async () => {
    const wanted = title.trim();
    if (!wanted || wanted === doc.title) return onClose();
    setBusy(true);
    try {
      const result = await apiService.renameDocument(doc.id, wanted, conflict);
      onClose();
      onRenamed?.(result);
    } catch (err) {
      if (err.status === 409 && err.data?.conflicts) setConflict(true);
      else { onError?.(`${doc.title}: ${err.message}`); onClose(); }
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); save(); }}>
      <label className="sr-only" htmlFor={`rename-${doc.id}`}>New name for {doc.title}</label>
      <input
        id={`rename-${doc.id}`} ref={input} className="input h-8 flex-1 min-w-[160px]" value={title} disabled={busy}
        onChange={(e) => { setTitle(e.target.value); setConflict(false); }}
        onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }}
      />
      <button type="submit" className="link-action disabled:opacity-50" disabled={busy}>
        {busy ? 'Saving…' : conflict ? 'Yes, use it' : 'Save'}
      </button>
      <button type="button" className="text-muted hover:text-ink" onClick={onClose} disabled={busy}>Cancel</button>
      {conflict && <span className="basis-full text-small text-warning">Another document has this name. Use it anyway?</span>}
    </form>
  );
}
