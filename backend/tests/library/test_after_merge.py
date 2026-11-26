"""Fixes from the first use of the merged app: renames of documents from before the merge, an offered match the user
says yes to, page counts of non-PDF documents, and the documents a chat has changed."""
from backend.library import activity, agent, catalog, library
from backend.models import database as db


def test_a_nearest_match_offered_after_no_match_can_be_renamed_once_the_user_replies():
    offered = {"decision": "none", "candidates": [{"doc_id": "d1"}]}
    now = agent.Guard({}, 1)
    now.saw_find(offered)
    assert now.identification_problem("d1")                  # not in the message that offered it
    assert agent.Guard(now.state(), 2).identification_problem("d1") is None   # after "yes, that one"


def test_a_document_without_a_summary_shows_its_opening(library_docs):
    doc = library_docs["svm.txt"]
    catalog.update_document(doc["id"], summary="")           # as day7's documents came over in the merge
    out = agent.run_tool("document_details", {"doc_id": doc["id"]}, agent.Guard({}, 1), [])
    assert out["opening"].startswith("Support Vector Machines")
    catalog.update_document(doc["id"], summary="About SVMs.")
    assert "opening" not in agent.run_tool("document_details", {"doc_id": doc["id"]}, agent.Guard({}, 1), [])


def test_a_long_text_document_counts_the_pages_it_is_previewed_and_downloaded_as(tmp_path):
    path = tmp_path / "long.txt"
    path.write_text("Long Notes\n" + "\n\n".join(f"Paragraph {i} " + "words " * 60 for i in range(80)),
                    encoding="utf-8")
    doc, _ = library.ingest_path(path)
    assert doc["status"] == "indexed" and doc["pages"] > 3   # day7's loader counted one page for any text file


def test_a_chat_lists_only_the_documents_it_changed_newest_first(library_docs):
    svm, glove = library_docs["svm.txt"], library_docs["glove.txt"]
    owner = svm["owner"]
    messages = [
        {"created_at": "2026-11-15T10:00:00", "meta": {"actions": [{"type": "rename", "doc_id": svm["id"]}]}},
        {"created_at": "2026-11-15T10:01:00", "meta": {"actions": [{"type": "queue", "batch_id": "b"}]}},
        {"created_at": "2026-11-15T10:02:00", "meta": {"actions": [{"type": "edit", "doc_id": glove["id"]},
                                                                   {"type": "edit", "doc_id": "deleted"}]}},
        {"created_at": "2026-11-15T10:03:00", "meta": {}},
    ]
    out = activity.documents_changed_in(messages, owner)
    assert [(d["id"], d["action"]) for d in out] == [(glove["id"], "edited"), (svm["id"], "renamed")]
    assert out[0]["title"] == glove["title"] and out[0]["ext"] == ".txt"
    assert activity.documents_changed_in(messages, "someone else") == []


def test_the_chat_documents_route_is_per_conversation(library_docs, signed_in):
    me = signed_in.user["id"]
    svm = library_docs["svm.txt"]   # the first account took these
    chat_a, chat_b = db.create_conversation(me, "A"), db.create_conversation(me, "B")
    db.add_message(chat_a["id"], "assistant", "Renamed.", tool="manage_documents",
                   meta={"actions": [{"type": "rename", "doc_id": svm["id"]}]})
    assert [d["id"] for d in signed_in.get(f"/api/conversations/{chat_a['id']}/documents").json()] == [svm["id"]]
    assert signed_in.get(f"/api/conversations/{chat_b['id']}/documents").json() == []
    assert signed_in.get("/api/conversations/nope/documents").status_code == 404


def test_an_undo_button_undoes_only_the_edit_it_is_under(library_docs, signed_in):
    from test_agent import Scripted
    doc, me = library_docs["svm.txt"], signed_in.user["id"]   # the first account took the documents

    def edit(text, *before):
        steps = [*before, [("edit_document", {"doc_id": doc["id"], "operation": "append", "text": text})]]
        return agent.chat("u1", "user message", model=Scripted(*steps), owner=me)["actions"][0]["edit_id"]

    first = edit("First addition.", [("find_documents", {"description": "svm.txt"})])
    second = edit("Second.")
    assert first and second and second != first

    url = f"/api/library/documents/{doc['id']}/undo"
    assert signed_in.post(url, json={"edit_id": first}).status_code == 409   # a later edit came after it
    assert signed_in.post(url, json={"edit_id": second}).status_code == 200
    assert signed_in.post(url, json={"edit_id": second}).status_code == 409  # already undone
    assert signed_in.post(url, json={"edit_id": first}).status_code == 200   # now the latest again


