"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  library: $("library"), docList: $("doc-list"), docCount: $("doc-count"), empty: $("empty-library"),
  fileInput: $("file-input"), messages: $("messages"), welcome: $("welcome"), composer: $("composer"),
  input: $("input"), send: $("send"), newChat: $("new-chat"), toast: $("toast"), toggle: $("toggle-library"),
  queue: $("queue"), queueCount: $("queue-count"), queueList: $("queue-list"),
  meter: $("meter"), meterText: $("meter-text"), meterTimer: $("meter-timer"), meterFill: $("meter-fill"),
};

let sessionId = localStorage.getItem("renamer-session") || newSessionId();
let docs = [];
let pollTimer = null;
let busy = false;

function newSessionId() {
  const id = crypto.randomUUID().replace(/-/g, "");
  localStorage.setItem("renamer-session", id);
  return id;
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.status === 204 ? null : res.json();
}

function toast(text) {
  els.toast.textContent = text;
  els.toast.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => els.toast.classList.remove("show"), 3200);
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

/* ---------------------------------------------------------------- library */
function renderDocs(flashIds = []) {
  els.docList.innerHTML = "";
  const indexed = docs.filter((d) => d.status === "indexed").length;
  els.docCount.textContent = docs.length
    ? `${docs.length} document${docs.length === 1 ? "" : "s"}${indexed < docs.length ? `, ${docs.length - indexed} not ready` : ""}`
    : "No documents";
  els.empty.hidden = docs.length > 0;

  for (const d of [...docs].reverse()) {
    const li = document.createElement("li");
    const type = (d.ext || "").replace(".", "");
    li.className = `doc t-${type}` + (flashIds.includes(d.id) ? " flash" : "");
    const renamedFrom = d.original_name;
    const badge = d.status === "indexed" ? "" :
      `<span class="badge ${d.status}">${d.status === "processing" ? "Indexing…" : "Failed"}</span>`;
    li.innerHTML = `
      <div class="doc-title">${escapeHtml(d.title)}</div>
      <div class="doc-meta"><span class="type">${escapeHtml(type.toUpperCase())}</span>${badge}<span title="Original file name">${escapeHtml(renamedFrom)}</span>
        ${d.pages ? `<span>· ${d.pages} page${d.pages === 1 ? "" : "s"}</span>` : ""}</div>
      ${d.status === "failed" ? `<div class="doc-summary" style="color:var(--bad)">${escapeHtml(d.error || "")}</div>` :
        d.summary ? `<div class="doc-summary" title="${escapeHtml(d.summary)}">${escapeHtml(d.summary)}</div>` : ""}
      <div class="doc-actions">
        <a class="link-button" href="/api/documents/${d.id}/file" title="${escapeHtml(d.file_name)}">Download</a>
        ${d.status === "indexed" && d.ext !== ".pdf"
          ? `<a class="link-button" href="/api/documents/${d.id}/pdf" title="Download as a PDF (simple layout)">PDF</a>` : ""}
        ${d.status !== "processing" ? `<button class="link-button" data-delete="${d.id}">Remove</button>` : ""}
      </div>`;
    els.docList.appendChild(li);
    if (flashIds.includes(d.id)) setTimeout(() => li.classList.remove("flash"), 1800);
  }

  clearTimeout(pollTimer);
  if (docs.some((d) => d.status === "processing")) pollTimer = setTimeout(loadDocs, 2500);
}

async function loadDocs() {
  try {
    const before = new Map(docs.map((d) => [d.id, d.status]));
    docs = await api("/api/documents");
    const finished = docs.filter((d) => before.get(d.id) === "processing" && d.status !== "processing");
    for (const d of finished) toast(d.status === "indexed" ? `Added “${d.title}”` : `Couldn't index ${d.original_name}`);
    renderDocs(finished.map((d) => d.id));
  } catch (e) {
    toast(`Can't reach the server: ${e.message}`);
    clearTimeout(pollTimer);
    pollTimer = setTimeout(loadDocs, 5000);
  }
}

