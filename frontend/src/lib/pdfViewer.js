import "./pdfViewer.css";
/** A PDF viewer drawn with pdf.js into the page, so it takes the site's own look: drag to pan, pinch / Ctrl+wheel /
    Ctrl +/- to zoom (anchored at the pointer), selectable / copyable text (press on text to select, elsewhere to pan), page box, fit-to-width, a narrow thumbnail rail and download.
    No knowledge of any one site: createPdfViewer({ url, downloadUrl, fileName, onError }) -> { element, destroy }. */

const PDFJS = "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build";
const MIN_SCALE = 0.25, MAX_SCALE = 5, STEP = 1.2, PAD = 16, MAX_PIXELS = 16_777_216, THUMB_WIDTH = 72;

let pdfjsPromise = null;
/** pdf.js, loaded on first use; its worker runs from a same-origin blob that imports the CDN module. */
function loadPdfjs() {
  pdfjsPromise ??= import(/* @vite-ignore */ `${PDFJS}/pdf.min.mjs`).then((lib) => {
    const blob = new Blob([`import "${PDFJS}/pdf.worker.min.mjs";`], { type: "text/javascript" });
    lib.GlobalWorkerOptions.workerPort = new Worker(URL.createObjectURL(blob), { type: "module" });
    return lib;
  });
  return pdfjsPromise;
}

