/** One document preview open at a time, opened from anywhere (the Documents page, a chat's buttons, the chat's
    side panel): openPreview(doc) with doc { id, title, ext }; <DocumentPreview /> shows it. */
import { useSyncExternalStore } from 'react';

let current = null;
const listeners = new Set();
const emit = () => listeners.forEach((fn) => fn());

export const openPreview = (doc) => { current = doc; emit(); };
export const closePreview = () => { current = null; emit(); };

export function usePreview() {
  return useSyncExternalStore((fn) => { listeners.add(fn); return () => listeners.delete(fn); }, () => current);
}
