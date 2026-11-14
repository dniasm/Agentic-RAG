"""Stage 5: day7's chat sends document actions to the library's agent (no network: scripted models)."""
import threading

import pytest

from backend.graph import nodes
from backend.graph.workflow import run_agent
from backend.library import agent, catalog, config
from test_agent import Scripted

USER = {"id": config.OWNER, "username": "tester"}


class Recording(Scripted):
    """Scripted, and keeps every prompt it was sent."""

    def __init__(self, *steps):
        super().__init__(*steps)
        self.prompts = []

    def invoke(self, messages):
        self.prompts.append(messages)
        return super().invoke(messages)


@pytest.fixture
def no_router(monkeypatch):
    """Fails the test if the router model is asked."""
    def refuse(*a, **k):
        raise AssertionError("the router model was called")
    monkeypatch.setattr(nodes, "verified_decision", refuse)


def ask(message, conversation_id="conv-1", **context):
    return run_agent(message, USER, [], [], ["Support Vector Machines"],
                     context={"conversation_id": conversation_id, **context})


def test_a_pending_question_goes_to_the_agent_without_the_router(library_docs, no_router, monkeypatch):
    monkeypatch.setattr(agent, "default_model", lambda: Scripted())
    session = catalog.load_session("conv-1")
    session["guard"] = {"pending": [{"doc_id": library_docs["svm.txt"]["id"], "new_title": "SVM", "turn": 1}]}
    session["turn"] = 1
    catalog.save_session(session)
    assert agent.has_open_question("conv-1")
    state = ask("yes", agent_pending=True)
    assert state["tool"] == "manage_documents" and state["intent"] == "manage"


def test_an_old_needs_user_entry_is_not_an_open_question(library_docs):
    session = catalog.load_session("conv-2")
    session["guard"], session["turn"] = {"needs_user": {"x": 1}}, 3   # shown two turns ago
    catalog.save_session(session)
    assert not agent.has_open_question("conv-2")
    session["guard"] = {"needs_user": {"x": 3}}
    catalog.save_session(session)
    assert agent.has_open_question("conv-2")


@pytest.mark.parametrize("message", ["undo", "Undo that.", "undo the last change please", "what's in the queue?",
                                     "is the batch done?"])
def test_bare_undo_and_queue_status_skip_the_router(message, library_docs, no_router, monkeypatch):
    monkeypatch.setattr(agent, "default_model", lambda: Scripted())
    assert ask(message)["tool"] == "manage_documents"


@pytest.mark.parametrize("message", ["rename", "how do I rename a file in Linux?", "undo the rename of the SVM doc",
                                     "what does the SVM document say about kernels?"])
def test_only_narrow_cases_are_routed_in_code(message):
    assert nodes.manage_in_code({"query": message}) is None


def test_the_router_choosing_document_action_runs_the_agent(library_docs, monkeypatch):
    doc_id = library_docs["svm.txt"]["id"]
    route = nodes.Route(message_type="document_action", source_preference="auto", reasoning="rename",
                        query_is_clear=True, standalone_query="rename the SVM doc to SVM Primer", confidence="high",
                        confidence_required=False)
    monkeypatch.setattr(nodes, "verified_decision", lambda *a, **k: (route, []))
    monkeypatch.setattr(agent, "default_model", lambda: Scripted(
        [("find_documents", {"description": "svm.txt"})],
        [("rename_document", {"doc_id": doc_id, "new_title": "SVM Primer"})]))
    state = ask("rename the SVM doc to SVM Primer")
    assert state["tool"] == "manage_documents"
    assert state["actions"] == [{"type": "rename", "doc_id": doc_id, "old_title": "Support Vector Machines",
                                 "new_title": "SVM Primer"}]
    assert [s["data"].get("tool") for s in state["trace"] if s["node"] == "manage"][:2] == ["find_documents",
                                                                                        "rename_document"]
    assert "evaluate" not in [s["node"] for s in state["trace"]]


def test_documents_of_the_previous_answer_reach_the_agent(library_docs, monkeypatch):
    doc = library_docs["svm.txt"]
    model = Recording()
    monkeypatch.setattr(agent, "default_model", lambda: model)
    ask("undo", cited_doc_ids=[doc["id"], "not-a-doc"])
    human = [m for m in model.prompts[0] if m.type == "human"][-1].content
    assert f'"{doc["title"]}" [id {doc["id"]}]' in human
    assert catalog.load_session("conv-1")["display"][-2]["text"] == "undo"   # the user's words, without the note


