/** An id kept in localStorage across visits (a chat session, a draft): read it, or make and keep a new one. */

export function storedId(key) {
  return localStorage.getItem(key) || newStoredId(key);
}

export function newStoredId(key) {
  const id = crypto.randomUUID().replace(/-/g, "");
  localStorage.setItem(key, id);
  return id;
}
