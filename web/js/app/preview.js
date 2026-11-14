/** Document preview: a PDF in the browser's own viewer, Markdown rendered, anything else as its text. */
import { markdown } from "../lib/markdown.js";
import { createModal } from "../lib/modal.js";

const el = (tag, cls, html) => Object.assign(document.createElement(tag), { className: cls, innerHTML: html ?? "" });

/** els: dialog, title, body, close. */
export function createPreview({ els, api, toast }) {
  const modal = createModal(els);

  async function show(doc) {
    if (doc.ext === ".pdf") {
      modal.open(doc.title, Object.assign(el("iframe", "preview-frame"),
        { src: `/api/documents/${doc.id}/file?inline=true`, title: doc.title }));
      return;
    }
    modal.open(doc.title, el("p", "preview-note", "Loading…"));
    try {
      const { text, truncated } = await api(`/api/documents/${doc.id}/preview`);
      const page = doc.ext === ".md" ? el("div", "preview-text md", markdown(text))
                                     : Object.assign(el("pre", "preview-text"), { textContent: text });
      const wrap = document.createElement("div");
      wrap.append(page);
      if (truncated) wrap.append(el("p", "preview-note", "The rest is cut off here; download the file to see all of it."));
      modal.open(doc.title, wrap);
    } catch (e) {
      modal.close();
      toast(`Can't preview: ${e.message}`);
    }
  }
  return { show };
}
