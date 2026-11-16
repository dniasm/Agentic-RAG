/** "Edit selection" in the preview: the passage selected on the page, and a box for what it becomes. Saving goes
    through the same edit as the chat's (a PDF changed in place and checked; Undo takes it back). When the passage
    appears in several places it asks whether to change them all; when a PDF's look can't be kept it asks before
    converting it to a Markdown copy. */
import React, { useEffect, useRef, useState } from 'react';
import { apiService } from '../services/api';

const MAX_CHARS = 20000;

/** doc: { id, title, ext }. selected: the passage as selected. onSaved(result), onCancel(). */
export default function SelectionEditor({ doc, selected, onSaved, onCancel }) {
  const [text, setText] = useState(selected);
  const [busy, setBusy] = useState(false);
  const [question, setQuestion] = useState(null);   // { kind: 'ambiguous' | 'convert', message, contexts? }
  const [error, setError] = useState(null);
  const box = useRef(null);

  useEffect(() => { box.current?.focus(); box.current?.select(); }, []);

  const save = async (options = {}) => {
    setBusy(true);
    setError(null);
    try {
      const result = await apiService.editDocument(doc.id, { find: selected, text, ...options });
      onSaved(result);
    } catch (err) {
      if (err.status === 409 && err.data?.ambiguous) {
        setQuestion({ kind: 'ambiguous', message: err.data.message, contexts: err.data.contexts || [] });
      } else if (err.status === 409 && err.data?.needs_conversion) {
        setQuestion({ kind: 'convert', message: err.data.message, tooLong: err.data.too_long });
      } else {
        setError(err.message);
      }
    } finally {
      setBusy(false);
    }
  };

  const unchanged = text.split(/\s+/).join(' ').trim() === selected.split(/\s+/).join(' ').trim();

  return (
    <section className="flex-none border-t border-line bg-surface px-4 py-3 flex flex-col gap-2 max-h-[55%] overflow-y-auto"
             aria-label="Edit the selected text">
      <div className="text-small text-muted">Replace</div>
      <blockquote className="text-body border-l-2 border-control pl-3 max-h-24 overflow-y-auto whitespace-pre-wrap">
        {selected}
      </blockquote>
      <label className="text-small text-muted" htmlFor="selection-edit">With</label>
      <textarea
        id="selection-edit" ref={box} className="input h-auto min-h-[88px] py-2 resize-y" value={text}
        maxLength={MAX_CHARS} disabled={busy}
        onChange={(e) => { setText(e.target.value); setQuestion(null); }}
        onKeyDown={(e) => {
          if (e.key === 'Escape') onCancel();
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !unchanged) save();
        }}
      />

      {question?.kind === 'ambiguous' && (
        <div className="text-body" role="alert">
          <p>{question.message} Select more of the text around it to pick one, or change them all:</p>
          <ul className="mt-1 text-small text-muted list-disc pl-5">
            {question.contexts.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      )}
      {question?.kind === 'convert' && (
        <p className="text-body" role="alert">
          {question.message}{' '}
          {question.tooLong
            ? 'A shorter wording may fit.'
            : 'The PDF can be converted to a Markdown copy and edited there instead (its layout and images are lost; Undo brings the PDF back).'}
        </p>
      )}
      {error && <p className="text-body text-[var(--pill-negative-ink)]" role="alert">{error}</p>}

      <div className="flex flex-wrap items-center gap-2">
        {question?.kind === 'ambiguous' ? (
          <button type="button" className="btn-primary" disabled={busy} onClick={() => save({ replaceAll: true })}>
            {busy ? 'Saving…' : 'Change all of them'}
          </button>
        ) : question?.kind === 'convert' && !question.tooLong ? (
          <button type="button" className="btn-primary" disabled={busy} onClick={() => save({ convertPdf: true })}>
            {busy ? 'Converting…' : 'Convert and edit'}
          </button>
        ) : (
          <button type="button" className="btn-primary" disabled={busy || unchanged} onClick={() => save()}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        )}
        <button type="button" className="btn-ghost" disabled={busy} onClick={onCancel}>Cancel</button>
        {doc.ext === '.pdf' && !question && (
          <span className="text-small text-muted">The PDF is changed in place, in its own fonts. It can take a few seconds.</span>
        )}
      </div>
    </section>
  );
}
