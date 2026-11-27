/** Files added from the chat box. Lives outside the page so an upload keeps going (and its chip stays) while a
    new chat gets its id and the page is rebuilt. Progress comes from the WebSocket's library events; only when
    the socket is down are the documents read again now and then. */
import { useSyncExternalStore } from 'react';
import { apiService } from '../services/api';
import { realtime } from '../services/realtime';
import { checkFile } from '../lib/uploadRules';
import { initialUploads, uploadsReducer } from '../lib/uploadChips';
import { documentsChanged } from './documentEvents';

let state = initialUploads;
let nextId = 1;
let started = false;
let timer = null;
const listeners = new Set();

function dispatch(action) {
  state = uploadsReducer(state, action);
  listeners.forEach((fn) => fn());
  schedulePoll();
}

function schedulePoll() {
  const waiting = state.chips.some((c) => c.status === 'indexing');
  if (!waiting || timer) return;
  timer = setTimeout(async () => {
    timer = null;
    if (!realtime.isOpen()) {
      try {
        const docs = await apiService.getDocuments();
        dispatch({ type: 'docs', docs });
        documentsChanged();
      } catch { /* try again next time */ }
    }
    schedulePoll();
  }, 5000);
}

function start() {
  if (started) return;
  started = true;
  realtime.subscribe((msg) => {
    if (msg.channel === 'library' && msg.type === 'document') {
      const d = msg.data;
      dispatch({ type: 'event', docId: d.doc_id, stage: d.stage, done: d.done, total: d.total, error: d.error });
    }
  });
}

const subscribe = (fn) => { start(); listeners.add(fn); return () => listeners.delete(fn); };

export const useUploads = () => useSyncExternalStore(subscribe, () => state);
export const removeUpload = (cid) => dispatch({ type: 'remove', cid });
export const moveUploads = (from, to) => dispatch({ type: 'rekey', from, to });

/** Add files to the library from the chat `key`; conversationId is set once the chat exists. */
export async function addFiles(fileList, key, conversationId) {
  start();
  for (const file of Array.from(fileList || [])) {
    const cid = nextId++;
    dispatch({ type: 'add', cid, key, name: file.name });
    const problem = checkFile(file);
    if (problem) { dispatch({ type: 'rejected', cid, error: problem }); continue; }
    try {
      const data = await apiService.uploadDocument(file, { conversationId });
      dispatch({ type: 'uploaded', cid, docId: data.document.id, docStatus: data.document.status, error: data.document.error });
      documentsChanged(data.document.id);
    } catch (err) {
      dispatch({ type: 'rejected', cid, error: err.message });
    }
  }
}
