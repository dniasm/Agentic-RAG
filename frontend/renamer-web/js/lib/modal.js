/** A modal window on a <dialog>: open it with a title and content, close it with Escape, the close button or a
    click on the backdrop. els: dialog, title, body, close. */
export function createModal({ dialog, title, body, close }, { onClose = () => {} } = {}) {
  const shut = () => dialog.close();
  close.addEventListener("click", shut);
  dialog.addEventListener("click", (e) => { if (e.target === dialog) shut(); });   /* the backdrop */
  dialog.addEventListener("close", () => { body.replaceChildren(); onClose(); });
  return {
    /** content: a Node, or a string shown as text. */
    open(heading, content) {
      title.textContent = heading;
      body.replaceChildren(typeof content === "string" ? document.createTextNode(content) : content);
      if (!dialog.open) dialog.showModal();
    },
    close: shut,
  };
}
