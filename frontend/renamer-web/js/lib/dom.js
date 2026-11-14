/** Small DOM helpers with no knowledge of any one site. */

/** Look up elements by id: elements({ list: "doc-list" }) -> { list: <ul> }. Fails loudly on a missing id. */
export function elements(ids) {
  const found = {};
  for (const [name, id] of Object.entries(ids)) {
    const el = document.getElementById(id);
    if (!el) throw new Error(`No element with id "${id}" (${name})`);
    found[name] = el;
  }
  return found;
}

const ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** Text made safe to put inside HTML. */
export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ENTITIES[c]);
}

/** "1 file", "3 files". */
export function plural(n, word, many = `${word}s`) {
  return `${n} ${n === 1 ? word : many}`;
}
