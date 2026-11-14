/** A JSON fetch helper: throws an Error with the server's message (FastAPI's "detail") when a request fails. */

/** onUnauthorized() runs when the server answers 401 (the login is missing or has expired). */
export function createApi(base = "", { onUnauthorized = null } = {}) {
  return async function api(path, options = {}) {
    const res = await fetch(base + path, options);
    if (res.status === 401 && onUnauthorized) onUnauthorized();
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return res.status === 204 ? null : res.json();
  };
}

/** A JSON POST body: api(path, postJson({ a: 1 })). */
export function postJson(body, method = "POST") {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}
