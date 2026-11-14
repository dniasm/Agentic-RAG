/** The live token meter: polled every second, so it counts down as the minute's tokens free up. */
import { createPoller } from "../lib/poller.js";

const POLL_MS = 1000;
const WARN_AT = 50, HOT_AT = 80;   /* percent of the minute's limit */

function statusLine(m) {
  const status = m.wait_seconds > 0 ? `next in ${Math.ceil(m.wait_seconds)}s · ${m.reason}`
    : m.running_task ? "working on a task…"
    : m.used > 0 ? `clear in ${Math.ceil(m.free_in)}s${m.queued ? ` · ${m.queued} queued` : ""}`
    : m.queued ? `${m.queued} queued` : "idle";
  /* the backup model takes over while the main one's minute is full; it has its own allowance */
  return m.backup_used > 0 ? `${status} · backup model ${m.backup_used.toLocaleString()}` : status;
}

/** els: meter, text, timer, fill. */
export function createMeter({ els, api }) {
  function render(m) {
    const pct = Math.min(100, Math.round((m.used / m.limit) * 100));
    els.meter.classList.remove("offline");
    els.meter.classList.toggle("warn", pct >= WARN_AT && pct < HOT_AT);
    els.meter.classList.toggle("hot", pct >= HOT_AT);
    els.fill.style.width = `${pct}%`;
    els.text.textContent = `${m.used.toLocaleString()} / ${m.limit.toLocaleString()} tokens`;
    els.timer.textContent = statusLine(m);
  }

  const poller = createPoller(async () => {
    render(await api("/api/limits"));
    return POLL_MS;
  }, {
    errorDelay: POLL_MS,
    onError: () => { els.meter.classList.add("offline"); els.timer.textContent = "server unreachable"; },
  });

  return { start: poller.now };
}
