/** Under an answer from the document agent: one row for each thing it did, with Preview, Undo (the latest edit of
    a document in this chat) and Redo (the edit undone most recently), each asking first. Like a browser's Back
    and Forward: a new edit ends what Redo can bring back. */
import React from 'react';
import { describeAction, LAYOUT_NOTE } from '../lib/chatActions';
import { openPreview } from '../state/previewStore';
import ConfirmAction from './ConfirmAction';

/** actions: meta.actions of one answer. undoable / redoable: edit ids an Undo / a Redo applies to.
    onUndo(action), onRedo(action) -> promise. */
export default function DocumentActions({ actions, undoable, redoable = new Set(), onUndo, onRedo }) {
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
              {['edit', 'redo'].includes(a.type) && undoable.has(a.edit_id) && (
                <ConfirmAction label="Undo" question="Undo this edit?" busyLabel="Undoing…"
                               onConfirm={() => onUndo(a)} />
              )}
              {['edit', 'undo'].includes(a.type) && onRedo && redoable.has(a.edit_id) && (
                <ConfirmAction label="Redo" question="Bring this edit back?" busyLabel="Redoing…"
                               onConfirm={() => onRedo(a)} />
              )}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
