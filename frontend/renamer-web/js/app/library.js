/** The library panel: lists the documents, uploads (button and drag-and-drop), removes, and keeps polling while
    any document is still being indexed. */
import { escapeHtml, plural } from "../lib/dom.js";
import { makeDropzone } from "../lib/dropzone.js";
import { createPoller } from "../lib/poller.js";

const STILL_INDEXING_POLL_MS = 2500;
const FLASH_MS = 1800;

function documentItem(d, flash) {
  const li = document.createElement("li");
  const type = (d.ext || "").replace(".", "");
  li.className = `doc t-${type}` + (flash ? " flash" : "");
  const badge = d.status === "indexed" ? "" :
    `<span class="badge ${d.status}">${d.status === "processing" ? "Indexing…" : "Failed"}</span>`;
  li.innerHTML = `
    <div class="doc-title">${escapeHtml(d.title)}</div>
    <div class="doc-meta"><span class="type">${escapeHtml(type.toUpperCase())}</span>${badge}<span title="Original file name">${escapeHtml(d.original_name)}</span>
      ${d.pages ? `<span>· ${plural(d.pages, "page")}</span>` : ""}</div>
    ${d.status === "failed" ? `<div class="doc-summary" style="color:var(--negative)">${escapeHtml(d.error || "")}</div>` :
      d.summary ? `<div class="doc-summary" title="${escapeHtml(d.summary)}">${escapeHtml(d.summary)}</div>` : ""}
    <div class="doc-actions">
      ${d.status === "indexed" ? `<button class="link-button" data-preview="${d.id}">Preview</button>` : ""}
      <a class="link-button" href="/api/documents/${d.id}/file" title="${escapeHtml(d.file_name)}">Download</a>
      ${d.status === "indexed" && d.ext !== ".pdf"
        ? `<a class="link-button" href="/api/documents/${d.id}/pdf" title="Download as a PDF (simple layout)">PDF</a>` : ""}
      ${d.status !== "processing" ? `<button class="link-button" data-delete="${d.id}">Remove</button>` : ""}
    </div>`;
  if (flash) setTimeout(() => li.classList.remove("flash"), FLASH_MS);
  return li;
}

/** els: panel, list, count, empty, fileInput, toggle. onPreview(doc) opens a document's preview. */
export function createLibrary({ els, api, toast, onPreview = () => {} }) {
  let docs = [];

  function render(flashIds = []) {
    els.list.innerHTML = "";
    const indexed = docs.filter((d) => d.status === "indexed").length;
    els.count.textContent = docs.length
      ? plural(docs.length, "document") + (indexed < docs.length ? `, ${docs.length - indexed} not ready` : "")
      : "No documents";
    els.empty.hidden = docs.length > 0;
    for (const d of [...docs].reverse()) els.list.appendChild(documentItem(d, flashIds.includes(d.id)));
  }

  const stillIndexing = () => docs.some((d) => d.status === "processing");

  const poller = createPoller(async () => {
    const before = new Map(docs.map((d) => [d.id, d.status]));
    docs = await api("/api/documents");
    const finished = docs.filter((d) => before.get(d.id) === "processing" && d.status !== "processing");
    for (const d of finished) toast(d.status === "indexed" ? `Added “${d.title}”` : `Couldn't index ${d.original_name}`);
    render(finished.map((d) => d.id));
    return stillIndexing() ? STILL_INDEXING_POLL_MS : null;
  }, { onError: (e) => toast(`Can't reach the server: ${e.message}`) });

  async function upload(fileList) {
    const files = [...fileList];
    if (!files.length) return;
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    toast(`Uploading ${plural(files.length, "file")}…`);
    try {
      const results = await api("/api/documents/upload", { method: "POST", body: form });
      const notes = results.filter((r) => r.status !== "queued")
        .map((r) => r.status === "duplicate"
          ? `${r.file} is already in the library` + (r.edited_since ? ` as “${r.document.title}” (edited since)` : "")
          : `${r.file}: ${r.error}`);
      if (notes.length) toast(notes.join(" · "));
      await poller.now();
    } catch (e) {
      toast(`Upload failed: ${e.message}`);
    }
  }

  els.fileInput.addEventListener("change", () => { upload(els.fileInput.files); els.fileInput.value = ""; });
  els.list.addEventListener("click", async (e) => {
    const previewId = e.target.dataset?.preview;
    if (previewId) return onPreview(docs.find((d) => d.id === previewId));
    const id = e.target.dataset?.delete;
    if (!id) return;
    const doc = docs.find((d) => d.id === id);
    if (!confirm(`Remove “${doc.title}” from the library?`)) return;
    try { await api(`/api/documents/${id}`, { method: "DELETE" }); await poller.now(); }
    catch (err) { toast(err.message); }
  });
  makeDropzone(els.panel, { onFiles: upload });
  els.toggle.addEventListener("click", () => els.panel.classList.toggle("open"));

  return {
    load: poller.now,
    /** A document by id, as last listed. */
    get: (id) => docs.find((d) => d.id === id),
    /** New documents from elsewhere (a chat reply), with the changed ones flashed. */
    set(newDocs, flashIds = []) {
      docs = newDocs;
      render(flashIds);
      if (stillIndexing()) poller.now();
    },
  };
}
