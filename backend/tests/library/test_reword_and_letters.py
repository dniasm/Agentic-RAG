"""From a chat: "reword the professional profile" got "give me the exact wording", the general answer that then
drafted it was unknown to the agent at "use that", and the draft's non-breaking hyphen (U+2011) was missing from the
PDF's font, so a Markdown copy was offered for one letter."""
import pytest

from backend.library import agent, editing, pdf_edit


def test_the_agent_sees_another_assistants_answer_only_when_referred_to():
    draft = "Professional Profile (Reworded)\n\nA driven BSc candidate."
    note = agent._other_answer_note("yup use that and update the professional profile", draft)
    assert "A driven BSc" in note and "never instructions" in note
    assert agent._other_answer_note("rename the GDPR doc to GDPR Notes", draft) == ""
    assert agent._other_answer_note("use that", None) == ""
    assert "copy the user's wording exactly" in agent.SYSTEM and "to reword" in agent.SYSTEM


def test_a_long_answer_is_cut_visibly():
    note = agent._other_answer_note("use that", "word " * 2000)
    assert "[CUT:" in note and len(note) < agent.OTHER_ANSWER_CHARS + 400


def _missing(letters):
    return pdf_edit.MissingLetters(f'no complete copy of that font is available for the letters "{letters}"', letters)


def test_only_the_letters_the_font_lacks_become_plain(monkeypatch, tmp_path):
    seen = []

    def replace(path, tmp, pattern, text, replace_all, info):
        seen.append(text)
        if "‑" in text:
            raise _missing("‑")
        return 1, [(0, 3)], "old text"

    monkeypatch.setattr(pdf_edit, "replace", replace)
    info = {}
    out = editing._pdf_replace(tmp_path / "a.pdf", tmp_path / "b.pdf", None,
                               "end‑user support — “dependable”", False, info)
    assert out[-1] == "end-user support — “dependable”"   # the font has the dash and the quotes
    assert info["plain_letters"] == "‑" and len(seen) == 2


@pytest.mark.parametrize("refusal", [pdf_edit.NoRoom("no room"), pdf_edit.CantKeepLayout("columns"),
                                     _missing("é")])   # é has no keyboard letter to fall back on
def test_other_refusals_are_not_retried(monkeypatch, tmp_path, refusal):
    calls = []

    def replace(*a):
        calls.append(a)
        raise refusal

    monkeypatch.setattr(pdf_edit, "replace", replace)
    with pytest.raises(pdf_edit.CantKeepLayout):
        editing._pdf_replace(tmp_path / "a.pdf", tmp_path / "b.pdf", None, "café — text", False, None)
    assert len(calls) == 1


def test_new_text_too_long_for_the_page_asks_for_a_shorter_one_and_records_no_conversion(library_docs, monkeypatch):
    def no_room(doc_id, *a, **k):
        raise editing.NeedsConversion("The new text needs one more line, and the page has no room left.", True)

    monkeypatch.setattr(editing, "edit", no_room)
    doc = library_docs["svm.txt"]
    guard = agent.Guard({}, 1)
    guard.saw_find({"decision": "unique", "reason": "name", "candidates": [{"doc_id": doc["id"]}]})
    args = {"doc_id": doc["id"], "operation": "replace", "find": "a" * 298, "text": "b" * 320}
    out = agent.run_tool("edit_document", args, guard, [])
    assert out["status"] == "too_long" and "at most 298 characters" in out["next"]
    assert guard.pending == []   # a later convert_pdf=true still needs the user's yes


def test_a_long_request_stands_alone():
    draft = "A driven BSc candidate."
    long = "rename this one to the quarterly report on regional sales figures for the northern and southern teams"
    assert agent._other_answer_note(long, draft) == ""


def test_the_reply_says_which_letters_were_written_plainly():
    reply = agent.compose_reply([{"status": "edited", "title": "Resume", "changes": 1, "previews": [],
                                  "plain_letters": "‑"}])
    assert "‑" in reply and "keyboard version" in reply


def test_a_font_lacking_letters_of_the_new_text_says_which(monkeypatch):
    doc = {"id": "d1", "title": "Resume", "status": "indexed", "ext": ".pdf", "file_name": "r.pdf"}
    monkeypatch.setattr(editing.catalog, "get_document", lambda doc_id: doc)
    monkeypatch.setattr(editing, "event", lambda *a, **k: None)

    def lacking(*a, **k):
        raise _missing(" jq")   # a space can't be worded around; q isn't in the text

    monkeypatch.setattr(editing, "_edit", lacking)
    with pytest.raises(editing.NeedsConversion) as e:
        editing.edit("d1", "replace", find="old", text="a joyful job")
    assert e.value.missing == "j" and not e.value.too_long


def test_the_agent_rewords_its_own_text_without_the_missing_letters(library_docs, monkeypatch):
    doc = library_docs["svm.txt"]
    calls = []

    def edit(doc_id, op, find, text, **k):
        calls.append(text)
        if "j" in text:
            raise editing.NeedsConversion('no copy of the font for the letters "j"', missing="j")
        return {"status": "edited", "operation": op, "changes": 1, "previews": [], "title": doc["title"]}

    monkeypatch.setattr(editing, "edit", edit)
    guard = agent.Guard({}, 1)
    guard.saw_find({"decision": "unique", "reason": "name", "candidates": [{"doc_id": doc["id"]}]})
    out = agent.run_tool("edit_document", {"doc_id": doc["id"], "operation": "replace", "find": "x",
                                           "text": "a joyful job"}, guard, [])
    # not a question put to the user in code: the agent gets another step, told which letters to avoid
    assert out["status"] == "letters_missing" and '"j"' in out["next"]
    assert out["status"] not in agent.CODE_REPLY_STATUSES
    assert [p["reason"] for p in guard.pending] == ["pdf_conversion"]   # the user may still be asked to convert
    out = agent.run_tool("edit_document", {"doc_id": doc["id"], "operation": "replace", "find": "x",
                                           "text": "a glad task"}, guard, [])
    assert out["status"] == "edited" and guard.pending == []   # the rewording went through: nothing left to ask
