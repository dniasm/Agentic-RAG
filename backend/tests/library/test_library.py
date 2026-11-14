from backend.library import catalog, config, library
from backend.rag import vectorstore
from backend.config.settings import settings


def _chunk_metadata(doc_id: str) -> list[dict]:
    points, _ = vectorstore.get_client().scroll(
        settings.collection_name, scroll_filter=vectorstore.user_filter(config.OWNER, doc_id), with_payload=True)
    return [p.payload["metadata"] for p in points]


def test_ingest_titles_the_document(library_docs):
    doc = library_docs["gdpr_final.txt"]
    assert doc["title"] == "EU General Data Protection Regulation"
    assert doc["original_name"] == "gdpr_final.txt"
    assert (config.LIBRARY_DIR / "EU General Data Protection Regulation.txt").exists()
    meta = _chunk_metadata(doc["id"])
    assert meta and all(m["title"] == doc["title"] for m in meta)
    assert catalog.rename_history(doc["id"])[0]["reason"] == "titled"


def test_duplicate_upload_is_detected(library_docs, tmp_path):
    path = tmp_path / "copy.txt"
    path.write_bytes((tmp_path / "svm.txt").read_bytes())
    doc, duplicate = library.ingest_path(path)
    assert duplicate and doc["id"] == library_docs["svm.txt"]["id"]


def test_rename_updates_catalog_file_and_chunks(library_docs):
    doc = library_docs["svm.txt"]
    result = library.rename(doc["id"], "SVM Notes")
    assert result["old_title"] == "Support Vector Machines"
    assert catalog.get_document(doc["id"])["title"] == "SVM Notes"
    assert (config.LIBRARY_DIR / "SVM Notes.txt").exists()
    assert not (config.LIBRARY_DIR / "Support Vector Machines.txt").exists()
    assert all(m["source"] == "SVM Notes" and m["title"] == "SVM Notes" for m in _chunk_metadata(doc["id"]))


def test_same_title_gets_its_own_file(library_docs):
    library.rename(library_docs["glove.txt"]["id"], "Support Vector Machines")
    files = sorted(p.name for p in config.LIBRARY_DIR.iterdir())
    assert "Support Vector Machines.txt" in files and "Support Vector Machines (2).txt" in files


def test_find_by_name(library_docs):
    result = library.find("w2v.txt")
    assert result["decision"] == "unique"
    assert result["candidates"][0]["doc_id"] == library_docs["w2v.txt"]["id"]


def test_find_by_old_name(library_docs):
    library.rename(library_docs["svm.txt"]["id"], "Margin Classifiers")
    result = library.find("Support Vector Machines")
    assert result["decision"] == "unique"
    assert result["candidates"][0]["doc_id"] == library_docs["svm.txt"]["id"]


def test_find_by_content(library_docs):
    result = library.find("personal data consent fines erasure")
    assert result["decision"] == "unique"
    assert result["candidates"][0]["doc_id"] == library_docs["gdpr_final.txt"]["id"]


def test_vague_description_is_ambiguous(library_docs):
    result = library.find("word embeddings")
    assert result["decision"] == "ambiguous"
    ids = {c["doc_id"] for c in result["candidates"]}
    assert {library_docs["w2v.txt"]["id"], library_docs["glove.txt"]["id"]} <= ids


def test_more_detail_resolves_it(library_docs):
    result = library.find("word embeddings by Mikolov at Google with skip-gram")
    assert result["decision"] == "unique"
    assert result["candidates"][0]["doc_id"] == library_docs["w2v.txt"]["id"]


def test_same_names_are_ambiguous(library_docs):
    library.rename(library_docs["glove.txt"]["id"], "Embeddings")
    library.rename(library_docs["w2v.txt"]["id"], "Embeddings")
    result = library.find("Embeddings")
    assert result["decision"] == "ambiguous" and result["reason"] == "same_name"


def test_nothing_matches(library_docs):
    assert library.find("quarterly sales budget for marketing")["decision"] == "none"


def test_empty_library():
    assert library.find("anything")["decision"] == "none"


def test_find_without_the_reranker_uses_cosine(library_docs, monkeypatch):
    from backend.rag import rerank
    monkeypatch.setattr(rerank, "score", lambda query, hits: None)
    result = library.find("Mikolov skip-gram continuous bag of words")
    assert result["scorer"] == "cosine"
    assert result["candidates"][0]["doc_id"] == library_docs["w2v.txt"]["id"]
