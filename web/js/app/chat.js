/** The conversation: sending messages, showing replies with what was done, and the saved session. */
import { postJson } from "../lib/api.js";
import { escapeHtml, plural } from "../lib/dom.js";
import { markdown } from "../lib/markdown.js";
import { newStoredId, storedId } from "../lib/storage.js";
import { autosize, submitOnEnter } from "../lib/textarea.js";

const TYPING = '<span class="typing"><span></span><span></span><span></span></span>';

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
    : `<span>Renamed</span><span class="old">${escapeHtml(a.old_title)}</span><span>→</span>
       <strong>${escapeHtml(a.new_title)}</strong>`;
  return chip;
}

/** els: messages, welcome, composer, input, send, newChat, suggestions. onReply(result) runs after each reply. */
/** sessionKey: where this browser keeps the current chat (one per user). */
export function createChat({ els, api, sessionKey, onReply = () => {} }) {
  let sessionId = storedId(sessionKey);
  let busy = false;
  const fitInput = autosize(els.input);

  function addMessage(role, text, actions = []) {
    els.welcome.hidden = true;
    const row = document.createElement("div");
    row.className = `msg ${role}`;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (role === "user") bubble.textContent = text;
    else bubble.innerHTML = markdown(text);
    for (const a of actions) bubble.appendChild(actionChip(a));
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
      addMessage("assistant", result.reply, result.actions);
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