const ICONS = {
  thumbs: '<path d="M4 4h6v7H4zM4 14h6v6H4zM14 4h6v16h-6z"/>',
  minus: '<path d="M5 12h14"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
};
const icon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export function createPdfViewer({ url, downloadUrl = url, fileName = "document.pdf", onError = () => {} }) {
  const root = document.createElement("div");
  root.className = "pdfv";
  root.innerHTML = `
    <div class="pdfv-bar">
      <button type="button" class="pdfv-btn" data-act="thumbs" title="Pages" aria-label="Show pages" aria-pressed="false" hidden>${icon("thumbs")}</button>
      <span class="pdfv-pageno"><input class="pdfv-input" inputmode="numeric" aria-label="Page" value="1"> / <span class="pdfv-count">–</span></span>
      <span class="pdfv-sep"></span>
      <button type="button" class="pdfv-btn" data-act="out" title="Zoom out (Ctrl −)" aria-label="Zoom out">${icon("minus")}</button>
      <button type="button" class="pdfv-zoom" data-act="reset" title="Actual size (Ctrl 0)">100%</button>
      <button type="button" class="pdfv-btn" data-act="in" title="Zoom in (Ctrl +)" aria-label="Zoom in">${icon("plus")}</button>
      <button type="button" class="pdfv-btn" data-act="fit" title="Fit to width" aria-label="Fit to width">${icon("fit")}</button>
      <span class="pdfv-fill"></span>
      <a class="pdfv-btn" title="Download" aria-label="Download">${icon("download")}</a>
    </div>
    <div class="pdfv-main">
      <aside class="pdfv-thumbs" hidden></aside>
      <div class="pdfv-view" tabindex="0"><div class="pdfv-pages"><p class="pdfv-note">Loading…</p></div></div>
    </div>`;
  const $ = (sel) => root.querySelector(sel);
  const view = $(".pdfv-view"), pagesEl = $(".pdfv-pages"), thumbsEl = $(".pdfv-thumbs");
  const pageInput = $(".pdfv-input"), zoomLabel = $(".pdfv-zoom"), thumbsBtn = $('[data-act="thumbs"]');
  Object.assign($("a.pdfv-btn"), { href: downloadUrl, download: fileName });

  let lib = null, doc = null, scale = 1, fitWidth = true, current = 1, destroyed = false, renderTimer = 0;
  const pages = [];     // { num, page, w, h, box, canvas, task, renderedScale, thumb }
  const visible = new Set();

  /* ------------------------------------------------------------ rendering */
  async function draw(p, canvas, cssScale) {
    const viewport = p.page.getViewport({ scale: cssScale });
    const ratio = Math.min(devicePixelRatio || 1, Math.sqrt(MAX_PIXELS / (viewport.width * viewport.height)));
    const off = document.createElement("canvas");
    off.width = Math.floor(viewport.width * ratio);
    off.height = Math.floor(viewport.height * ratio);
    const task = p.page.render({ canvasContext: off.getContext("2d"), viewport,
      transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0] });
    if (canvas === p.canvas) { p.task?.cancel(); p.task = task; }
    try { await task.promise; } catch { return; }   /* cancelled by a newer render */
    if (destroyed) return;
    canvas.width = off.width; canvas.height = off.height;   /* swap in only when done: no blank flash */
    canvas.getContext("2d").drawImage(off, 0, 0);
  }

  /** The page's invisible, selectable text, laid over the canvas: made on first sight, re-fitted after a zoom. */
  function layoutText(p) {
    const viewport = p.page.getViewport({ scale });
    if (p.text) { p.text.update({ viewport }); return; }
    const div = Object.assign(document.createElement("div"), { className: "textLayer" });
    p.box.append(div);
    p.text = new lib.TextLayer({ textContentSource: p.page.streamTextContent(), container: div, viewport });
    p.text.render().then(() => {
      /* pdf.js's trick for smooth selection: an end marker that covers the page while a selection is dragged */
      div.append(Object.assign(document.createElement("div"), { className: "endOfContent" }));
    }).catch(() => {});
  }

  function renderVisible() {
    for (const p of visible) {
      if (p.renderedScale === scale) continue;
      p.renderedScale = scale;
      draw(p, p.canvas, scale);
      layoutText(p);
    }
  }
  const scheduleRender = () => { clearTimeout(renderTimer); renderTimer = setTimeout(renderVisible, 120); };

  const pageObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      const p = pages[+e.target.dataset.index];
      e.isIntersecting ? visible.add(p) : visible.delete(p);
    }
    scheduleRender();
  }, { root: view, rootMargin: "600px 0px" });

  const thumbObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const p = pages[+e.target.dataset.index];
      thumbObserver.unobserve(e.target);
      draw(p, p.thumb, THUMB_WIDTH / p.w);
    }
  }, { root: thumbsEl, rootMargin: "200px 0px" });

  /* ------------------------------------------------------------ zoom */
  function sizePages() {
    for (const p of pages) {
      p.box.style.width = `${p.w * scale}px`;
      p.box.style.height = `${p.h * scale}px`;
    }
    pagesEl.style.setProperty("--scale-factor", String(scale));   /* the text layer sizes itself from this */
    zoomLabel.textContent = `${Math.round(scale * 100)}%`;
  }

  const fitScale = () => {
    const widest = Math.max(...pages.map((p) => p.w));
    return clamp((view.clientWidth - 2 * PAD) / widest, MIN_SCALE, MAX_SCALE);
  };

  /** Zoom to `next`, keeping the document point under client (x, y) where it is (default: the view's centre). */
  function zoomTo(next, x, y) {
    if (!pages.length) return;
    next = clamp(next, MIN_SCALE, MAX_SCALE);
    if (Math.abs(next - scale) < 1e-4) return;
    const r = view.getBoundingClientRect();
    x ??= r.left + r.width / 2; y ??= r.top + r.height / 2;
    let anchor = pages[0], best = Infinity;
    for (const p of pages) {      /* the page under the point, or the nearest one */
      const b = p.box.getBoundingClientRect();
      const d = y < b.top ? b.top - y : y > b.bottom ? y - b.bottom : 0;
      if (d < best) { best = d; anchor = p; }
      if (d === 0) break;
    }
    const before = anchor.box.getBoundingClientRect();
    const fx = (x - before.left) / before.width, fy = (y - before.top) / before.height;
    scale = next;
    sizePages();
    const after = anchor.box.getBoundingClientRect();
    view.scrollLeft += after.left + fx * after.width - x;
    view.scrollTop += after.top + fy * after.height - y;
    scheduleRender();
  }
  const zoomBy = (factor, x, y) => { fitWidth = false; zoomTo(scale * factor, x, y); };
  const fit = () => { fitWidth = true; zoomTo(fitScale()); };

  /* Ctrl/⌘ + wheel, and touchpad pinch (which browsers send as a Ctrl + wheel), anywhere over the viewer. */
  root.addEventListener("wheel", (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
    zoomBy(Math.exp(-clamp(dy, -50, 50) * 0.006), e.clientX, e.clientY);
  }, { passive: false });

  /* Safari's trackpad pinch */
  let gestureStart = 1;
  root.addEventListener("gesturestart", (e) => { e.preventDefault(); gestureStart = scale; });
  root.addEventListener("gesturechange", (e) => {
    e.preventDefault(); fitWidth = false; zoomTo(gestureStart * e.scale, e.clientX, e.clientY);
  });

  /* ------------------------------------------------------------ drag to pan, two-finger pinch on touch */
  const pointers = new Map();
  let pinch = null;
  view.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    /* a mouse or pen pressed on text selects it (the browser's own selection, so Ctrl+C copies it) */
    const layer = e.pointerType !== "touch" && e.target.closest(".textLayer");
    if (layer && e.target !== layer) {
      layer.classList.add("selecting");
      const done = () => { layer.classList.remove("selecting"); document.removeEventListener("pointerup", done); };
      document.addEventListener("pointerup", done);
      return;
    }
    /* on a scrollbar? measured against the view itself (offsetX/Y are relative to whatever was clicked, e.g. a page) */
    const r = view.getBoundingClientRect();
    if (e.clientX - r.left - view.clientLeft >= view.clientWidth ||
        e.clientY - r.top - view.clientTop >= view.clientHeight) return;
    if (e.pointerType !== "touch") getSelection()?.removeAllRanges();   /* a click off the text clears a selection */
    view.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    view.classList.add("dragging");
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinch = { dist: Math.hypot(a.x - b.x, a.y - b.y), scale };
    }
  });
  view.addEventListener("pointermove", (e) => {
    const prev = pointers.get(e.pointerId);
    if (!prev) return;
    const pt = { x: e.clientX, y: e.clientY };
    pointers.set(e.pointerId, pt);
    if (pinch && pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      fitWidth = false;
      zoomTo(pinch.scale * Math.hypot(a.x - b.x, a.y - b.y) / pinch.dist, (a.x + b.x) / 2, (a.y + b.y) / 2);
    } else if (pointers.size === 1) {
      view.scrollLeft -= pt.x - prev.x;
      view.scrollTop -= pt.y - prev.y;
    }
  });
  const release = (e) => {
    pointers.delete(e.pointerId);
    if (pointers.size < 2) pinch = null;
    if (!pointers.size) view.classList.remove("dragging");
  };
  view.addEventListener("pointerup", release);
  view.addEventListener("pointercancel", release);

  /* ------------------------------------------------------------ keys: Ctrl +/- zoom, Ctrl 0 actual size (arrows and PgUp/PgDn scroll natively) */
  const onKey = (e) => {
    if (!root.isConnected) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && (e.key === "+" || e.key === "=")) { e.preventDefault(); zoomBy(STEP); }
    else if (mod && e.key === "-") { e.preventDefault(); zoomBy(1 / STEP); }
    else if (mod && e.key === "0") { e.preventDefault(); fitWidth = false; zoomTo(1); }
  };
  document.addEventListener("keydown", onKey);

  /* ------------------------------------------------------------ toolbar */
  root.addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "in") zoomBy(STEP);
    else if (act === "out") zoomBy(1 / STEP);
    else if (act === "reset") { fitWidth = false; zoomTo(1); }
    else if (act === "fit") fit();
    else if (act === "thumbs") toggleThumbs();
    const thumb = e.target.closest(".pdfv-thumb");
    if (thumb) goTo(+thumb.dataset.index + 1);
  });

  function toggleThumbs(show = thumbsEl.hidden) {
    thumbsEl.hidden = !show;
    thumbsBtn.setAttribute("aria-pressed", String(show));
    if (fitWidth) requestAnimationFrame(fit);
  }

  function goTo(n) {
    n = clamp(Math.round(n) || 1, 1, pages.length);
    view.scrollTop = pages[n - 1].box.offsetTop - PAD;
    setCurrent(n);
  }
  pageInput.addEventListener("change", () => goTo(+pageInput.value));
  pageInput.addEventListener("keydown", (e) => { if (e.key === "Enter") pageInput.blur(); });

  function setCurrent(n) {
    if (n === current && pageInput.value === String(n)) return;
    pages[current - 1]?.thumbBox?.classList.remove("current");
    current = n;
    if (document.activeElement !== pageInput) pageInput.value = String(n);
    const tb = pages[n - 1]?.thumbBox;
    if (tb) { tb.classList.add("current"); if (!thumbsEl.hidden) tb.scrollIntoView({ block: "nearest" }); }
  }
  view.addEventListener("scroll", () => {
    const mid = view.scrollTop + view.clientHeight / 3;
    let n = 1;
    for (const p of pages) { if (p.box.offsetTop <= mid) n = p.num; else break; }
    setCurrent(n);
  }, { passive: true });

  const resize = new ResizeObserver(() => { if (fitWidth && pages.length) zoomTo(fitScale()); });
  resize.observe(view);

  /* ------------------------------------------------------------ load */
  (async () => {
    try {
      lib = await loadPdfjs();
      if (destroyed) return;
      const task = lib.getDocument({ url, isEvalSupported: false });
      doc = await task.promise;
      if (destroyed) { doc.destroy(); return; }
      pagesEl.replaceChildren();
      for (let i = 1; i <= doc.numPages; i++) {
        const page = await doc.getPage(i);
        if (destroyed) return;
        const { width: w, height: h } = page.getViewport({ scale: 1 });
        const box = Object.assign(document.createElement("div"), { className: "pdfv-page" });
        box.dataset.index = String(i - 1);
        const canvas = document.createElement("canvas");
        box.append(canvas);
        pagesEl.append(box);
        const thumbBox = Object.assign(document.createElement("button"), { type: "button", className: "pdfv-thumb" });
        thumbBox.dataset.index = String(i - 1);
        thumbBox.setAttribute("aria-label", `Page ${i}`);
        const thumb = document.createElement("canvas");
        thumb.style.aspectRatio = `${w} / ${h}`;
        thumbBox.append(thumb, Object.assign(document.createElement("span"), { textContent: String(i) }));
        thumbsEl.append(thumbBox);
        pages.push({ num: i, page, w, h, box, canvas, thumb, thumbBox, renderedScale: 0 });
        pageObserver.observe(box);
        thumbObserver.observe(thumbBox);
        if (i === 1) { scale = fitScale(); sizePages(); }
        else { box.style.width = `${w * scale}px`; box.style.height = `${h * scale}px`; }
      }
      $(".pdfv-count").textContent = String(doc.numPages);
      thumbsBtn.hidden = doc.numPages < 2;
      setCurrent(1);
      pages[0].thumbBox.classList.add("current");
      view.focus({ preventScroll: true });
    } catch (err) {
      if (destroyed) return;
      pagesEl.replaceChildren(Object.assign(document.createElement("p"),
        { className: "pdfv-note", textContent: "This PDF could not be shown." }));
      onError(err);
    }
  })();

  function destroy() {
    destroyed = true;
    clearTimeout(renderTimer);
    document.removeEventListener("keydown", onKey);
    pageObserver.disconnect(); thumbObserver.disconnect(); resize.disconnect();
    for (const p of pages) { p.task?.cancel(); p.text?.cancel(); }
    doc?.destroy();
  }

  return { element: root, destroy };
}
