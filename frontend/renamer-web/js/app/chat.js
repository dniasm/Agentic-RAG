/** The conversation: sending messages, showing replies with what was done, and the saved session. */
import { postJson } from "../lib/api.js";
import { escapeHtml, plural } from "../lib/dom.js";
import { markdown } from "../lib/markdown.js";
import { newStoredId, storedId } from "../lib/storage.js";
import { autosize, submitOnEnter } from "../lib/textarea.js";

const TYPING = '<span class="typing"><span></span><span></span><span></span></span>';

const LAYOUT_NOTE = {
  rewrapped: "the paragraph was re-wrapped to fit",
  moved: "the paragraph was re-wrapped and the page below it moved",
};

/** One line under a reply for each thing the agent did. */
function actionChip(a) {
  const chip = document.createElement("div");
  chip.className = `action ${a.type}`;
  chip.innerHTML = a.type === "queue"
    ? `<span>Queued</span><strong>${plural(a.tasks, "task")}</strong><span>see the Queue panel</span>`
    : a.type === "undo"
    ? `<span>Undid edit</span><strong>${escapeHtml(a.title)}</strong><span>${escapeHtml(a.undid)}</span>`
    : a.type === "edit"
    ? `<span>Edited</span><strong>${escapeHtml(a.title)}</strong><span>(${a.operation}, ${plural(a.changes, "change")})</span>`
      + (LAYOUT_NOTE[a.layout] ? `<span class="layout-note">${LAYOUT_NOTE[a.layout]}: check it</span>` : "")
    : `<span>Renamed</span><span class="old">${escapeHtml(a.old_title)}</span><span>→</span>
       <strong>${escapeHtml(a.new_title)}</strong>`;
  return chip;
}

/** els: messages, welcome, composer, input, send, newChat, suggestions. onReply(result) runs after each reply. */
/** sessionKey: where this browser keeps the current chat (one per user). */
/** onPreview(docId) opens a document; onUndo(docId) undoes its latest edit (a promise). */
export function createChat({ els, api, sessionKey, onReply = () => {}, onPreview = null, onUndo = null }) {
  let sessionId = storedId(sessionKey);
  let busy = false;
  const fitInput = autosize(els.input);
  const undoButtons = new Map();     /* doc id -> the Undo under its latest edit (undo always takes the latest back) */

  function button(label, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "link-button";
    b.textContent = label;
    b.addEventListener("click", onClick);
    return b;
  }

  /** Preview, and (for an edit made on this page) Undo, under an edit. */
  function editButtons(chip, a, live) {
    const row = document.createElement("span");
    row.className = "action-buttons";
    if (onPreview) row.append(button("Preview", () => onPreview(a.doc_id)));
    if (live && onUndo) {
      const undo = button("Undo", async () => {
        undo.disabled = true;
        try { await onUndo(a.doc_id); undo.textContent = "Undone"; undoButtons.delete(a.doc_id); }
        catch { undo.disabled = false; }
      });
      undoButtons.get(a.doc_id)?.remove();        /* an earlier edit's Undo would take this one back instead */
      undoButtons.set(a.doc_id, undo);
      row.append(undo);
    }
    if (row.childElementCount) chip.append(row);
  }

  function addMessage(role, text, actions = [], live = false) {
    els.welcome.hidden = true;
    const row = document.createElement("div");
    row.className = `msg ${role}`;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (role === "user") bubble.textContent = text;
    else bubble.innerHTML = markdown(text);
    for (const a of actions) {
      const chip = actionChip(a);
      if (a.type === "edit") editButtons(chip, a, live);
      if (a.type === "undo") undoButtons.get(a.doc_id)?.remove();   /* the edit it took back can't be undone again */
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

  async function send(raw) {
    const text = raw.trim();
    if (!text || busy) return;
    els.input.value = "";
    fitInput();
    addMessage("user", text);
    const typing = addMessage("assistant", "");
    typing.querySelector(".bubble").innerHTML = TYPING;
    setBusy(true);
    try {
      const result = await api("/api/chat", postJson({ session_id: sessionId, message: text }));
      typing.remove();
      addMessage("assistant", result.reply, result.actions, true);
      onReply(result);
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
    } catch { /* a new session */ }
  }

  function newChat() {
    sessionId = newStoredId(sessionKey);
    els.messages.querySelectorAll(".msg").forEach((m) => m.remove());
    els.welcome.hidden = false;
    els.input.focus();
  }

  els.composer.addEventListener("submit", (e) => { e.preventDefault(); send(els.input.value); });
  submitOnEnter(els.input, () => send(els.input.value));
  els.newChat.addEventListener("click", newChat);
  els.suggestions.querySelectorAll(".chip").forEach((chip) => chip.addEventListener("click", () => {
    if (chip.dataset.fill) {           /* a template: let the user finish it */
      els.input.value = chip.dataset.fill;
      els.input.focus();
      fitInput();
    } else {
      send(chip.textContent);
    }
  }));

  return { send, loadSession, newChat };
}
