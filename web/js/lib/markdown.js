/** A small, safe Markdown renderer for chat replies: paragraphs, numbered and bulleted lists, bold, italics, code.
    Everything is escaped first, so model output can't inject HTML. */
import { escapeHtml } from "./dom.js";

export function inline(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
}

export function markdown(text) {
  const out = [];
  let list = null;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of String(text ?? "").split("\n")) {
    const line = raw.trimEnd();
    const ol = line.match(/^\s*\d+[.)]\s+(.*)/);
    const ul = line.match(/^\s*[-*•]\s+(.*)/);
    if (ol || ul) {
      const kind = ol ? "ol" : "ul";
      if (list !== kind) { close(); out.push(`<${kind}>`); list = kind; }
      out.push(`<li>${inline((ol || ul)[1])}</li>`);
    } else if (!line.trim()) {
      close();
    } else {
      close();
      out.push(`<p>${inline(line.replace(/^#+\s*/, ""))}</p>`);
    }
  }
  close();
  return out.join("");
}
