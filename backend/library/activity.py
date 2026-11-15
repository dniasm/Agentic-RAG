"""Which documents a conversation has changed: the documents its replies renamed, edited or undid (each reply keeps
what it did in meta.actions), newest first, with their current titles. A document the user has since deleted, or
one that isn't theirs, is left out."""
from backend.library import catalog

LAST_ACTION = {"rename": "renamed", "edit": "edited", "undo": "undone"}


def documents_changed_in(messages: list[dict], owner: str) -> list[dict]:
    latest: dict[str, dict] = {}   # doc_id -> the last thing done to it in this conversation
    for m in messages:
        for a in (m.get("meta") or {}).get("actions") or []:
            if a.get("doc_id") and a.get("type") in LAST_ACTION:
                latest[a["doc_id"]] = {"action": LAST_ACTION[a["type"]], "at": m.get("created_at") or ""}
    out = []
    for doc_id, last in latest.items():
        doc = catalog.get_document(doc_id)
        if doc and doc["owner"] == owner:
            out.append({"id": doc["id"], "title": doc["title"], "ext": doc["ext"], "status": doc["status"],
                        "pages": doc["pages"] or 0, **last,
                        # an Undo button applies only to this edit: the document's latest, from whichever chat
                        "latest_edit_id": (catalog.last_edit(doc_id) or {}).get("id")})
    return sorted(out, key=lambda d: d["at"], reverse=True)
