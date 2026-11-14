"""Fewer Groq tokens: shorter history, questions written in code, no summary refresh for small edits."""
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from renamer import agent, config, editing, titles
from tests.test_agent import turn
from tests.test_pdf_edit import CROSS, pdf_doc  # noqa: F401  (fixture)


class Counting:
    """Plays the steps and counts the model calls; a call past the steps is an error."""

    def __init__(self, *steps):
        self.steps, self.calls = list(steps), 0

    def invoke(self, messages):
        self.calls += 1
        assert self.steps, "no closing model call was expected"
        calls = self.steps.pop(0)
        return AIMessage("", tool_calls=[{"name": n, "args": a, "id": f"c{i}{n}"} for i, (n, a) in enumerate(calls)])


# ---------------------------------------------------------------- history
def call(name, i):
    return AIMessage("", tool_calls=[{"name": name, "args": {}, "id": f"t{i}"}])


def test_older_exchanges_lose_their_tool_calls_but_the_last_keeps_them():
    history = [HumanMessage("rename the svm one"), call("find_documents", 1),
               ToolMessage('{"candidates": "' + "x" * 2000 + '"}', tool_call_id="t1"), AIMessage("Renamed it."),
               HumanMessage("and the glove one?"), call("find_documents", 2),
               ToolMessage('{"decision": "ambiguous"}', tool_call_id="t2"), AIMessage("Which one?")]
    out = agent._shorten(history)
    assert [type(m).__name__ for m in out] == ["HumanMessage", "AIMessage",
                                               "HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
    assert out[1].content == "Renamed it." and out[3].tool_calls     # old: what was said; last: everything


# ---------------------------------------------------------------- questions written in code
def test_a_name_conflict_question_needs_no_closing_model_call(library_docs):
    svm = library_docs["svm.txt"]["id"]
    model = Counting([("find_documents", {"description": "svm.txt"})],
                     [("rename_document", {"doc_id": svm, "new_title": "GloVe Word Embeddings"})])
    out = agent.chat("o1", "rename svm to GloVe Word Embeddings", model=model)
    assert model.calls == 2
    assert 'Another document is already called "GloVe Word Embeddings"' in out["reply"]
    assert "anyway, or use a different name?" in out["reply"]
    model = Counting([("rename_document", {"doc_id": svm, "new_title": "GloVe Word Embeddings", "confirmed": True})])
    out = agent.chat("o1", "yes", model=model)                      # the answer still works in the next message
    assert out["actions"][0]["new_title"] == "GloVe Word Embeddings"


def test_a_pdf_question_needs_no_closing_model_call(pdf_doc):  # noqa: F811
    model = Counting([("find_documents", {"description": "budget.pdf"})],
                     [("edit_document", {"doc_id": pdf_doc["id"], "operation": "replace", **CROSS})])
    out = agent.chat("o2", "change March to June in the budget", model=model)
    assert model.calls == 2 and "is a PDF" in out["reply"] and "Shall I do that?" in out["reply"]


# ---------------------------------------------------------------- summary refresh
def test_changed_share():
    assert editing.changed_share("abc", "abc") == 0
    assert editing.changed_share("a" * 95 + "xxxxx", "a" * 95 + "yyyyy") == 0.05
    assert editing.changed_share("", "new text") == 1


def refreshes(monkeypatch):
    seen = []
    real = titles.profile_document

    def counting(*a, **k):
        seen.append(1)
        return real(*a, **k)
    monkeypatch.setattr(titles, "profile_document", counting)
    return seen


def test_a_small_edit_and_its_undo_keep_the_summary(library_docs, monkeypatch):
    doc = library_docs["svm.txt"]
    editing.edit(doc["id"], "append", text="Kernels map the data into a higher dimensional space. " * 30)
    seen = refreshes(monkeypatch)                                   # a document of a realistic length from here on
    editing.edit(doc["id"], "replace", find="classifier", text="model")
    editing.undo(doc["id"])
    assert seen == []                                               # no model call: about 4,400 tokens saved


def test_a_large_edit_refreshes_the_summary(library_docs, monkeypatch):
    seen = refreshes(monkeypatch)
    doc = library_docs["svm.txt"]
    editing.edit(doc["id"], "append", text="Zebrafish are striped fish from South Asia. " * 30)
    assert seen == [1]
    assert config.SUMMARY_REFRESH_SHARE == 0.05


# ---------------------------------------------------------------- the fixed prompt
def test_the_fixed_prompt_stays_small():
    import json

    import tiktoken
    enc = tiktoken.get_encoding("o200k_base")
    size = len(enc.encode(agent.SYSTEM)) + sum(len(enc.encode(json.dumps(t))) for t in agent.TOOLS)
    assert size <= 1150, f"system prompt + tools is {size} tokens, sent with every model call"


def test_rare_case_rules_come_with_the_results_that_need_them(library_docs):
    _, results = turn("o3", [("find_documents", {"description": "word embeddings"})])
    assert results[-1]["decision"] == "ambiguous" and "don't choose" in results[-1]["next"]
    _, results = turn("o4", [("find_documents", {"description": "svm.txt"})])
    assert results[-1]["decision"] == "unique" and "next" not in results[-1]       # nothing extra when not needed
    w2v = library_docs["w2v.txt"]["id"]
    turn("o5", [("find_documents", {"description": "w2v.txt"})])
    _, results = turn("o5", [("edit_document", {"doc_id": w2v, "operation": "replace", "find": "word", "text": "term"})])
    assert results[-1]["status"] == "ambiguous" and "several places" in results[-1]["next"]


# ---------------------------------------------------------------- the queue's reservation
def test_the_queue_reserves_refresh_room_only_for_larger_edits(library_docs):
    from renamer import batch
    editing.edit(library_docs["svm.txt"]["id"], "append", text="Kernels map data into higher dimensions. " * 30)
    small = {"action": "replace", "document": "Support Vector Machines", "find": "classifier", "text": "model"}
    large = {"action": "append", "document": "Support Vector Machines", "text": "A long new section. " * 40}
    assert not batch._refresh_expected(small) and batch._refresh_expected(large)
    assert not batch._refresh_expected({"action": "rename", "document": "x", "new_title": "y"})
    assert batch._refresh_expected({**small, "document": "a title nobody has"})   # unsure: reserve


# ---------------------------------------------------------------- Voyage and the find cache
def counting_rerank(monkeypatch):
    from renamer.day7 import rerank
    calls, real = [], rerank.score

    def score(query, hits):
        calls.append(query)
        return real(query, hits)
    monkeypatch.setattr(rerank, "score", score)
    return calls


def test_a_repeated_search_is_answered_from_the_cache_until_the_library_changes(library_docs, monkeypatch):
    from renamer import library
    calls = counting_rerank(monkeypatch)
    first = library.find("word embeddings")
    assert library.find("Word   Embeddings") == first and len(calls) == 1      # same words: no second rerank
    library.rename(library_docs["glove.txt"]["id"], "Global Vectors")
    library.find("word embeddings")
    assert len(calls) == 2                                                     # the library changed: searched again


def test_past_voyages_minute_find_uses_cosine_without_calling_it(library_docs, monkeypatch):
    from renamer import library
    monkeypatch.setattr(library, "_VOYAGE", library.limits.TokenWindow(3, 60.0))
    calls = counting_rerank(monkeypatch)
    for q in ("word embeddings", "data protection fines", "margin classifier"):
        assert library.find(q)["scorer"] != "cosine"
    result = library.find("neural networks for words")
    assert len(calls) == 3 and result["scorer"] == "cosine"                   # the fourth didn't try Voyage


class Checked(Counting):
    """A model that also answers the 'anything left?' check, from a script of answers."""

    def __init__(self, *steps, left=()):
        super().__init__(*steps)
        self.left, self.asked = list(left), []

    def anything_left(self, message, done):
        self.asked.append([r["status"] for r in done])
        return self.left.pop(0), 200


def test_changes_done_one_at_a_time_all_get_done(library_docs):
    """The model renames one document per step: the turn may not end after the first."""
    svm, glove = library_docs["svm.txt"]["id"], library_docs["glove.txt"]["id"]
    turn("o6", [("find_documents", {"description": "svm.txt"})])
    turn("o6", [("find_documents", {"description": "glove.txt"})])
    model = Checked([("rename_document", {"doc_id": svm, "new_title": "SVM Primer"})],
                    [("rename_document", {"doc_id": glove, "new_title": "GloVe Primer"})], left=[True, False])
    out = agent.chat("o6", "rename both", model=model)
    assert model.calls == 2 and model.asked == [["renamed"], ["renamed", "renamed"]]
    assert [a["new_title"] for a in out["actions"]] == ["SVM Primer", "GloVe Primer"]
    assert "SVM Primer" in out["reply"] and "GloVe Primer" in out["reply"]   # one reply covering both
    assert out["tokens"] == 400                                              # just the two checks (fakes use 0)


def test_a_question_ends_the_turn_without_a_check(library_docs):
    svm = library_docs["svm.txt"]["id"]
    model = Checked([("find_documents", {"description": "svm.txt"})],
                    [("rename_document", {"doc_id": svm, "new_title": "GloVe Word Embeddings"})])
    out = agent.chat("o7", "rename svm to GloVe Word Embeddings", model=model)
    assert model.asked == [] and "anyway, or use a different name?" in out["reply"]
