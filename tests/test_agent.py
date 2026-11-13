"""The rename guard, driven by a scripted model: each turn lists the tool calls the 'model' makes."""
import json

from langchain_core.messages import AIMessage

from renamer import agent, catalog


class Scripted:
    """Plays back one list of tool calls per model call, then a closing reply."""

    def __init__(self, *steps):
        self.steps = list(steps)

    def invoke(self, messages):
        if not self.steps:
            return AIMessage("ok")
        calls = self.steps.pop(0)
        return AIMessage("", tool_calls=[{"name": n, "args": a, "id": f"c{i}{n}"} for i, (n, a) in enumerate(calls)])


def turn(session_id, *steps):
    """One user message; returns (chat output, the tool results of this message in order)."""
    out = agent.chat(session_id, "user message", model=Scripted(*steps))
    messages = catalog.load_session(session_id)["llm_messages"]
    last_user = max(i for i, m in enumerate(messages) if m["type"] == "human")
    results = [json.loads(m["data"]["content"]) for m in messages[last_user:] if m["type"] == "tool"]
    return out, results


def test_unique_match_is_renamed(library_docs):
    doc_id = library_docs["svm.txt"]["id"]
    out, results = turn("s1", [("find_documents", {"description": "svm.txt"})],
                        [("rename_document", {"doc_id": doc_id, "new_title": "SVM Primer"})])
    assert results[-1]["status"] == "renamed"
    assert out["actions"] == [{"type": "rename", "doc_id": doc_id, "old_title": "Support Vector Machines",
                               "new_title": "SVM Primer"}]


def test_rename_without_finding_is_refused(library_docs):
    doc_id = library_docs["svm.txt"]["id"]
    _, results = turn("s2", [("rename_document", {"doc_id": doc_id, "new_title": "X"})])
    assert results[-1]["status"] == "needs_clarification"
    assert catalog.get_document(doc_id)["title"] == "Support Vector Machines"


def test_ambiguous_match_waits_for_the_user(library_docs):
    w2v = library_docs["w2v.txt"]["id"]
    _, results = turn("s3", [("find_documents", {"description": "word embeddings"})],
                      [("rename_document", {"doc_id": w2v, "new_title": "Embeddings A"})])
    assert results[0]["decision"] == "ambiguous"
    assert results[-1]["status"] == "needs_clarification"      # the model may not pick one itself

    # The model also can't get around it by searching again more narrowly in the same message.
    _, results = turn("s3b", [("find_documents", {"description": "word embeddings"})],
                      [("find_documents", {"description": "Mikolov skip-gram word embeddings Google"})],
                      [("rename_document", {"doc_id": w2v, "new_title": "Embeddings A"})])
    assert results[-1]["status"] == "needs_clarification"

    # Next message (the user said "the Word2vec one"): now it may.
    _, results = turn("s3", [("rename_document", {"doc_id": w2v, "new_title": "Embeddings A"})])
    assert results[-1]["status"] == "renamed"


def test_name_conflict_needs_confirmation_in_a_later_message(library_docs):
    glove = library_docs["glove.txt"]["id"]
    step = [("rename_document", {"doc_id": glove, "new_title": "support vector machines", "confirmed": True})]
    _, results = turn("s4", [("find_documents", {"description": "glove.txt"})], step)
    assert results[-1]["status"] == "needs_confirmation"       # confirmed=true in the same turn doesn't count
    assert results[-1]["conflicts_with"][0]["title"] == "Support Vector Machines"

    _, results = turn("s4", step)                               # the user said yes
    assert results[-1]["status"] == "renamed"
    assert catalog.get_document(glove)["file_name"] == "support vector machines (2).txt"


def test_confirmation_is_for_that_exact_name(library_docs):
    glove = library_docs["glove.txt"]["id"]
    turn("s5", [("find_documents", {"description": "glove.txt"})],
         [("rename_document", {"doc_id": glove, "new_title": "Support Vector Machines"})])
    _, results = turn("s5", [("rename_document", {"doc_id": glove, "new_title": "Word2vec Neural Word Embeddings",
                                                  "confirmed": True})])
    assert results[-1]["status"] == "needs_confirmation"


def test_bad_titles_and_unknown_ids(library_docs):
    svm = library_docs["svm.txt"]["id"]
    _, results = turn("s6", [("find_documents", {"description": "svm.txt"})],
                      [("rename_document", {"doc_id": svm, "new_title": "   "})],
                      [("rename_document", {"doc_id": "nope", "new_title": "A"})],
                      [("rename_document", {"doc_id": svm, "new_title": "Support Vector Machines"})])
    assert [r.get("status") for r in results[1:]] == ["error", "error", "unchanged"]


def test_session_keeps_the_conversation(library_docs):
    turn("s7", [("list_documents", {})])
    session = catalog.load_session("s7")
    assert session["turn"] == 1 and len(session["display"]) == 2
    assert any(m["type"] == "tool" for m in session["llm_messages"])


def test_listed_document_needs_a_find_in_the_same_message(library_docs):
    svm = library_docs["svm.txt"]["id"]
    _, results = turn("s8", [("list_documents", {})], [("rename_document", {"doc_id": svm, "new_title": "A"})])
    assert results[-1]["status"] == "needs_clarification" and "find_documents" in results[-1]["error"]
    _, results = turn("s8", [("rename_document", {"doc_id": svm, "new_title": "A"})])  # "rename the 4th one"
    assert results[-1]["status"] == "renamed"
