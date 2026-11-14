"""Uploads return at once and are indexed in the background (no API calls; temporary database and upload folder).

    python -m unittest backend.tests.test_documents -v
"""
import os
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import get_current_user
from backend.api.routes import documents
from backend.config.settings import settings
from backend.models import database as db
from backend.rag.ingestion import IngestionError


class BackgroundIndexing(unittest.TestCase):
    def setUp(self):
        self._old = settings.database_path, settings.upload_dir, db._conn
        tmp = tempfile.mkdtemp()
        settings.database_path, settings.upload_dir, db._conn = os.path.join(tmp, "t.db"), tmp, None
        db.init_db()
        self.user = db.create_user("docs_test", "x")

        app = FastAPI()
        app.include_router(documents.router)
        app.dependency_overrides[get_current_user] = lambda: {"id": self.user["id"], "username": "docs_test"}
        self.client = TestClient(app)

        self.jobs = []  # indexing jobs, run by the test when it chooses
        submit = mock.patch.object(documents._ingest_worker, "submit",
                                   side_effect=lambda fn, *args: self.jobs.append((fn, args)))
        vectors = mock.patch.object(documents.vectorstore, "delete_document_vectors")
        submit.start()
        self.delete_vectors = vectors.start()
        self.addCleanup(submit.stop)
        self.addCleanup(vectors.stop)

    def tearDown(self):
        db._conn.close()
        settings.database_path, settings.upload_dir, db._conn = self._old

    def upload(self, text=b"Some text about BM25."):
        return self.client.post("/documents/upload", files={"file": ("notes.txt", text, "text/plain")})

    def run_jobs(self, **ingest):
        with mock.patch.object(documents, "ingest_file", **ingest):
            for fn, args in self.jobs:
                fn(*args)
        self.jobs.clear()

    def status(self):
        return [(d["status"], d["chunks"], d.get("error")) for d in db.list_documents(self.user["id"])]

    def test_upload_returns_before_indexing_then_the_document_becomes_indexed(self):
        res = self.upload()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "processing")
        self.assertEqual(self.status(), [("processing", 0, None)])
        self.run_jobs(return_value={"pages": 1, "chunks": 3})
        self.assertEqual(self.status(), [("indexed", 3, None)])

    def test_a_file_without_text_fails_with_its_message(self):
        self.upload()
        self.run_jobs(side_effect=IngestionError("No extractable text"))
        self.assertEqual(self.status(), [("failed", 0, "No extractable text")])

    def test_an_unexpected_error_fails_and_removes_partial_vectors(self):
        self.upload()
        self.run_jobs(side_effect=RuntimeError("Gemini quota"))
        self.assertEqual(self.status()[0][0], "failed")
        self.assertIn("Gemini quota", self.status()[0][2])
        self.delete_vectors.assert_called_once()

    def test_removed_while_indexing_leaves_no_vectors(self):
        doc_id = self.upload().json()["document"]["id"]
        self.assertEqual(self.client.delete(f"/documents/{doc_id}").status_code, 204)
        self.delete_vectors.reset_mock()
        self.run_jobs(return_value={"pages": 1, "chunks": 3})
        self.assertEqual(self.status(), [])
        self.delete_vectors.assert_called_once_with(self.user["id"], doc_id)

    def test_uploading_the_same_file_while_it_is_indexed_starts_no_second_job(self):
        self.upload()
        res = self.upload()
        self.assertEqual(res.json()["status"], "processing")
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(len(db.list_documents(self.user["id"])), 1)

    def test_a_failed_file_can_be_uploaded_again(self):
        self.upload()
        self.run_jobs(side_effect=RuntimeError("down"))
        self.assertEqual(self.upload().json()["status"], "processing")
        self.run_jobs(return_value={"pages": 2, "chunks": 5})
        self.assertEqual(self.status(), [("indexed", 5, None)])

    def test_indexing_cut_off_by_a_restart_is_marked_failed(self):
        self.upload()
        self.assertEqual(db.fail_unfinished_documents(), 1)
        self.assertEqual(self.status(), [("failed", 0, db.INTERRUPTED)])


if __name__ == "__main__":
    unittest.main()
