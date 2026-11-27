"""Checks on a finished answer, in code."""
import re

# A template slot the model left unfilled: "[Document Name]", "[A concise description ...]", "{{title}}", "<your name>".
# Not a citation ("[1]", "[2, 3]"), a link ("[text](url)"), a checkbox ("[x]") or anything in code.
_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
_SLOT = re.compile(
    r"\[(?![\d\s,]+\])(?!\s*[xX ]?\s*\])([A-Za-z][^\[\]\n]{2,80})\](?!\()"
    r"|\{\{\s*[\w .-]{2,60}\s*\}\}"
    r"|<\s*(?:your|insert|add|enter)\s[^<>\n]{2,60}>", re.I)
_SLOT_WORD = re.compile(r"(?:insert|your|name|description|title|date|here|placeholder|summary|topic)", re.I)
SAFE_ANSWER = ("I couldn't put together a reliable answer to that just now. Could you rephrase it, or tell me which "
               "document you mean?")


def has_placeholders(text: str) -> bool:
    for m in _SLOT.finditer(_CODE.sub("", text or "")):
        inner = m.group(1)
        if inner is not None and not (inner[0].isupper() or _SLOT_WORD.search(inner)):
            continue
        return True
    return False


def guarded(text: str, regenerate=None) -> tuple[str, bool]:
    """(answer, changed). An answer with template slots is written again once; if the second try has them too (or
    there is nothing to write again with) it becomes SAFE_ANSWER."""
    if not has_placeholders(text):
        return text, False
    if regenerate:
        try:
            again = regenerate()
        except Exception:
            again = None
        if again and not has_placeholders(again):
            return again, True
    return SAFE_ANSWER, True
