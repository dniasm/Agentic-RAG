/** The files being added from the chat box, one chip each: uploading -> indexing -> ready, or failed.
    Framework-free reducer. state = { chips, seen }; `seen` keeps the latest library event per document, because
    an event can arrive before the upload's own reply names the document. */
export const initialUploads = { chips: [], seen: {} };

const TERMINAL = ['indexed', 'failed'];

function applyStage(chip, stage, { done, total, error } = {}) {
  if (stage === 'indexed') return { ...chip, status: 'ready', detail: '' };
  if (stage === 'failed') return { ...chip, status: 'failed', detail: error || 'Indexing failed.' };
  const progress = total ? ` ${done || 0}/${total}` : '';
  const what = stage === 'ocr' ? 'Reading scanned pages' : stage === 'embedding' ? 'Indexing' : 'Reading';
  return { ...chip, status: 'indexing', detail: `${what}${progress}` };
}

export function uploadsReducer(state, action) {
  const { chips, seen } = state;
  const patch = (match, fn) => ({ ...state, chips: chips.map((c) => (match(c) ? fn(c) : c)) });
  switch (action.type) {
    case 'add':
      return { ...state, chips: [...chips, { cid: action.cid, key: action.key, name: action.name, status: 'uploading', docId: null, detail: '' }] };
    case 'rejected':
      return patch((c) => c.cid === action.cid, (c) => ({ ...c, status: 'failed', detail: action.error }));
    case 'uploaded': {
      const early = seen[action.docId];
      return patch((c) => c.cid === action.cid, (c) => {
        const next = { ...c, docId: action.docId };
        if (TERMINAL.includes(action.docStatus)) return applyStage(next, action.docStatus, { error: action.error });
        return early ? applyStage(next, early.stage, early) : applyStage(next, 'reading');
      });
    }
    case 'event': {
      const next = { ...state, seen: { ...seen, [action.docId]: action } };
      return { ...next, chips: chips.map((c) => (c.docId === action.docId && c.status !== 'ready' ? applyStage(c, action.stage, action) : c)) };
    }
    case 'docs': {   // the documents list read over REST: the fallback when the socket is down
      const byId = new Map((action.docs || []).map((d) => [d.id, d]));
      return patch(
        (c) => c.status === 'indexing' && byId.has(c.docId) && TERMINAL.includes(byId.get(c.docId).status),
        (c) => { const d = byId.get(c.docId); return applyStage(c, d.status, { error: d.error }); },
      );
    }
    case 'remove':
      return { ...state, chips: chips.filter((c) => c.cid !== action.cid) };
    case 'rekey':   // a new chat got its id: the chips follow it
      return patch((c) => c.key === action.from, (c) => ({ ...c, key: action.to }));
    default:
      return state;
  }
}

export const chipsFor = (state, key) => state.chips.filter((c) => c.key === key);

/** Whether a chat opens at its latest message: it has messages and hasn't been positioned yet for this chat.
    `positionedFor` is the chat key last jumped for. */
export function shouldJumpToBottom({ key, positionedFor, messageCount }) {
  return messageCount > 0 && positionedFor !== key;
}
