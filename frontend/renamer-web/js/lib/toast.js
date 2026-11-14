/** A one-line notice that shows for a few seconds. The element's CSS shows it while it has the class `show`. */

export function createToast(el, { duration = 3200, showClass = "show" } = {}) {
  let timer = null;
  return function toast(text) {
    el.textContent = text;
    el.classList.add(showClass);
    clearTimeout(timer);
    timer = setTimeout(() => el.classList.remove(showClass), duration);
  };
}
