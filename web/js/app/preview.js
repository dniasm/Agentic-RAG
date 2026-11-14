/** Document preview: a PDF in the site's own pdf.js viewer, Markdown rendered, anything else as its text. */
import { markdown } from "../lib/markdown.js";
import { createPdfViewer } from "../lib/pdf-viewer.js";
import { createModal } from "../lib/modal.js";

const el = (tag, cls, html) => Object.assign(document.createElement(tag), { className: cls, innerHTML: html ?? "" });

/** els: dialog, title, body, close. */
export function createPreview({ els, api, toast }) {
  let viewer = null;
  const modal = createModal(els, { onClose: () => { viewer?.destroy(); viewer = null; } });

  async function show(doc) {
    viewer?.destroy(); viewer = null;
    if (doc.ext === ".pdf") {
      viewer = createPdfViewer({
        url: `/api/documents/${doc.id}/file?inline=true`,
        downloadUrl: `/api/documents/${doc.id}/file`,
        fileName: doc.file_name ?? `${doc.title}.pdf`,
        onError: (e) => toast(`Can't preview: ${e.message}`),
      });
      modal.open(doc.title, viewer.element);
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
