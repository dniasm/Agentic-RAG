/** Runs `task` again and again. The task returns how many ms to wait before the next run, or null to stop until
    `now()` is called. If it throws, `onError` runs and the next try is `errorDelay` ms later. */

export function createPoller(task, { errorDelay = 5000, onError = () => {} } = {}) {
  let timer = null;
  let running = false;

  async function run() {
    clearTimeout(timer);
    if (running) return;
    running = true;
    let next = null;
    try {
      next = await task();
    } catch (e) {
      onError(e);
      next = errorDelay;
    } finally {
      running = false;
    }
    if (next != null) timer = setTimeout(run, next);
  }

  return {
    now: run,                                 /* run at once (also starts the loop) */
    stop: () => clearTimeout(timer),
  };
}
