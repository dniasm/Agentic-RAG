"""Questions that point at a document instead of describing its content ("tell me about the document I just
uploaded"). Their words have nothing in common with the passages, so the relevance gate rejects the document. They
are recognised in code (no model) and resolved to the document's id, and retrieval reads that document directly.

Resolution order: documents uploaded in this conversation (newest first), then the user's most recent ready
upload, then a document named by its title or file name."""
import logging
import re

from backend.library import catalog

log = logging.getLogger(__name__)

_KIND = r"(?:documents?|docs?|files?|pdfs?|docx|papers?|reports?|uploads?|notes|attachments?)"
_UPLOADED = r"(?:uploaded|upload|sent|added|attached|shared|gave|provided|submitted)"
# "the document I just uploaded", "the file I've sent", "what I just uploaded", "the one I added"
_JUST_UPLOADED = re.compile(
    rf"\b(?:(?:the|this|that|my)\s+(?:\w+\s+)?{_KIND}|(?:what|the\s+one|the\s+thing)|everything)\s+"
    rf"(?:that\s+)?(?:i|we)(?:'ve|\s+have|\s+had)?\s+(?:just\s+|only\s+|now\s+|recently\s+|already\s+)?{_UPLOADED}\b"
    rf"|\b(?:just|recently|newly)[\s-]+{_UPLOADED}\s+{_KIND}\b"
    rf"|\bthe\s+{_KIND}\s+(?:i|we)\s+(?:just\s+)?{_UPLOADED}\b",
    re.I)
# "my latest upload", "the last document", "the most recent file I uploaded", "the newest document"
_LATEST = re.compile(rf"\b(?:my|the|our)\s+(?:latest|last|most\s+recent|newest|recent)\s+(?:\w+\s+)?{_KIND}\b", re.I)
# "this document", "that file", "the attached pdf"
_THIS = re.compile(rf"\b(?:this|that|the\s+attached|the\s+attached\s+and)\s+(?:\w+\s+)?{_KIND}\b", re.I)
# a change to a document is the document agent's, not a question about its content
_CHANGE = re.compile(r"\b(?:re-?name|re-?title|retitle|replace|append|undo|edit|delete|remove|export|download|"
                     r"convert|fix|update|queue|cancel|change\s+the\s+(?:name|title))\b", re.I)
MIN_NAME = 6   # shorter titles ("CV") match too many questions


def _terms(doc: dict) -> list[str]:
    """What a user may call the document: its title or its uploaded file name (not the bare stem: "nepal" is also a
    topic)."""
    names = [doc.get("title") or "", doc.get("original_name") or ""]
    return [n.strip().lower() for n in names if len(n.strip()) >= MIN_NAME]


def detect(query: str) -> str | None:
    """"just_uploaded", "latest" or "this" when the message points at a document by position; None otherwise."""
    if _CHANGE.search(query):
        return None
    if _JUST_UPLOADED.search(query):
        return "just_uploaded"
    if _LATEST.search(query):
        return "latest"
    if _THIS.search(query):
        return "this"
    return None


def named(query: str, docs: list[dict]) -> list[dict]:
    """The documents whose title or file name the message contains (the longest match first)."""
    text = query.lower()
    def found(term: str) -> bool:
        return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None
    hits = [(max(len(t) for t in terms), d) for d in docs if (terms := [t for t in _terms(d) if found(t)])]
    return [d for _, d in sorted(hits, key=lambda h: -h[0])]


def resolve(query: str, user_id: str, conversation_id: str | None) -> tuple[list[dict], str] | None:
    """([documents], why) the message points at, or None when it is an ordinary question. Only ready (indexed)
    documents count: a file still being indexed has nothing to read yet, and the newest upload is never swapped for an
    older one."""
    if _CHANGE.search(query):
        return None
    kind = detect(query)
    try:
        if kind:
            here = catalog.conversation_documents(user_id, conversation_id) if conversation_id else []
            if here:
                doc = here[0]
                return ([doc], f"the newest upload in this conversation: {doc['title']}") if doc["status"] == "indexed" \
                    else None
            if kind == "this":
                return None   # "this document" with nothing uploaded here: the router decides
            doc = catalog.latest_document(user_id, status=None)
            if doc and doc["status"] == "indexed":
                return [doc], f"the most recent upload: {doc['title']}"
            return None
        docs = catalog.list_documents(status="indexed", owner=user_id)
    except Exception as exc:   # the catalog being down must not break a chat: the normal path takes over
        log.warning("Document reference lookup failed: %s", exc)
        return None
    if kind is None:
        found = named(query, docs)
        if found:
            return found[:3], "named by title or file name: " + ", ".join(d["title"] for d in found[:3])
    return None