async function upload(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  toast(`Uploading ${files.length} file${files.length === 1 ? "" : "s"}…`);
  try {
    const results = await api("/api/documents/upload", { method: "POST", body: form });
    const notes = results.filter((r) => r.status !== "queued")
      .map((r) => r.status === "duplicate" ? `${r.file} is already in the library` : `${r.file}: ${r.error}`);
    if (notes.length) toast(notes.join(" · "));
    await loadDocs();
  } catch (e) {
    toast(`Upload failed: ${e.message}`);
  }
}

els.fileInput.addEventListener("change", () => { upload(els.fileInput.files); els.fileInput.value = ""; });
els.docList.addEventListener("click", async (e) => {
  const id = e.target.dataset?.delete;
  if (!id) return;
  const doc = docs.find((d) => d.id === id);
  if (!confirm(`Remove “${doc.title}” from the library?`)) return;
  try { await api(`/api/documents/${id}`, { method: "DELETE" }); await loadDocs(); }
  catch (err) { toast(err.message); }
});
let dragDepth = 0;
els.library.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; els.library.classList.add("dragging"); });
els.library.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; els.library.classList.remove("dragging"); } });
els.library.addEventListener("dragover", (e) => e.preventDefault());
els.library.addEventListener("drop", (e) => {
  e.preventDefault(); dragDepth = 0; els.library.classList.remove("dragging");
  upload(e.dataTransfer.files);
});
els.toggle.addEventListener("click", () => els.library.classList.toggle("open"));

/* ---------------------------------------------------------------- chat */
function inline(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
}

/** Paragraphs, numbered and bulleted lists, bold, italics and code: all the agent's replies use. */
function markdown(text) {
  const out = [];
  let list = null;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const ol = line.match(/^\s*\d+[.)]\s+(.*)/);
    const ul = line.match(/^\s*[-*•]\s+(.*)/);
    if (ol || ul) {
      const kind = ol ? "ol" : "ul";
      if (list !== kind) { close(); out.push(`<${kind}>`); list = kind; }
      out.push(`<li>${inline((ol || ul)[1])}</li>`);
    } else if (!line.trim()) {
      close();
    } else {
      close();
      out.push(`<p>${inline(line.replace(/^#+\s*/, ""))}</p>`);
    }
  }
  close();
  return out.join("");
}

function addMessage(role, text, actions = []) {
  els.welcome.hidden = true;
  const row = document.createElement("div");
  row.className = `msg ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (role === "user") bubble.textContent = text;
  else bubble.innerHTML = markdown(text);
  for (const a of actions) {
    const chip = document.createElement("div");
    chip.className = `action ${a.type}`;
    chip.innerHTML = a.type === "queue"
      ? `<span>Queued</span><strong>${a.tasks} task${a.tasks === 1 ? "" : "s"}</strong><span>see the Queue panel</span>`
      : a.type === "undo"
      ? `<span>Undid edit</span><strong>${escapeHtml(a.title)}</strong><span>${escapeHtml(a.undid)}</span>`
      : a.type === "edit"
      ? `<span>Edited</span><strong>${escapeHtml(a.title)}</strong><span>(${a.operation}, ${a.changes} change${a.changes === 1 ? "" : "s"})</span>`
      : `<span>Renamed</span><span class="old">${escapeHtml(a.old_title)}</span><span>→</span>
      <strong>${escapeHtml(a.new_title)}</strong>`;
    bubble.appendChild(chip);
  }
  row.appendChild(bubble);
  els.messages.appendChild(row);
  els.messages.scrollTop = els.messages.scrollHeight;
  return row;
}

function setBusy(value) {
  busy = value;
  els.send.disabled = value;
}

async function send(text) {
  text = text.trim();
  if (!text || busy) return;
  els.input.value = "";
  autosize();
  addMessage("user", text);
  const typing = addMessage("assistant", "");
  typing.querySelector(".bubble").innerHTML = '<span class="typing"><span></span><span></span><span></span></span>';
  setBusy(true);
  try {
    const result = await api("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    typing.remove();
    addMessage("assistant", result.reply, result.actions);
    docs = result.documents;
    renderDocs(result.actions.map((a) => a.doc_id));
    if (result.actions.some((a) => a.type === "queue")) loadQueue();
  } catch (e) {
    typing.remove();
    addMessage("assistant", `Something went wrong: ${e.message}`);
  } finally {
    setBusy(false);
    els.input.focus();
  }
}

async function loadSession() {
  try {
    const { messages } = await api(`/api/sessions/${sessionId}`);
    for (const m of messages) addMessage(m.role, m.text, m.actions || []);
  } catch { /* new session */ }
}

function autosize() {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 180)}px`;
}

