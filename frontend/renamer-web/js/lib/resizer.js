/** Makes a panel resizable by dragging a handle (or with the arrow keys while the handle has focus).
    The width goes into a CSS variable, so the layout decides how to use it, and is remembered in localStorage.
    Double-click the handle to reset. Give the handle role="separator" and aria-orientation in the HTML. */

export function makeResizable({ handle, panel, cssVar, min = 200, max = 600, initial = 320, storageKey = null,
                                 activeClass = "resizing", step = 16 }) {
  function set(px, save = true) {
    const width = Math.round(Math.min(max, Math.max(min, px)));
    document.documentElement.style.setProperty(cssVar, `${width}px`);
    handle.setAttribute("aria-valuenow", width);
    if (save && storageKey) localStorage.setItem(storageKey, width);
    return width;
  }

  handle.setAttribute("aria-valuemin", min);
  handle.setAttribute("aria-valuemax", max);
  set(Number(storageKey && localStorage.getItem(storageKey)) || initial, false);

  handle.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    try { handle.setPointerCapture(e.pointerId); } catch { /* a synthetic event has no pointer to capture */ }
    document.body.classList.add(activeClass);
    const left = panel.getBoundingClientRect().left;
    const move = (ev) => set(ev.clientX - left);
    const stop = () => {
      document.body.classList.remove(activeClass);
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", stop);
      handle.removeEventListener("pointercancel", stop);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", stop);
    handle.addEventListener("pointercancel", stop);
  });
  handle.addEventListener("dblclick", () => set(initial));
  handle.addEventListener("keydown", (e) => {
    const by = e.shiftKey ? step * 4 : step;
    const now = panel.getBoundingClientRect().width;
    if (e.key === "ArrowLeft") { set(now - by); e.preventDefault(); }
    if (e.key === "ArrowRight") { set(now + by); e.preventDefault(); }
  });

  return { set, reset: () => set(initial) };
}
