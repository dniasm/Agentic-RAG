"""A longer edit goes through and the user is told what it did to the document's length (from a chat where
"expand the profile" was refused as a spacing problem when the paragraph only needed more lines than the page had)."""
from backend.library import agent


def _reply(**grew):
    return agent.compose_reply([{"status": "edited", "title": "cv", "operation": "replace", "changes": 1,
                                 "previews": [], **grew}])


def test_the_reply_says_the_text_grew_and_where_the_rest_went():
    assert "grew by 3 line(s); what is below it moved down" in _reply(added_lines=3)
    out = _reply(added_lines=13, new_pages=[2])
    assert "grew by 13 line(s)" in out and "moved to a new page 2" in out and "undo" in out
    assert "grew" not in _reply()


def test_markdown_never_reaches_a_pdf_and_the_user_is_told():
    from backend.library import editing
    text, removed = editing.without_markdown("**Professional Profile (expanded)**\n\nA driven `CSIT` student.")
    assert (text, removed) == ("Professional Profile (expanded) A driven CSIT student.", True)
    assert editing.without_markdown("C++ and 2*3*4 stay as written.") == ("C++ and 2*3*4 stay as written.", False)
    assert editing.without_markdown("## Skills")[0] == "Skills"
    assert "Formatting marks (** and #) were left out" in _reply(markdown_removed=True)
    assert "page numbers couldn't be updated" in _reply(added_lines=2, new_pages=[3], page_numbers_kept=True)
