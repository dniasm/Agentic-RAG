"""The chat's side panel: an edit undone with the Undo button shows as undone (no reply records that undo), and
documents a queued task changed show once the task ran, with a count of tasks still to run so the panel looks again."""
from backend.library import catalog, editing
from backend.models import database as db


def _edit(doc, text):
    editing.edit(doc["id"], "append", text=text)
    return catalog.last_edit(doc["id"])["id"]


def test_an_edit_undone_with_the_button_shows_as_undone(library_docs, signed_in):
    me, doc = signed_in.user["id"], library_docs["svm.txt"]
    chat = db.create_conversation(me, "A")
    edit_id = _edit(doc, "An added line.")
    db.add_message(chat["id"], "assistant", "Edited.", tool="manage_documents",
                   meta={"actions": [{"type": "edit", "doc_id": doc["id"], "edit_id": edit_id}]})
    url = f"/api/conversations/{chat['id']}/documents"
    assert [d["action"] for d in signed_in.get(url).json()] == ["edited"]
    assert signed_in.post(f"/api/library/documents/{doc['id']}/undo", json={"edit_id": edit_id}).status_code == 200
    assert [d["action"] for d in signed_in.get(url).json()] == ["undone"]


def test_documents_a_queued_task_changed_show_in_its_chat(library_docs, signed_in):
    me, svm, glove = signed_in.user["id"], library_docs["svm.txt"], library_docs["glove.txt"]
    chat, other = db.create_conversation(me, "A"), db.create_conversation(me, "B")
    db.add_message(chat["id"], "assistant", "Queued.", tool="manage_documents",
                   meta={"actions": [{"type": "queue", "batch_id": "x", "tasks": 2}]})
    batch = catalog.add_batch(chat["id"], ["rename svm", "rename glove"], owner=me)
    first, second = batch["tasks"]
    pending = f"/api/conversations/{chat['id']}/pending"
    assert signed_in.get(pending).json() == {"tasks": 2}

    catalog.update_task(first["id"], status="done", finished_at=catalog.now(),
                        actions=[{"type": "rename", "doc_id": svm["id"]}])
    url = f"/api/conversations/{chat['id']}/documents"
    assert [(d["id"], d["action"]) for d in signed_in.get(url).json()] == [(svm["id"], "renamed")]
    assert signed_in.get(pending).json() == {"tasks": 1}

    catalog.update_task(second["id"], status="done", finished_at="2999-01-01T00:00:00+00:00",
                        actions=[{"type": "rename", "doc_id": glove["id"]}])
    assert [d["id"] for d in signed_in.get(url).json()] == [glove["id"], svm["id"]]      # newest first
    assert signed_in.get(pending).json() == {"tasks": 0}
    assert signed_in.get(f"/api/conversations/{other['id']}/documents").json() == []   # only the chat that queued
    assert signed_in.get("/api/conversations/nope/pending").status_code == 404