els.composer.addEventListener("submit", (e) => { e.preventDefault(); send(els.input.value); });
els.input.addEventListener("input", autosize);
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(els.input.value); }
});
els.newChat.addEventListener("click", () => {
  sessionId = newSessionId();
  els.messages.querySelectorAll(".msg").forEach((m) => m.remove());
  els.welcome.hidden = false;
  els.input.focus();
});
document.querySelectorAll(".suggestions .chip").forEach((chip) => chip.addEventListener("click", () => {
  if (chip.dataset.fill) {   // a template: let the user finish it
    els.input.value = chip.dataset.fill;
    els.input.focus();
    autosize();
  } else {
    send(chip.textContent);
  }
}));

/* ---------------------------------------------------------------- queue */
const TASK_LABEL = { queued: "waiting", running: "running…", done: "done", not_done: "not done", failed: "failed", cancelled: "cancelled" };
let queueTimer = null, queueDone = null;

async function loadQueue() {
  clearTimeout(queueTimer);
  let q;
  try { q = await api("/api/queue"); } catch { queueTimer = setTimeout(loadQueue, 8000); return; }
  const batches = q.batches.filter((b) => b.tasks.some((t) => ["queued", "running"].includes(t.status)) ||
    Date.now() - Date.parse(b.created_at) < 3600e3);
  const active = q.batches.some((b) => b.tasks.some((t) => ["queued", "running"].includes(t.status)));
  els.queue.hidden = batches.length === 0;
  els.queueList.innerHTML = "";
  let done = 0, total = 0;
  for (const b of batches) {
    for (const t of b.tasks) {
      total++; if (!["queued", "running"].includes(t.status)) done++;
      const li = document.createElement("li");
      li.className = `task ${t.status}`;
      li.innerHTML = `<span class="task-state">${TASK_LABEL[t.status]}</span>
        <span class="task-text" title="${escapeHtml(t.reply || t.instruction)}">${escapeHtml(t.instruction)}</span>` +
        (t.reply && t.status !== "queued" ? `<div class="task-reply">${escapeHtml(t.reply)}</div>` : "");
      els.queueList.appendChild(li);
    }
    if (b.tasks.some((t) => t.status === "queued")) {
      const cancel = document.createElement("button");
      cancel.className = "link-button"; cancel.textContent = "Cancel the rest";
      cancel.onclick = async () => {
        try { await api(`/api/batches/${b.id}`, { method: "DELETE" }); } catch (e) { toast(`Couldn't cancel: ${e.message}`); }
        loadQueue();
      };
      els.queueList.appendChild(cancel);
    }
  }
  els.queueCount.textContent = total ? `${done} of ${total} finished` : "";
  if (queueDone !== null && done > queueDone) loadDocs();   // a task finished: titles/summaries may have changed
  queueDone = done;
  queueTimer = setTimeout(loadQueue, active ? 3000 : 15000);
}

