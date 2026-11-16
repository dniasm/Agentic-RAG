/** The Library's queue: batches of tasks a chat queued ("do these to every report"), still running or queued in
    the last hour, each task with its state and reply. Polled every few seconds while any task waits, slowly
    otherwise; hidden when there is nothing to show. */
import React, { useEffect, useRef, useState } from 'react';
import { apiService } from '../services/api';
import {
  ACTIVE_POLL_MS, IDLE_POLL_MS, TASK_LABEL, TASK_PILL, plainReply, queueSummary, waitingTasks,
} from '../lib/queue';

/** onTaskFinished(): a task finished (titles or files may have changed: reload the list). */
export default function QueuePanel({ onTaskFinished }) {
  const [queue, setQueue] = useState(null);
  const [error, setError] = useState(null);
  const doneBefore = useRef(null);
  const timer = useRef(null);
  const finished = useRef(onTaskFinished);
  finished.current = onTaskFinished;

  const poll = async () => {
    clearTimeout(timer.current);
    let next = IDLE_POLL_MS;
    try {
      const q = await apiService.getQueue();
      const { done, active } = queueSummary(q);
      if (doneBefore.current !== null && done > doneBefore.current) finished.current?.();
      doneBefore.current = done;
      setQueue(q);
      setError(null);
      next = active ? ACTIVE_POLL_MS : IDLE_POLL_MS;
    } catch (err) {
      setError(err.message);
    }
    timer.current = setTimeout(poll, next);
  };

  useEffect(() => { poll(); return () => clearTimeout(timer.current); }, []);

  const cancel = async (batch) => {
    try { await apiService.cancelBatch(batch.id); } catch (err) { setError(`Couldn't cancel: ${err.message}`); }
    poll();
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
