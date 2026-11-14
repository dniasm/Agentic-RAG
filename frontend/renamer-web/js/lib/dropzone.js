/** Lets files be dropped on an element. While files are dragged over it, it has `activeClass`. */

export function makeDropzone(zone, { onFiles, activeClass = "dragging" }) {
  let depth = 0;   /* dragenter/leave fire for every child, so count them */
  zone.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; zone.classList.add(activeClass); });
  zone.addEventListener("dragleave", () => {
    if (--depth <= 0) { depth = 0; zone.classList.remove(activeClass); }
  });
  zone.addEventListener("dragover", (e) => e.preventDefault());
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0;
    zone.classList.remove(activeClass);
    if (e.dataTransfer?.files?.length) onFiles(e.dataTransfer.files);
  });
}
