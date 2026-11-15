/** Under an answer from the document agent: one row for each thing it did, with Preview and (for the latest edit
    of a document in this chat) Undo. */
import React, { useState } from 'react';
import { describeAction, LAYOUT_NOTE } from '../lib/chatActions';
import { openPreview } from '../state/previewStore';

function UndoButton({ action, onUndo }) {
  const [state, setState] = useState('idle');   // idle | busy | failed
  return (
    <button type="button" className="link-action disabled:opacity-50" disabled={state === 'busy'}
            title={state === 'failed' ? 'Undo failed: see the message above' : undefined}
            onClick={async () => {
              setState('busy');
              try { await onUndo(action); setState('idle'); } catch { setState('failed'); }
            }}>
      {state === 'busy' ? 'Undoing…' : 'Undo'}
    </button>
  );
}

/** actions: meta.actions of one answer. undoable: edit ids that can still be undone. onUndo(action) -> promise. */
export default function DocumentActions({ actions, undoable, onUndo }) {
  const shown = actions.filter((a) => a.type !== 'queue' || a.tasks);
  if (!shown.length) return null;
  return (
    <ul className="mt-3 flex flex-col gap-1.5" aria-label="What was done">
      {shown.map((a, i) => (
        <li key={i} className="panel px-3 py-2 text-body flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="min-w-0 flex-1">
            {a.type === 'rename' ? (
              <>Renamed <span className="text-muted line-through decoration-control">{a.old_title}</span> → <span className="font-medium">{a.new_title}</span></>
            ) : (
              describeAction(a)
            )}
            {a.type === 'edit' && LAYOUT_NOTE[a.layout] && (
              <span className="block text-small text-warning">{LAYOUT_NOTE[a.layout]}</span>
            )}
          </span>
          {a.doc_id && (
            <span className="inline-flex items-center gap-3">
              <button type="button" className="link-action"
                      onClick={() => openPreview({ id: a.doc_id, title: a.new_title || a.title, ext: a.ext })}>
                Preview
              </button>
              {a.type === 'edit' && undoable.has(a.edit_id) && <UndoButton action={a} onUndo={onUndo} />}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
