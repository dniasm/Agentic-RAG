"""The Library page (stage 6): Rename, Undo and Redo on each document, and the built front end served by FastAPI."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.library import catalog, config, editing
from backend.web import LONG_CACHE, NO_CACHE, mount_frontend


def test_rename_from_the_library_page(library_docs, signed_in):
    doc = library_docs["svm.txt"]
    r = signed_in.post(f"/api/library/documents/{doc['id']}/rename", json={"title": "  SVM Notes.txt "})
    assert r.status_code == 200, r.text
    assert r.json()["new_title"] == "SVM Notes" and r.json()["old_title"] == doc["title"]
    assert catalog.get_document(doc["id"])["title"] == "SVM Notes"
    assert catalog.rename_histories()[doc["id"]][-1]["new_title"] == "SVM Notes"


def test_a_name_another_document_has_is_asked_about_first(library_docs, signed_in):
    doc, other = library_docs["svm.txt"], library_docs["glove.txt"]
    url = f"/api/library/documents/{doc['id']}/rename"
    r = signed_in.post(url, json={"title": other["title"]})
    assert r.status_code == 409 and r.json()["detail"]["conflicts"] == [other["title"]]
    assert catalog.get_document(doc["id"])["title"] == doc["title"]          # nothing changed yet
    assert signed_in.post(url, json={"title": other["title"], "confirmed": True}).status_code == 200


def test_an_unusable_name_is_refused(library_docs, signed_in):
    r = signed_in.post(f"/api/library/documents/{library_docs['svm.txt']['id']}/rename", json={"title": "???"})
    assert r.status_code == 400 and "letter or number" in r.json()["detail"]


def test_another_users_document_cant_be_renamed(library_docs, signed_in, monkeypatch):
    monkeypatch.setattr(config, "SIGNUP_CODE", "let-me-in")
    other = TestClient(signed_in.app)
    r = other.post("/api/auth/signup", json={"username": "someone", "password": "another horse", "invite": "let-me-in"})
    assert r.status_code == 200, r.text
    r = other.post(f"/api/library/documents/{library_docs['svm.txt']['id']}/rename", json={"title": "Mine"})
    assert r.status_code == 404


def test_the_list_says_which_edit_undo_and_redo_apply_to(library_docs, signed_in):
    doc = library_docs["svm.txt"]
    row = lambda: next(d for d in signed_in.get("/api/documents").json() if d["id"] == doc["id"])  # noqa: E731
    assert row()["edit_id"] is None and row()["redo_id"] is None
    editing.edit(doc["id"], "append", text="A line added.")
    edit_id = catalog.last_edit(doc["id"])["id"]
    assert row()["edit_id"] == edit_id and row()["redo_id"] is None
    assert signed_in.post(f"/api/library/documents/{doc['id']}/undo", json={"edit_id": edit_id}).status_code == 200
    assert row()["edit_id"] is None and row()["redo_id"] == edit_id


def _app(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "assets" / "pdf.worker.min-abc123.mjs").write_text("self.onmessage = null", encoding="utf-8")
    (dist / "mark.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not served", encoding="utf-8")
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"ok": True}

    assert mount_frontend(app, dist)
    return TestClient(app)


def test_the_built_front_end_is_served_with_the_right_caching(tmp_path):
    client = _app(tmp_path)
    page = client.get("/")
    assert page.status_code == 200 and "<title>app</title>" in page.text
    assert page.headers["cache-control"] == NO_CACHE["Cache-Control"]
    asset = client.get("/assets/index-abc123.js")
    assert asset.text == "console.log(1)" and asset.headers["cache-control"] == LONG_CACHE["Cache-Control"]
    assert client.get("/mark.svg").headers["cache-control"] == NO_CACHE["Cache-Control"]
    # scripts and the PDF viewer's module worker as JavaScript, whatever the machine guesses for .mjs (nosniff)
    assert asset.headers["content-type"].startswith("text/javascript")
    assert client.get("/assets/pdf.worker.min-abc123.mjs").headers["content-type"].startswith("text/javascript")
    # the app's own routes get the page; the API keeps its routes and its JSON 404s
    route = client.get("/documents")
    assert "<title>app</title>" in route.text and route.headers["cache-control"] == NO_CACHE["Cache-Control"]
    assert client.get("/api/health").json() == {"ok": True}
    missing = client.get("/api/nothing")
    assert missing.status_code == 404 and missing.json() == {"detail": "Not Found"}


def test_nothing_outside_the_build_is_served(tmp_path):
    client = _app(tmp_path)
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"):
        assert "not served" not in client.get(path).text


def test_without_a_build_nothing_is_mounted(tmp_path):
    assert not mount_frontend(FastAPI(), tmp_path / "dist")