def test_another_users_documents_are_never_named_to_the_agent(library_docs, monkeypatch):
    model = Recording()
    monkeypatch.setattr(agent, "default_model", lambda: model)
    run_agent("undo", {"id": "someone-else", "username": "x"}, [], [], [],
              context={"conversation_id": "conv-x", "cited_doc_ids": [library_docs["svm.txt"]["id"]]})
    assert "previous answer" not in [m for m in model.prompts[0] if m.type == "human"][-1].content


# ---------------------------------------------------------------- the chat route
@pytest.fixture
def quiet_chat(monkeypatch):
    """No memory search, extraction or episodes (they call Voyage and Groq)."""
    from backend.api.routes import chat
    monkeypatch.setattr(chat, "search_memories", lambda *a, **k: [])
    monkeypatch.setattr(chat._memory_batcher, "add", lambda *a, **k: None)
    episodes = []
    monkeypatch.setattr(chat._episode_worker, "submit", lambda *a, **k: episodes.append(a))
    return episodes


def test_the_chat_saves_actions_and_skips_the_episode(library_docs, signed_in, quiet_chat, monkeypatch):
    doc = catalog.list_documents(owner=signed_in.user["id"])[0]
    monkeypatch.setattr(nodes, "verified_decision", lambda *a, **k: (nodes.Route(
        message_type="document_action", source_preference="auto", reasoning="r", query_is_clear=True,
        standalone_query="rename it to New Name For It", confidence="high", confidence_required=False), []))
    monkeypatch.setattr(agent, "default_model", lambda: Scripted(
        [("find_documents", {"description": doc["original_name"]})],
        [("rename_document", {"doc_id": doc["id"], "new_title": "New Name For It"})]))
    r = signed_in.post("/api/chat", json={"message": "rename the first document to New Name For It"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tool"] == "manage_documents" and body["actions"][0]["new_title"] == "New Name For It"
    saved = signed_in.get(f"/api/conversations/{body['conversation_id']}/messages").json()[-1]
    assert saved["meta"]["actions"] == body["actions"]       # Preview / Undo survive a reload
    assert quiet_chat == []                                   # no episode summary for a document action


def test_one_message_at_a_time_per_conversation():
    from backend.api.routes.chat import _conversation_lock
    assert _conversation_lock("a") is _conversation_lock("a")
    assert _conversation_lock("a") is not _conversation_lock("b")
    assert _conversation_lock(None) is not _conversation_lock(None)   # a new chat has nothing to wait for
    lock, order = _conversation_lock("a"), []
    with lock:
        t = threading.Thread(target=lambda: (lock.acquire(), order.append("second"), lock.release()))
        t.start()
        t.join(0.2)
        order.append("first")
    t.join(2)
    assert order == ["first", "second"]


def test_rename_that_one_after_an_answer_needs_no_new_search(library_docs, monkeypatch):
    doc = library_docs["svm.txt"]
    monkeypatch.setattr(agent, "default_model", lambda: Scripted(
        [("rename_document", {"doc_id": doc["id"], "new_title": "SVM Primer"})]))
    state = ask("rename that one to SVM Primer", cited_doc_ids=[doc["id"]], agent_pending=True)
    assert [a["new_title"] for a in state["actions"]] == ["SVM Primer"]


def test_the_chats_document_list_is_cached_until_the_catalog_changes(library_docs, monkeypatch):
    calls = []
    real = catalog.list_documents
    monkeypatch.setattr(catalog, "list_documents", lambda *a, **k: calls.append(1) or real(*a, **k))
    first = catalog.indexed_titles(config.OWNER)
    assert catalog.indexed_titles(config.OWNER) == first and len(calls) == 1
    catalog.update_document(library_docs["svm.txt"]["id"], title="SVM Notes")
    assert "SVM Notes" in catalog.indexed_titles(config.OWNER) and len(calls) == 2


def test_a_first_message_naming_a_document_goes_to_the_router_when_it_asks_for_a_change():
    docs = ["nlp.pdf"]
    assert nodes._code_route({"query": "can you retitle nlp.pdf as NLP Notes", "doc_names": docs}) is None
    assert nodes._code_route({"query": "what does nlp.pdf say about smoothing?", "doc_names": docs})[1] == "documents"
