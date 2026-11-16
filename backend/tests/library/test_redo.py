"""Undo and redo work like a browser's Back and Forward: an undone edit can be brought back (under its own id, so
its buttons apply again), a new edit ends what can be brought back, and a misspelled undo is asked about first."""
from langchain_core.messages import AIMessage

from backend.graph import nodes
from backend.library import activity, agent, catalog, config, editing
from backend.models import database as db
from test_agent import Scripted
from test_manage_route import Recording, no_router  # noqa: F401  (fixture)


def _text(doc):
    return (config.LIBRARY_DIR / catalog.get_document(doc["id"])["file_name"]).read_text(encoding="utf-8")


def _edit(doc, text):
    editing.edit(doc["id"], "append", text=text)
    return catalog.last_edit(doc["id"])["id"]


def test_an_undone_edit_comes_back_with_redo_and_can_be_undone_again(library_docs):
    doc = library_docs["svm.txt"]
    original = _text(doc)
    edit_id = _edit(doc, "A line added.")
    edited = _text(doc)
    assert editing.undo(doc["id"], edit_id)["redo_id"] == edit_id and _text(doc) == original
    assert catalog.last_undone(doc["id"])["id"] == edit_id
    assert editing.redo(doc["id"], edit_id)["edit_id"] == edit_id
    assert _text(doc) == edited and catalog.last_edit(doc["id"])["id"] == edit_id   # the same edit, same id
    assert catalog.last_undone(doc["id"]) is None
    editing.undo(doc["id"], edit_id)                                  # Back again: the backup was kept
    assert _text(doc) == original


def test_redo_steps_forward_one_edit_at_a_time(library_docs):
    doc = library_docs["svm.txt"]
    first, second = _edit(doc, "First."), _edit(doc, "Second.")
    with_both = _text(doc)
    editing.undo(doc["id"], second)
    editing.undo(doc["id"], first)
    try:
        editing.redo(doc["id"], second)                               # first comes back first
        raise AssertionError("redo of the wrong edit was allowed")
    except editing.EditError:
        pass
    editing.redo(doc["id"], first)
    editing.redo(doc["id"], second)
    assert _text(doc) == with_both


def test_a_new_edit_ends_what_redo_can_bring_back(library_docs):
    doc = library_docs["svm.txt"]
    edit_id = _edit(doc, "Old addition.")
    editing.undo(doc["id"], edit_id)
    saved = list((editing._versions_dir(doc["id"])).glob("redo-*"))
    assert saved
    _edit(doc, "New addition.")
    assert catalog.last_undone(doc["id"]) is None and not any(p.exists() for p in saved)
    try:
        editing.redo(doc["id"])
        raise AssertionError("redo after a new edit was allowed")
    except editing.EditError as e:
        assert "nothing to redo" in str(e)


def test_the_redo_button_and_the_panel(library_docs, signed_in):
    me, doc = signed_in.user["id"], library_docs["svm.txt"]
    chat = db.create_conversation(me, "A")
    edit_id = _edit(doc, "An added line.")
    db.add_message(chat["id"], "assistant", "Edited.", tool="manage_documents",
                   meta={"actions": [{"type": "edit", "doc_id": doc["id"], "edit_id": edit_id}]})
    panel = f"/api/conversations/{chat['id']}/documents"
    base = f"/api/library/documents/{doc['id']}"
    assert signed_in.post(f"{base}/redo", json={"edit_id": edit_id}).status_code == 409    # nothing undone yet
    assert signed_in.post(f"{base}/undo", json={"edit_id": edit_id}).status_code == 200
    row = signed_in.get(panel).json()[0]
    assert (row["action"], row["redo_edit_id"], row["latest_edit_id"]) == ("undone", edit_id, None)
    assert signed_in.post(f"{base}/redo", json={"edit_id": edit_id}).status_code == 200
    row = signed_in.get(panel).json()[0]
    assert (row["action"], row["redo_edit_id"], row["latest_edit_id"]) == ("edited", None, edit_id)


def test_an_undo_in_the_chat_shows_as_redone_after_the_redo_button(library_docs):
    doc = library_docs["svm.txt"]
    edit_id = _edit(doc, "Line.")
    editing.undo(doc["id"], edit_id)
    messages = [{"created_at": "2026-11-16T10:00:00+00:00",
                 "meta": {"actions": [{"type": "undo", "doc_id": doc["id"], "edit_id": edit_id}]}}]
    assert activity.documents_changed_in(messages, doc["owner"])[0]["action"] == "undone"
    editing.redo(doc["id"], edit_id)
    assert activity.documents_changed_in(messages, doc["owner"])[0]["action"] == "redone"


def test_the_agent_redoes_with_undo_edit(library_docs):
    doc = library_docs["svm.txt"]
    edit_id = _edit(doc, "Line.")
    editing.undo(doc["id"], edit_id)
    out = agent.chat("redo-1", "redo", model=Scripted([("find_documents", {"description": "svm.txt"})],
                                                       [("undo_edit", {"doc_id": doc["id"], "redo": True})]))
    assert out["actions"] == [{"type": "redo", "doc_id": doc["id"], "title": doc["title"],
                               "redid": catalog.last_edit(doc["id"])["detail"], "edit_id": edit_id}]
    assert "Brought back the edit" in out["reply"]


def test_a_misspelled_undo_is_asked_about_and_a_yes_undoes(library_docs, no_router, monkeypatch):  # noqa: F811
    doc = library_docs["svm.txt"]
    edit_id = _edit(doc, "Line.")
    changed = [f'edited "{doc["title"]}"']
    state = nodes.manage_node({"query": "udno", "conversation_id": "typo-1", "user_id": config.OWNER,
                               "agent_changed": changed})
    assert state["answer"].startswith('Did you mean "undo"') and doc["title"] in state["answer"]
    assert catalog.last_edit(doc["id"])["id"] == edit_id                      # nothing undone yet
    assert agent.has_open_question("typo-1")                                  # the yes goes back to the agent
    model = Recording([("find_documents", {"description": "svm.txt"})], [("undo_edit", {"doc_id": doc["id"]})])
    monkeypatch.setattr(agent, "default_model", lambda: model)
    nodes.manage_node({"query": "yes", "conversation_id": "typo-1", "user_id": config.OWNER})
    assert "Yes: undo the last change." in model.prompts[0][-1].content
    assert catalog.last_edit(doc["id"]) is None


def test_a_correctly_spelled_undo_is_not_asked_about():
    assert nodes.manage_in_code({"query": "undo", "agent_changed": ['edited "x"']})
    assert nodes._BARE_UNDO.match("redo") and nodes._BARE_UNDO.match("undo")
    assert not (nodes._undo_typo("undo") and not nodes._BARE_UNDO.match("undo"))    # manage_node's typo test


def test_saying_no_to_did_you_mean_undo_changes_nothing(library_docs, monkeypatch):
    doc = library_docs["svm.txt"]
    edit_id = _edit(doc, "Line.")
    agent.confirm_undo("typo-2", "udno", doc["title"])

    class Says:
        def invoke(self, messages):
            assert "undo the last change" not in messages[-1].content
            return AIMessage("Okay, nothing undone.")
    agent.chat("typo-2", "no", model=Says())
    assert catalog.last_edit(doc["id"])["id"] == edit_id
