"""The orchestrator's catalog functions (backend/library/catalog.py) over gRPC to the Document service: the dicts they
return keep the shapes the old SQL gave, and the pairs of writes that were two commits are one."""


from backend.library import catalog


def add(title="Report", owner="u1"):
    return catalog.add_document(title=title, original_name="o.pdf", file_name=f"{owner}/{title}.pdf", ext=".pdf",
                                sha256=f"sha-{title}", size_bytes=10, owner=owner)


def test_a_new_document_has_none_for_what_is_not_set_yet():
    doc = add()
    assert doc["status"] == "processing" and doc["keywords"] == [] and doc["summary"] == ""
    assert (doc["pages"], doc["chunks"], doc["error"]) == (None, None, None) and doc["original_sha256"] == doc["sha256"]
    catalog.update_document(doc["id"], pages=3, error="bad")
    catalog.update_document(doc["id"], error=None, keywords=["a", "b"])   # None clears the column
    got = catalog.get_document(doc["id"])
    assert (got["pages"], got["error"], got["keywords"]) == (3, None, ["a", "b"])
    assert catalog.get_document("nope") is None and catalog.find_by_hash("sha-Report", "u2") is None


def test_a_rename_and_the_document_change_are_one_call_and_move_the_version():
    doc = add()
    before = catalog.version()
    catalog.update_document(doc["id"], rename=("Report", "Annual report", "user"), title="Annual report")
    assert catalog.version() > before
    assert catalog.get_document(doc["id"])["title"] == "Annual report"
    assert [(r["old_title"], r["new_title"], r["reason"]) for r in catalog.rename_history(doc["id"])] == [
        ("Report", "Annual report", "user")]
    assert catalog.rename_histories()[doc["id"]] == catalog.rename_history(doc["id"])


def test_indexed_titles_follow_the_catalog_without_asking_every_time():
    doc = add("Handbook", owner="u7")
    assert catalog.indexed_titles("u7") == []
    catalog.update_document(doc["id"], status="indexed")
    assert catalog.indexed_titles("u7") == ["Handbook"]


def test_an_edit_undo_and_redo_carry_their_document_change_in_the_same_call():
    doc = add()
    edit_id = catalog.record_edit(doc["id"], "replace", "x", "b.pdf", document_fields={"size_bytes": 99})
    assert catalog.get_document(doc["id"])["size_bytes"] == 99 and catalog.last_edit(doc["id"])["id"] == edit_id
    last = {**catalog.last_edit(doc["id"]), "doc_id": doc["id"]}
    catalog.move_edit_to_undone(last, "r.pdf", ".md", document_fields={"ext": ".md", "size_bytes": 5})
    assert not catalog.edit_exists(edit_id) and catalog.get_document(doc["id"])["ext"] == ".md"
    assert catalog.last_undone(doc["id"])["redo"] == "r.pdf"
    catalog.restore_undone(edit_id, document_fields={"ext": ".pdf", "size_bytes": 99})
    assert catalog.edit_exists(edit_id) and catalog.get_document(doc["id"])["ext"] == ".pdf"
    assert catalog.last_undone(doc["id"]) is None and catalog.edit_history(doc["id"])[0]["detail"] == "x"


def test_a_batch_comes_back_with_decoded_tasks_and_a_missing_task_is_ignored():
    batch = catalog.add_batch("s1", ["free text", ("change", {"action": "append", "text": "t"})], owner="u1")
    first, second = batch["tasks"]
    assert first["payload"] is None and second["payload"] == {"action": "append", "text": "t"}
    assert (first["started_at"], first["checkpoint"], first["actions"]) == (None, None, [])
    catalog.update_task(second["id"], status="running", checkpoint={"doc_id": "d"}, actions=[{"type": "x"}])
    got = catalog.get_task(second["id"])
    assert got["checkpoint"] == {"doc_id": "d"} and got["actions"] == [{"type": "x"}]
    catalog.update_task(10 ** 9, status="done")   # no such task: nothing happens, as before
    assert catalog.cancel_batch(batch["id"]) == 1 and catalog.cancel_batch("unknown") == 0
    assert catalog.load_session("fresh")["owner"] is None
    catalog.save_session({"id": "fresh", "turn": 1, "llm_messages": [{"r": 1}], "display": [], "guard": {"g": 1}})
    assert catalog.load_session("fresh")["owner"] == "library" and catalog.load_session("fresh")["guard"] == {"g": 1}
