/** One document preview open at a time, opened from anywhere (the Documents page, a chat's buttons, the chat's
    side panel): openPreview(doc) with doc { id, title, ext }; <DocumentPreview /> shows it.

    The open preview holds one browser-history entry ({ preview: id }, same URL), so a phone's back gesture closes it
    instead of leaving the site. Opening another document while one is open replaces that entry; closing (X, Escape)
    goes back over it, so no stale entry is left. A press of Back while the preview is open reaches onPreviewBack(): the
    preview decides (an open editor first, then the preview). */
import { useSyncExternalStore } from 'react';

let current = null;
let backHandler = null;   // set by the preview: what Back does now
const listeners = new Set();
const emit = () => listeners.forEach((fn) => fn());

// A reload with the entry still there: nothing is open, so a Back would do nothing visible. Drop it.
try { if (window.history.state?.preview) window.history.replaceState(null, ''); } catch { /* no history */ }

export function openPreview(doc) {
  const entry = { ...(window.history.state || {}), preview: doc.id };
  try {
    if (current) window.history.replaceState(entry, '');
    else window.history.pushState(entry, '');
  } catch { /* no history: the preview still opens */ }
  current = doc;
  emit();
}

/** Close the preview (X, Escape, after a delete): goes back over its history entry. */
export function closePreview() {
  if (!current) return;
  if (window.history.state?.preview) window.history.back();   // popstate below finishes the close
  else { current = null; emit(); }
}

/** Put the entry back after a Back press the preview didn't close for (it closed an editor instead). */
export function keepPreviewEntry() {
  if (current) {
    try { window.history.pushState({ ...(window.history.state || {}), preview: current.id }, ''); } catch { /* none */ }
  }
}

/** The preview registers what Back does while it is open; returns the function that unregisters it. */
export function onPreviewBack(fn) {
  backHandler = fn;
  return () => { if (backHandler === fn) backHandler = null; };
}

window.addEventListener('popstate', () => {
  if (!current) return;
  if (backHandler && backHandler() === false) { keepPreviewEntry(); return; }   // the preview stays (an editor closed)
  current = null;
  emit();
});

export function usePreview() {
  return useSyncExternalStore((fn) => { listeners.add(fn); return () => listeners.delete(fn); }, () => current);
}
