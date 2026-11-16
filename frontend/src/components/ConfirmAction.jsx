/** A link-style action that asks first: one click shows the question with confirm and cancel, the second does it.
    For anything that changes a document (Undo, Redo under an edit in the chat). */
import React, { useState } from 'react';

/** label: the action ("Undo"); question: asked before it ("Undo this edit?"); busyLabel: while it runs;
    onConfirm: () => promise. A failure shows on the button (the caller shows the message). */
export default function ConfirmAction({ label, question, busyLabel, onConfirm }) {
  const [state, setState] = useState('idle');   // idle | asking | busy | failed
  if (state === 'asking' || state === 'busy') {
    return (
      <span className="inline-flex items-center gap-3" role="group" aria-label={question}>
        <span className="text-muted">{question}</span>
        <button type="button" className="link-action font-medium disabled:opacity-50" disabled={state === 'busy'}
                onClick={async () => {
                  setState('busy');
                  try { await onConfirm(); setState('idle'); } catch { setState('failed'); }
                }}>
          {state === 'busy' ? busyLabel : `Yes, ${label.toLowerCase()}`}
        </button>
        {state === 'asking' && (
          <button type="button" className="text-muted hover:text-ink" onClick={() => setState('idle')}>Cancel</button>
        )}
      </span>
    );
  }
  return (
    <button type="button" className="link-action" onClick={() => setState('asking')}
            title={state === 'failed' ? `${label} failed: see the message above` : undefined}>
      {label}
    </button>
  );
}
