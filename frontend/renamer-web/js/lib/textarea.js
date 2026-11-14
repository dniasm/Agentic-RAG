/** Textarea helpers: grow with the text, send on Enter, and a placeholder that changes on narrow screens. */

/** Grows the textarea with its content, up to `max` px. Returns a function to re-measure (after setting .value). */
export function autosize(textarea, max = 180) {
  const fit = () => {
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, max)}px`;
  };
  textarea.addEventListener("input", fit);
  return fit;
}

/** Enter submits, Shift+Enter makes a new line. */
export function submitOnEnter(textarea, onSubmit) {
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); onSubmit(); }
  });
}

/** A shorter placeholder while `query` matches (for example a phone width, where a long one wraps out of sight). */
export function responsivePlaceholder(textarea, query, short) {
  const media = matchMedia(query);
  const long = textarea.placeholder;
  const fit = () => { textarea.placeholder = media.matches ? short : long; };
  media.addEventListener("change", fit);
  fit();
}
