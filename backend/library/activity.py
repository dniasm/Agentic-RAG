"""Which documents a conversation has changed: the documents its replies renamed, edited or undid (each reply keeps
what it did in meta.actions), and those its queued tasks changed (each task keeps its actions too), newest first,
with their current titles. A document the user has since deleted, or one that isn't theirs, is left out.

An edit undone outside the chat (the Undo button calls the API directly, and no reply records it) shows as undone:
an undo deletes the edit's record, so an edit whose record is gone was undone."""
from datetime import datetime, timezone

from backend.library import catalog

LAST_ACTION = {"rename": "renamed", "edit": "edited", "undo": "undone"}
_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def _when(at: str | None) -> datetime:
    """A stored time (chat messages and tasks write ISO times with different precision) to compare by."""
    try:
        t = datetime.fromisoformat(at or "")
    except ValueError:
        return _OLDEST
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def documents_changed_in(messages: list[dict], owner: str, tasks: list[dict] = ()) -> list[dict]:
    """messages: the chat's replies ({"meta": {"actions"}, "created_at"}); tasks: its queued tasks that ran
    ({"actions", "finished_at"})."""
    happened = [(m.get("created_at") or "", (m.get("meta") or {}).get("actions") or []) for m in messages]
    happened += [(t.get("finished_at") or "", t.get("actions") or []) for t in tasks]
    happened.sort(key=lambda h: _when(h[0]))
    latest: dict[str, dict] = {}   # doc_id -> the last thing done to it in this conversation
    for at, actions in happened:
        for a in actions:
            if a.get("doc_id") and a.get("type") in LAST_ACTION:
                latest[a["doc_id"]] = {"action": LAST_ACTION[a["type"]], "at": at, "edit_id": a.get("edit_id")}
    out = []
    for doc_id, last in latest.items():
        doc = catalog.get_document(doc_id)
        if not doc or doc["owner"] != owner:
            continue
        edit_id = last.pop("edit_id")
        if last["action"] == "edited" and edit_id is not None and not catalog.edit_exists(edit_id):
            last["action"] = "undone"
        out.append({"id": doc["id"], "title": doc["title"], "ext": doc["ext"], "status": doc["status"],
                    "pages": doc["pages"] or 0, **last,
                    # an Undo button applies only to this edit: the document's latest, from whichever chat
                    "latest_edit_id": (catalog.last_edit(doc_id) or {}).get("id")})
    return sorted(out, key=lambda d: _when(d["at"]), reverse=True)
