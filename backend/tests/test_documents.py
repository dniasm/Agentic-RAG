"""day7's /documents routes on the document library: uploads return at once and are titled and indexed in the
background by the library's one queue (no API calls; temporary database and folders).

    python -m unittest backend.tests.test_documents -v
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import get_current_user
from backend.api.routes import documents
from backend.config.settings import settings
from backend.library import api as library_api, catalog, config, library, titles
from backend.models import database as db
from backend.documents.rag import IngestionError


class LibraryBackedDocuments(unittest.TestCase):
    def setUp(self):
        self._old = settings.database_path, db._conn, config.DATA_DIR, config.LIBRARY_DIR
        tmp = Path(tempfile.mkdtemp())
        settings.database_path, db._conn = str(tmp / "t.db"), None
        config.DATA_DIR, config.LIBRARY_DIR = tmp, tmp / "library"
        db.init_db()
        self.user = db.create_user("docs_test", "x")
        self.other = db.create_user("someone_else", "x")

        app = FastAPI()
        app.include_router(documents.router)
        self.as_user = self.user
        app.dependency_overrides[get_current_user] = lambda: {"id": self.as_user["id"], "username": "u"}
        self.client = TestClient(app)

        self.jobs = []  # indexing jobs, run by the test when it chooses
        for target, attr, kw in (
            (library._queue, "submit", {"side_effect": lambda fn, *args: self.jobs.append((fn, args))}),
            (library.rag, "delete_document_vectors", {}),
            (library, "_set_chunk_fields", {}),
            (titles, "profile_document", {"return_value": (titles.DocProfile(title="Notes On BM25", summary="s",
                                                                             keywords=[]), True)}),
        ):
            patcher = mock.patch.object(target, attr, **kw)
            setattr(self, attr, patcher.start())
            self.addCleanup(patcher.stop)

    def tearDown(self):
        catalog.reset_connection()
        db._conn.close()
        settings.database_path, db._conn, config.DATA_DIR, config.LIBRARY_DIR = self._old

    def upload(self, text=b"Some text about BM25."):
        return self.client.post("/documents/upload", files={"file": ("notes.txt", text, "text/plain")})

    def run_jobs(self, **ingest):
        with mock.patch.object(library.rag, "ingest", **ingest):
            for fn, args in self.jobs:
                fn(*args)
        self.jobs.clear()

    def status(self):
        return [(d["status"], d["chunks"], d["error"]) for d in catalog.list_documents(owner=self.user["id"])]

    def test_an_upload_is_titled_stored_in_the_users_folder_and_indexed(self):
        res = self.upload()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "processing")
        self.run_jobs(return_value={"pages": 1, "chunks": 3})
        self.assertEqual(self.status(), [("indexed", 3, None)])
        listed = self.client.get("/documents").json()
        self.assertEqual((listed[0]["title"], listed[0]["filename"]), ("Notes On BM25", "notes.txt"))
        doc = catalog.list_documents(owner=self.user["id"])[0]
        self.assertTrue(doc["file_name"].startswith(self.user["id"] + "/"))
        self.assertTrue((config.LIBRARY_DIR / doc["file_name"]).exists())

    def test_a_file_without_text_fails_with_its_message(self):
        self.upload()
        self.run_jobs(side_effect=IngestionError("No extractable text"))
        self.assertEqual(self.status(), [("failed", None, "No extractable text")])

    def test_an_unexpected_error_fails_and_removes_partial_vectors(self):
        self.upload()
        self.run_jobs(side_effect=RuntimeError("Gemini quota"))
        self.assertEqual(self.status()[0][0], "failed")
        self.assertIn("Gemini quota", self.status()[0][2])
        self.delete_document_vectors.assert_called()

    def test_removed_while_waiting_leaves_no_document(self):
        doc_id = self.upload().json()["document"]["id"]
        self.assertEqual(self.client.delete(f"/documents/{doc_id}").status_code, 204)
        self.run_jobs(return_value={"pages": 1, "chunks": 3})
        self.assertEqual(self.status(), [])

    def test_uploading_the_same_file_while_it_is_indexed_starts_no_second_job(self):
        self.upload()
        res = self.upload()
        self.assertEqual(res.json()["status"], "processing")
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(len(catalog.list_documents(owner=self.user["id"])), 1)

    def test_a_failed_file_can_be_uploaded_again(self):
        self.upload()
        self.run_jobs(side_effect=RuntimeError("down"))
        self.assertEqual(self.upload().json()["status"], "processing")
        self.run_jobs(return_value={"pages": 2, "chunks": 5})
        self.assertEqual(self.status(), [("indexed", 5, None)])

    def test_indexing_cut_off_by_a_restart_is_resumed(self):
        self.upload()
        self.jobs.clear()
        library_api._resume_unfinished()
        self.assertEqual(len(self.jobs), 1)

    def test_another_users_document_is_not_found(self):
        doc_id = self.upload().json()["document"]["id"]
        self.as_user = self.other
        self.assertEqual(self.client.delete(f"/documents/{doc_id}").status_code, 404)
        self.assertEqual(self.client.get("/documents").json(), [])


if __name__ == "__main__":
    unittest.main()
