/** The Library's queue: batches of tasks a chat queued ("do these to every report"), still running or queued in
    the last hour, each task with its state and reply. Read once on load and again after a reconnect; the
    server then pushes each change over the WebSocket (queue/task, queue/changed). Hidden when there is nothing
    to show. */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { apiService } from '../services/api';
import { realtime } from '../services/realtime';
import { TASK_LABEL, TASK_PILL, applyTaskEvent, plainReply, queueSummary, waitingTasks } from '../lib/queue';

/** onTaskFinished(): a task finished (titles or files may have changed: reload the list). */
export default function QueuePanel({ onTaskFinished }) {
  const [queue, setQueue] = useState(null);
  const [error, setError] = useState(null);
  const current = useRef(null);
  const finished = useRef(onTaskFinished);
  finished.current = onTaskFinished;

  const show = (q) => { current.current = q; setQueue(q); };

  const load = useCallback(async () => {
    try {
      show(await apiService.getQueue());
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    load();
    let wasOpen = realtime.isOpen();
    const offState = realtime.onState((s) => {   // events may have been missed while it was down: read again
      if (s === 'open' && !wasOpen) load();
      wasOpen = s === 'open';
    });
    const offEvents = realtime.subscribe((msg) => {
      if (msg.channel === 'system' && msg.type === 'resync') load();
      else if (msg.channel === 'queue' && msg.type === 'changed') load();
      else if (msg.channel === 'queue' && msg.type === 'task') {
        const next = applyTaskEvent(current.current, msg.data);
        if (next) show(next); else load();
        if (!['queued', 'running'].includes(msg.data.status)) finished.current?.();
      }
    });
    return () => { offState(); offEvents(); };
  }, [load]);

  const cancel = async (batch) => {
    try { await apiService.cancelBatch(batch.id); } catch (err) { setError(`Couldn't cancel: ${err.message}`); }
    load();
  };

  const { batches, done, total } = queueSummary(queue);
  if (!batches.length) return null;

  return (
    <section className="panel mt-6" aria-labelledby="queue-title">
      <div className="flex items-baseline justify-between gap-3 px-4 pt-3 pb-2">
        <h2 id="queue-title" className="text-section font-medium">Queue</h2>
        <span className="text-small text-muted num" role="status">{done} of {total} finished</span>
      </div>
      {error && <p className="px-4 pb-2 text-small text-[var(--pill-negative-ink)]">{error}</p>}
      {batches.map((b) => (
        <div key={b.id} className="border-t border-rule">
          <ul>
            {b.tasks.map((t) => (
              <li key={t.id} className="flex items-start gap-3 px-4 py-2.5 border-t border-rule first:border-t-0">
                <span className={TASK_PILL[t.status] || 'pill-neutral'}>{TASK_LABEL[t.status] || t.status}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-body truncate" title={t.instruction}>{t.instruction}</span>
                  {t.reply && t.status !== 'queued' && (
                    <span className="block text-small text-muted line-clamp-2" title={plainReply(t.reply)}>
                      {plainReply(t.reply)}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
          {waitingTasks(b) > 0 && (
            <div className="px-4 pb-3">
              <button type="button" className="link-action" onClick={() => cancel(b)}>
                Cancel the {waitingTasks(b)} waiting
              </button>
            </div>
          )}
        </div>
      ))}
    </section>
  );
}
