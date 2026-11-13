/** The queue panel: batches still running or queued in the last hour, polled fast while any task is waiting. */
import { escapeHtml } from "../lib/dom.js";
import { createPoller } from "../lib/poller.js";

const ACTIVE_POLL_MS = 3000;
const IDLE_POLL_MS = 15000;
const RECENT_MS = 3600e3;
const LABEL = { queued: "waiting", running: "running…", done: "done", not_done: "not done", failed: "failed", cancelled: "cancelled" };
const unfinished = (t) => t.status === "queued" || t.status === "running";

function taskItem(t) {
  const li = document.createElement("li");
  li.className = `task ${t.status}`;
  li.innerHTML = `<span class="task-state">${LABEL[t.status]}</span>
    <span class="task-text" title="${escapeHtml(t.reply || t.instruction)}">${escapeHtml(t.instruction)}</span>` +
    (t.reply && t.status !== "queued" ? `<div class="task-reply">${escapeHtml(t.reply)}</div>` : "");
  return li;
}

/** els: panel, list, count. onTaskFinished() runs when a task finishes (titles and summaries may have changed). */
export function createQueue({ els, api, toast, onTaskFinished = () => {} }) {
  let doneBefore = null;

  const poller = createPoller(async () => {
    const q = await api("/api/queue");
    const batches = q.batches.filter((b) => b.tasks.some(unfinished) || Date.now() - Date.parse(b.created_at) < RECENT_MS);
    const active = batches.some((b) => b.tasks.some(unfinished));
    els.panel.hidden = batches.length === 0;
    els.list.innerHTML = "";
    let done = 0, total = 0;
    for (const b of batches) {
      for (const t of b.tasks) {
        total++;
        if (!unfinished(t)) done++;
        els.list.appendChild(taskItem(t));
      }
      if (b.tasks.some((t) => t.status === "queued")) {
        const cancel = document.createElement("button");
        cancel.className = "link-button";
        cancel.textContent = "Cancel the rest";
        cancel.onclick = async () => {
          try { await api(`/api/batches/${b.id}`, { method: "DELETE" }); } catch (e) { toast(`Couldn't cancel: ${e.message}`); }
          poller.now();
        };
        els.list.appendChild(cancel);
      }
    }
    els.count.textContent = total ? `${done} of ${total} finished` : "";
    if (doneBefore !== null && done > doneBefore) onTaskFinished();
    doneBefore = done;
    return active ? ACTIVE_POLL_MS : IDLE_POLL_MS;
  }, { errorDelay: 8000 });

  return { refresh: poller.now };
}
