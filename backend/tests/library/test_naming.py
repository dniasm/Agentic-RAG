import pytest

from backend.library import naming


def test_clean_title():
    assert naming.clean_title('  "Annual Report 2024.pdf" ') == "Annual Report 2024"
    assert naming.clean_title("Q1/Q2  results") == "Q1-Q2 results"
    with pytest.raises(naming.TitleError):
        naming.clean_title("  ..  ")
    with pytest.raises(naming.TitleError):
        naming.clean_title("x" * 200)


def test_file_names_are_safe_and_unique():
    assert naming.file_name_for('a: b?', ".pdf", set()) == "a- b-.pdf"
    assert naming.file_name_for("CON", ".txt", set()) == "CON_.txt"
    assert naming.file_name_for("Report", ".pdf", {"report.pdf", "report (2).pdf"}) == "Report (3).pdf"


def test_same_name_ignores_case_spacing_and_extension():
    assert naming.same_name("GDPR  Overview", "gdpr_overview.pdf")
    assert not naming.same_name("GDPR Overview", "GDPR Overview 2")


def test_name_similarity():
    assert naming.name_similarity("w2v.txt", "w2v.txt") == 1.0
    assert naming.name_similarity("the w2v file", "w2v.txt") == 1.0
    assert naming.name_similarity("Word2vec Neural Word Embedings", "Word2vec Neural Word Embeddings") >= 0.9  # typo
    assert naming.name_similarity("word2vec", "Word2vec Neural Word Embeddings") < 0.9                       # partial
    assert naming.name_similarity("budget", "Support Vector Machines") < 0.4



def test_the_real_title_writer_runs(monkeypatch):
    """conftest replaces titles._ask_model in every test; a fresh copy of the module runs the real one, so a broken
    import in it (as the deleted bridge once was) fails here instead of silently falling back to the filename."""
    import importlib.util

    from backend.config import llm
    from backend.library import titles
    spec = importlib.util.spec_from_file_location("titles_copy", titles.__file__)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)

    class Fake:
        def invoke(self, prompt, config=None):
            return fresh.DocProfile(title="Written By The Model", summary="s", keywords=[])
    monkeypatch.setattr(llm, "structured", lambda *a, **k: Fake())
    assert fresh._ask_model("prompt").title == "Written By The Model"
