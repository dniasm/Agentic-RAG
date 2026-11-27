/** What the Library's queue panel shows of GET /library/queue. Framework-free. */

export const TASK_LABEL = {
  queued: 'Waiting', running: 'Running', done: 'Done', not_done: 'Not done', failed: 'Failed', cancelled: 'Cancelled',
};
export const TASK_PILL = {
  queued: 'pill-neutral', running: 'pill-warning', done: 'pill-positive', not_done: 'pill-neutral',
  failed: 'pill-negative', cancelled: 'pill-neutral',
};
export const unfinished = (task) => task.status === 'queued' || task.status === 'running';

/** The panel's figures: { batches, done, total, active } — active while any task is still to run. */
export function queueSummary(queue) {
  const batches = (queue?.batches || []).filter((b) => b.tasks?.length);
  const tasks = batches.flatMap((b) => b.tasks);
  return {
    batches,
    done: tasks.filter((t) => !unfinished(t)).length,
    total: tasks.length,
    active: tasks.some(unfinished),
  };
}

/** A batch's tasks that are still waiting: what "Cancel the rest" would stop. */
export const waitingTasks = (batch) => batch.tasks.filter((t) => t.status === 'queued').length;

/** The queue with one pushed task change applied ({batch_id, task_id, status, reply}); null when the task isn't in
    it (a batch the panel hasn't read yet): the caller then reads the queue again. */
export function applyTaskEvent(queue, { batch_id: batchId, task_id: taskId, status, reply }) {
  const batch = queue?.batches?.find((b) => b.id === batchId);
  if (!batch || !batch.tasks.some((t) => t.id === taskId)) return null;
  return {
    ...queue,
    batches: queue.batches.map((b) => (b.id !== batchId ? b : {
      ...b,
      tasks: b.tasks.map((t) => (t.id !== taskId ? t : { ...t, status, reply: reply ?? t.reply })),
    })),
  };
}

/** A task's reply as one plain line (the agent's replies are Markdown). */
export const plainReply = (text) => (text || '').replace(/[*_`#>]+/g, '').replace(/\s+/g, ' ').trim();
