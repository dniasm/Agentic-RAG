/** "Edit a passage" in the preview: the passage to replace (selected on the page, or typed here, so the keyboard
    reaches it too) and what it becomes. Saving goes through the same edit as the chat's (a PDF changed in place and
    checked; Undo takes it back). When the passage appears in several places it asks whether to change them all; when a
    PDF's look can't be kept it says why (letters its font lacks: reword without them) and asks before converting it
    to a Markdown copy. */
import React, { useEffect, useRef, useState } from 'react';
import { apiService } from '../services/api';

const MAX_CHARS = 20000;
const same = (a, b) => a.split(/\s+/).join(' ').trim() === b.split(/\s+/).join(' ').trim();

/** doc: { id, title, ext }. selected: the passage as selected ('' : typed here). onSaved(result), onCancel(),
    onDirtyChange(bool): whether there is unsaved text. */
export default function SelectionEditor({ doc, selected, onSaved, onCancel, onDirtyChange }) {
  const [find, setFind] = useState(selected);
  const [text, setText] = useState(selected);
  const [busy, setBusy] = useState(false);
  const [question, setQuestion] = useState(null);   // { kind: 'ambiguous' | 'convert', message, contexts?, missing? }
  const [error, setError] = useState(null);
  const findBox = useRef(null);
  const textBox = useRef(null);

  useEffect(() => {
    const first = selected ? textBox.current : findBox.current;
    first?.focus();
    first?.select();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const changed = find.trim() !== '' && !same(text, find);
  useEffect(() => { onDirtyChange?.(text !== selected || find !== selected); }, [text, find]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (options = {}) => {
    setBusy(true);
    setError(null);
    try {
      const result = await apiService.editDocument(doc.id, { find, text, ...options });
      onSaved(result);
    } catch (err) {
      if (err.status === 409 && err.data?.ambiguous) {
        setQuestion({ kind: 'ambiguous', message: err.data.message, contexts: err.data.contexts || [] });
      } else if (err.status === 409 && err.data?.needs_conversion) {
        setQuestion({ kind: 'convert', message: err.data.message, tooLong: err.data.too_long, missing: err.data.missing });
      } else {
        setError(err.message);
      }
    } finally {
      setBusy(false);
    }
  };

  const reset = () => setQuestion(null);
  const onKeyDown = (e) => {
    if (e.key === 'Escape') onCancel();
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && changed) save();
  };

  return (
    <section className="flex-none border-t border-line bg-surface px-4 py-3 flex flex-col gap-2 max-h-[55%] overflow-y-auto"
             aria-label="Edit a passage">
      <label className="text-small text-muted" htmlFor="selection-find">Replace</label>
      {selected ? (
        <blockquote id="selection-find" className="text-body border-l-2 border-control pl-3 max-h-24 overflow-y-auto whitespace-pre-wrap">
          {selected}
        </blockquote>
      ) : (
        <textarea
          id="selection-find" ref={findBox} className="input h-auto min-h-[60px] py-2 resize-y" value={find}
          maxLength={MAX_CHARS} disabled={busy} placeholder="The passage as it is now (or select it on the page)"
          onChange={(e) => { setFind(e.target.value); reset(); }} onKeyDown={onKeyDown}
        />
      )}
      <label className="text-small text-muted" htmlFor="selection-edit">With</label>
      <textarea
        id="selection-edit" ref={textBox} className="input h-auto min-h-[88px] py-2 resize-y" value={text}
        maxLength={MAX_CHARS} disabled={busy}
        onChange={(e) => { setText(e.target.value); reset(); }} onKeyDown={onKeyDown}
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
          {question.missing
            ? <>The PDF's font has no “{question.missing}”. Reword without {question.missing.length === 1 ? 'it' : 'them'} to keep the PDF as it is, or convert it: </>
            : <>{question.message}{' '}</>}
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
          <button type="button" className="btn-primary" disabled={busy || !changed} onClick={() => save()}>
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
