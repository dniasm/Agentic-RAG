/** "A document changed" for whoever shows documents (the app's list, the Documents page): an edit saved from the
    preview tells them to read the list again. Framework-free. */
const listeners = new Set();

/** Tell every listener; `docId` is the document that changed. */
export function documentsChanged(docId) {
  for (const fn of [...listeners]) {
    try { fn(docId); } catch { /* one listener's failure doesn't stop the others */ }
  }
}

/** Listen; returns the function that stops listening (for a React effect's cleanup). */
export function onDocumentsChanged(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
