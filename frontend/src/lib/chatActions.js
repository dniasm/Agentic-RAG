/** What a chat's document actions (meta.actions on its answers) allow. Framework-free. */

/** The edit ids whose Undo still applies: each document's latest edit as the server last reported it
    (chatDocs: GET /conversations/{id}/documents), if this chat made it and it hasn't been undone here since.
    An edit from another chat or a queued task, or an undo, moves it out of reach. The server checks again. */
export function undoableEdits(chatDocs, undone = new Set()) {
  return new Set((chatDocs || []).map((d) => d.latest_edit_id).filter((id) => id != null && !undone.has(id)));
}

/** The edit ids a Redo can bring back: each document's edit undone most recently, as the server last reported it. */
export function redoableEdits(chatDocs) {
  return new Set((chatDocs || []).map((d) => d.redo_edit_id).filter((id) => id != null));
}

/** A one-line description of an action, for screen readers and titles. */
export function describeAction(a) {
  if (a.type === 'rename') return `Renamed “${a.old_title}” to “${a.new_title}”`;
  if (a.type === 'edit') return `Edited “${a.title}” (${a.operation}, ${a.changes} change${a.changes === 1 ? '' : 's'})`;
  if (a.type === 'undo') return `Undid an edit to “${a.title}”`;
  if (a.type === 'redo') return `Brought back an edit to “${a.title}”`;
  if (a.type === 'queue') return `Queued ${a.tasks} task${a.tasks === 1 ? '' : 's'}`;
  return a.type;
}

export const LAYOUT_NOTE = {
  rewrapped: 'The paragraph was re-wrapped to fit: check it.',
  moved: 'The paragraph was re-wrapped and the page below it moved: check it.',
};
