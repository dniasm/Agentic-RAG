/** A small, safe Markdown renderer for chat replies: paragraphs, numbered and bulleted lists, bold, italics, code,
    and backslash escapes (\* shows a literal asterisk). Everything is escaped first, so model output can't inject
    HTML. As in CommonMark, emphasis markers must hug their text: "2 * 3 * 4" stays as written. */
import { escapeHtml } from "./dom.js";

const ESCAPABLE = /\\([\\`*_#\[\]()>~-])/g;
/* placeholders for escaped characters, so the emphasis rules below never see them */
const hide = (text) => text.replace(ESCAPABLE, (_, c) => `\u0000${c.charCodeAt(0)}\u0000`);
const restore = (text, as = (c) => c) =>
  text.replace(/\u0000(\d+)\u0000/g, (_, code) => as(String.fromCharCode(Number(code))));

export function inline(text) {
  return restore(escapeHtml(hide(String(text ?? "")))
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*(?=\S)([^*]*?\S)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![*\w])/g, "$1<em>$2</em>"), escapeHtml);
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
      out.push(`<p>${inline(line.replace(/^#+\s+/, ""))}</p>`);
    }
  }
  close();
  return out.join("");
}

/** The text of a Markdown string without its markup, for places that show plain text (a list, a tooltip). */
export function plainText(text) {
  return restore(hide(String(text ?? ""))
    .replace(/\*\*(?=\S)([^*]*?\S)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1"));
}
