// Text rewrites applied to an answer before it goes to react-markdown.

// Code spans and fenced blocks, and $$ … $$ math, are left alone by the rewrites below.
const CODE = /(```[\s\S]*?(?:```|$)|`[^`\n]*`)/;
const CODE_OR_MATH = /(```[\s\S]*?(?:```|$)|`[^`\n]*`|\$\$[\s\S]+?\$\$)/;
const outside = (pattern, text, fn) => text.split(pattern).map((part, i) => (i % 2 ? part : fn(part))).join('');

// Formulas. Documents (Wikipedia articles especially) contain LaTeX and the model copies it, usually as \[ … \]
// or \( … \), which remark-math doesn't read; in Markdown "\[" is just an escaped bracket. Rewrite those to $$ …
// $$ outside code. Single-dollar math stays off (remark-math's singleDollarTextMath) so "$5 and $6" stays money.
const MATH = /\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$\$([\s\S]+?)\$\$/g;
// The model sometimes ends a formula with its citation ("…\;[4]\n\]"). Inside the math it would render as a literal
// "[4]" (or, before this, as broken link markup), so it is moved after the formula, where it becomes a link.
const TRAILING_CITES = /(?:(?:\s|\\[;,:!]|~)*\[\d{1,2}\])+\s*$/;

function formula(body, display) {
  let cites = '';
  const m = body.match(TRAILING_CITES);
  if (m) {
    cites = m[0].match(/\[\d{1,2}\]/g).join('');
    body = body.slice(0, m.index);
  }
  body = body.trim();
  return display ? `\n$$\n${body}\n$$\n${cites}` : `$$${body}$$${cites}`;
}

export const withMath = (text) => outside(CODE, text, (part) => part.replace(MATH,
  (_, display, inline, dollars) => (display != null ? formula(display, true)
    : inline != null ? formula(inline, false) : formula(dollars, dollars.includes('\n')))));

// "[2]" in an answer becomes a citation marker in the hue, titled with the passage it points to.
// Markdown links ("[text](url)") are left as they are, and so are code and formulas: "[4]" there is not a citation.
const CITATION = /\[(\d{1,2})\](?!\()/g;
export const withCitations = (text) => outside(CODE_OR_MATH, text,
  (part) => part.replace(CITATION, (_, n) => `[\\[${n}\\]](#cite-${n})`));