/* live token meter: polled every second, so it counts down as the minute's tokens free up */
function renderMeter(m) {
  const pct = Math.min(100, Math.round((m.used / m.limit) * 100));
  els.meter.classList.remove("offline");
  els.meter.classList.toggle("warn", pct >= 50 && pct < 80);
  els.meter.classList.toggle("hot", pct >= 80);
  els.meterFill.style.width = `${pct}%`;
  els.meterText.textContent = `${m.used.toLocaleString()} / ${m.limit.toLocaleString()} tokens`;
  const status = m.wait_seconds > 0 ? `next in ${Math.ceil(m.wait_seconds)}s · ${m.reason}`
    : m.running_task ? "working on a task…"
    : m.used > 0 ? `clear in ${Math.ceil(m.free_in)}s${m.queued ? ` · ${m.queued} queued` : ""}`
    : m.queued ? `${m.queued} queued` : "idle";
  // the 20B takes over while the 120B's minute is full; it has its own allowance
  els.meterTimer.textContent = m.backup_used > 0 ? `${status} · backup model ${m.backup_used.toLocaleString()}` : status;
}

async function pollMeter() {
  try {
    renderMeter(await api("/api/limits"));
  } catch {
    els.meter.classList.add("offline");
    els.meterTimer.textContent = "server unreachable";
  }
  setTimeout(pollMeter, 1000);
}

loadDocs();
loadSession();
loadQueue();
pollMeter();

/* a shorter example in the input on narrow screens, where the long one wraps out of sight */
const narrow = matchMedia("(max-width: 640px)");
const longPlaceholder = els.input.placeholder;
const fitPlaceholder = () => { els.input.placeholder = narrow.matches ? "Rename, edit or undo a document…" : longPlaceholder; };
narrow.addEventListener("change", fitPlaceholder);
fitPlaceholder();

/* ---------------------------------------------------------------- resizable library */
const LIBRARY_WIDTH = { min: 240, max: 600, default: 320, key: "renamer-library-width" };
const resizer = document.getElementById("resizer");

function setLibraryWidth(px, save = true) {
  const width = Math.round(Math.min(LIBRARY_WIDTH.max, Math.max(LIBRARY_WIDTH.min, px)));
  document.documentElement.style.setProperty("--library-width", `${width}px`);
  resizer.setAttribute("aria-valuenow", width);
  if (save) localStorage.setItem(LIBRARY_WIDTH.key, width);
}

setLibraryWidth(Number(localStorage.getItem(LIBRARY_WIDTH.key)) || LIBRARY_WIDTH.default, false);
resizer.setAttribute("aria-valuemin", LIBRARY_WIDTH.min);
resizer.setAttribute("aria-valuemax", LIBRARY_WIDTH.max);

resizer.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  try { resizer.setPointerCapture(e.pointerId); } catch { /* a synthetic event has no pointer to capture */ }
  document.body.classList.add("resizing");
  const left = els.library.getBoundingClientRect().left;
  const move = (ev) => setLibraryWidth(ev.clientX - left);
  const stop = () => {
    document.body.classList.remove("resizing");
    resizer.removeEventListener("pointermove", move);
    resizer.removeEventListener("pointerup", stop);
    resizer.removeEventListener("pointercancel", stop);
  };
  resizer.addEventListener("pointermove", move);
  resizer.addEventListener("pointerup", stop);
  resizer.addEventListener("pointercancel", stop);
});
resizer.addEventListener("dblclick", () => setLibraryWidth(LIBRARY_WIDTH.default));
resizer.addEventListener("keydown", (e) => {   /* arrow keys resize too, for the keyboard */
  const step = e.shiftKey ? 64 : 16;
  const now = els.library.getBoundingClientRect().width;
  if (e.key === "ArrowLeft") { setLibraryWidth(now - step); e.preventDefault(); }
  if (e.key === "ArrowRight") { setLibraryWidth(now + step); e.preventDefault(); }
});