def test_a_rename_of_a_near_miss_is_confirmed_first_even_with_two_of_them(library_docs):
    w2v, glove = library_docs["w2v.txt"]["id"], library_docs["glove.txt"]["id"]
    guard = agent.Guard({}, 1)
    guard.saw_find({"decision": "none", "candidates": [{"doc_id": w2v}, {"doc_id": glove}]})
    later = agent.Guard(guard.state(), 2)   # the user said "yeah rename it"
    out = agent.run_tool("rename_document", {"doc_id": glove, "new_title": "Embeddings"}, later, [])
    assert out["status"] == "needs_confirmation" and out["reason"] == "weak_match"
    assert agent.run_tool("rename_document", {"doc_id": glove, "new_title": "Embeddings", "confirmed": True},
                          later, [])["status"] == "needs_confirmation"     # not in the message that asked
    yes = agent.Guard(later.state(), 3)
    actions = []
    assert agent.run_tool("rename_document", {"doc_id": glove, "new_title": "Embeddings", "confirmed": True},
                          yes, actions)["status"] == "renamed"
    assert catalog.get_document(w2v)["title"] == "Word2vec Neural Word Embeddings"


def test_a_word_files_page_count_is_words_own(tmp_path):
    import zipfile
    import docx
    path = tmp_path / "report.docx"
    d = docx.Document()
    d.add_paragraph("Quarterly Report")
    d.add_paragraph("Sales rose in every region this quarter. " * 5)
    d.save(path)
    with zipfile.ZipFile(path) as z:   # Word writes its page count into docProps/app.xml; python-docx doesn't
        files = {n: z.read(n) for n in z.namelist()}
    files["docProps/app.xml"] = (b'<?xml version="1.0"?><Properties xmlns="http://schemas.openxmlformats.org/'
                                 b'officeDocument/2006/extended-properties"><Pages>7</Pages></Properties>')
    with zipfile.ZipFile(path, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    doc, _ = library.ingest_path(path)
    assert doc["status"] == "indexed" and doc["pages"] == 7


def test_passages_of_a_text_file_carry_no_page(library_docs):
    from backend.config.settings import settings
    from services.document import vectorstore
    points, _ = vectorstore.get_client().scroll(settings.collection_name, limit=50,
                                               scroll_filter=vectorstore.user_filter(library_docs["svm.txt"]["owner"],
                                                                                     library_docs["svm.txt"]["id"]))
    assert points and all(p.payload["metadata"].get("page") is None for p in points)


def _offered_as_near_miss(*doc_ids):
    guard = agent.Guard({}, 1)
    guard.saw_find({"decision": "none", "candidates": [{"doc_id": d} for d in doc_ids]})
    return agent.Guard(guard.state(), 2)   # the user replied to the offer, maybe without naming one


def test_an_edit_of_a_near_miss_is_confirmed_first(library_docs):
    glove = library_docs["glove.txt"]["id"]
    later = _offered_as_near_miss(library_docs["w2v.txt"]["id"], glove)
    args = {"doc_id": glove, "operation": "append", "text": "More."}
    out = agent.run_tool("edit_document", args, later, [])
    assert out["status"] == "needs_confirmation" and out["reason"] == "weak_match"
    assert agent.run_tool("edit_document", args, later, [])["status"] == "needs_confirmation"
    yes, actions = agent.Guard(later.state(), 3), []
    assert agent.run_tool("edit_document", args, yes, actions)["status"] == "edited"
    # confirmed once: the document counts as identified from then on, for any tool
    assert not agent.Guard(yes.state(), 4).weak_match_unconfirmed(glove)


def test_an_undo_of_a_near_miss_is_confirmed_first(library_docs):
    w2v = library_docs["w2v.txt"]["id"]
    later = _offered_as_near_miss(w2v)
    out = agent.run_tool("undo_edit", {"doc_id": w2v}, later, [])
    assert out["status"] == "needs_confirmation" and out["reason"] == "weak_match"
    out = agent.run_tool("undo_edit", {"doc_id": w2v}, agent.Guard(later.state(), 3), [])
    assert out["status"] != "needs_confirmation"   # asked and answered (nothing to undo here)


def test_an_old_near_miss_question_is_asked_again(library_docs):
    w2v = library_docs["w2v.txt"]["id"]
    later = _offered_as_near_miss(w2v)
    assert later.weak_match_unconfirmed(w2v)                       # asked at turn 2
    assert agent.Guard(later.state(), 5).weak_match_unconfirmed(w2v)   # the user may have said no back then
