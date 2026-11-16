"""Follow-ups to the document agent stay with it (from a resume chat): its plain question's answer ("anything you'd
like"), a message carrying on with the change it just made ("on second thought, regenerate it again"), and a
misspelled undo ("udno") went to model knowledge, which said it couldn't see or edit the file."""
from langchain_core.messages import AIMessage

from backend.graph import nodes
from backend.library import agent
from test_manage_route import ask, no_router, quiet_chat  # noqa: F401  (fixtures)

CHANGED = ['edited "asmin dani resume"']


class Says:
    """A model that answers in words only."""

    def __init__(self, text):
        self.text = text

    def invoke(self, messages):
        return AIMessage(self.text)


def test_a_question_the_agent_asks_in_words_waits_for_the_users_answer():
    agent.chat("fu-1", "update the professional profile of my cv",
               model=Says("What would you like the Professional Profile to say?"))
    assert agent.has_open_question("fu-1")
    agent.chat("fu-1", "anything you'd like", model=Says("Done."))
    assert not agent.has_open_question("fu-1")          # answered: the next message is routed as usual


def test_follow_ups_to_a_change_go_back_to_the_agent():
    for message in ["on second thought, regenerate it again", "make it shorter", "udno", "Undo it pls", "revrt",
                    "no no not like that, try again", "reword that", "give me a new version", "go back"]:
        assert nodes.manage_in_code({"query": message, "agent_changed": CHANGED}), message
    # looser wording ("okay generate a new professional profile within the bounds of the current one") is the
    # router's call, told what the previous answer changed (test_the_router_is_told_what_the_previous_answer_changed)


def test_other_messages_after_a_change_still_go_to_the_router():
    for message in ["what is the capital of France?", "thanks!", "explain support vector machines",
                    "under", "undone yet?", "write me a poem about the long voyage home again",
                    "which is longer, the Nile or the Amazon?", "generate a haiku about autumn",
                    "what does it say about kernels?", "can I use this in a cover letter?"]:
        assert nodes.manage_in_code({"query": message, "agent_changed": CHANGED}) is None, message
    assert nodes.manage_in_code({"query": "regenerate it again"}) is None    # no change just made


def test_the_router_is_told_what_the_previous_answer_changed():
    assert 'The previous answer edited "asmin dani resume"' in nodes._recent_change({"agent_changed": CHANGED})
    assert nodes._recent_change({}) == ""


def test_the_chat_passes_on_what_the_agents_answer_changed(signed_in, quiet_chat):
    from backend.api.routes import chat
    from backend.models import database as db
    conv = db.create_conversation(signed_in.user["id"], "c")
    req = chat.ChatRequest(message="regenerate it", conversation_id=conv["id"])
    db.add_message(conv["id"], "assistant", "Edited.", tool="manage_documents",
                   meta={"actions": [{"type": "edit", "doc_id": "d", "title": "CV"},
                                     {"type": "rename", "doc_id": "e", "old_title": "a", "new_title": "B"}]})
    assert chat._prepare(req, signed_in.user)[4]["agent_changed"] == ['edited "CV"', 'renamed "B"']
    db.add_message(conv["id"], "assistant", "An answer.", tool="rag")
    assert chat._prepare(req, signed_in.user)[4]["agent_changed"] == []


def test_a_follow_up_runs_the_agent_without_the_router(library_docs, no_router, monkeypatch):  # noqa: F811
    monkeypatch.setattr(agent, "default_model", lambda: Says("Which passage?"))
    state = ask("on second thought, regenerate it again", agent_changed=CHANGED)
    assert state["tool"] == "manage_documents" and state["intent"] == "manage"
