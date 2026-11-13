"""Re-indexing after an edit embeds only the chunks that changed, and leaves the index as a full ingest would."""
import pytest

from renamer import catalog, config, editing, library
from renamer.day7 import ingestion, settings as day7_settings, vectorstore

PARAGRAPHS = [f"Section {i}. " + " ".join(f"Topic{i} detail {j} explains part {j} of the subject." for j in range(14))
              for i in range(12)]


@pytest.fixture
def long_doc(tmp_path):
    path = tmp_path / "handbook.txt"
    path.write_text("\n\n".join(PARAGRAPHS), encoding="utf-8")
    doc, _ = library.ingest_path(path)
    assert doc["status"] == "indexed" and doc["chunks"] >= 6, doc
    return doc


@pytest.fixture
def embedded(monkeypatch):
    """The texts sent to Gemini (through the fake embedder)."""
    sent, real = [], ingestion.embed_paced

    def counting(texts, title):
        sent.extend(texts)
        return real(texts, title)
    monkeypatch.setattr(ingestion, "embed_paced", counting)
    return sent


def stored_points(doc_id):
    client = vectorstore.get_client()
    points, _ = client.scroll(day7_settings.collection_name, scroll_filter=vectorstore.user_filter(config.OWNER, doc_id),
                              limit=500, with_vectors=[vectorstore.DENSE])
    return {p.id: (p.payload["page_content"], p.payload["metadata"], p.vector[vectorstore.DENSE]) for p in points}


def test_a_one_word_edit_embeds_only_its_chunk(long_doc, embedded):
    chunks = long_doc["chunks"]
    out = editing.edit(long_doc["id"], "replace", find="Topic5 detail 3 explains", text="Topic5 detail 3 shows")
    assert out["chunks_reindexed"] == chunks
    assert 1 <= out["chunks_embedded"] <= 2 and len(embedded) == out["chunks_embedded"]    # not all of them
    assert all("Topic5 detail 3 shows" in t or "Topic5" in t for t in embedded)


def test_the_index_matches_a_full_ingest(long_doc, embedded, monkeypatch):
    editing.edit(long_doc["id"], "replace", find="Topic7 detail 9 explains", text="Topic7 detail 9 now explains")
    editing.edit(long_doc["id"], "append", text="An appendix with new material. " * 40)
    partial = stored_points(long_doc["id"])
    doc = catalog.get_document(long_doc["id"])
    ingestion.ingest_file(config.LIBRARY_DIR / doc["file_name"], user_id=config.OWNER, doc_id=doc["id"],
                          filename=doc["title"])                      # day7's own full re-ingest, for comparison
    library._set_chunk_fields(doc["id"], doc["owner"], title=doc["title"], file_name=doc["file_name"])
    full = stored_points(long_doc["id"])
    assert partial.keys() == full.keys()
    for pid in full:
        text, meta, vector = partial[pid]
        assert (text, meta) == full[pid][:2]
        assert vector == pytest.approx(full[pid][2], abs=1e-6)       # the reused vectors are the right ones


def test_undo_embeds_only_what_the_edit_changed(long_doc, embedded):
    editing.edit(long_doc["id"], "replace", find="Topic2 detail 4 explains", text="Topic2 detail 4 covers")
    embedded.clear()
    out = editing.undo(long_doc["id"])
    assert 1 <= len(embedded) <= 2 and out["chunks_reindexed"] == long_doc["chunks"]
