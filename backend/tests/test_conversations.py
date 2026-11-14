"""Renaming a chat (no API calls; temporary database).

    python -m unittest backend.tests.test_conversations -v
"""
import os
import sqlite3
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import get_current_user
from backend.api.routes import chat
from backend.config.settings import settings
from backend.models import database as db


class Rename(unittest.TestCase):
    def setUp(self):
        self._old_path, self._old_conn = settings.database_path, db._conn
        self.tmp = tempfile.mkdtemp()
        settings.database_path = os.path.join(self.tmp, "test.db")
        db._conn = None
        db.init_db()
        self.user = db.create_user("rename_test", "x")
        self.other = db.create_user("someone_else", "x")
        self.conv = db.create_conversation(self.user["id"], "what is bm25?")

        app = FastAPI()
        app.include_router(chat.router)
        app.dependency_overrides[get_current_user] = lambda: {"id": self.user["id"], "username": "rename_test"}
        self.client = TestClient(app)

    def tearDown(self):
        db._conn.close()
        db._conn, settings.database_path = self._old_conn, self._old_path

    def test_rename_keeps_the_chats_place_in_the_list(self):
        res = self.client.patch(f"/conversations/{self.conv['id']}", json={"title": "  BM25 notes  "})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["title"], "BM25 notes")
        self.assertEqual(res.json()["updated_at"], self.conv["updated_at"])
        self.assertEqual(db.list_conversations(self.user["id"])[0]["title"], "BM25 notes")

    def test_empty_or_too_long_titles_are_rejected(self):
        for title in ("", "   ", "x" * 61):
            self.assertEqual(self.client.patch(f"/conversations/{self.conv['id']}", json={"title": title}).status_code,
                             422, title)
        self.assertEqual(db.get_conversation(self.user["id"], self.conv["id"])["title"], "what is bm25?")

    def test_another_users_chat_is_not_found(self):
        theirs = db.create_conversation(self.other["id"], "private")
        self.assertEqual(self.client.patch(f"/conversations/{theirs['id']}", json={"title": "mine"}).status_code, 404)
        self.assertEqual(db.get_conversation(self.other["id"], theirs["id"])["title"], "private")


class FreshDatabase(unittest.TestCase):
    def test_no_old_memory_table_and_migration_marked_done(self):
        path = os.path.join(tempfile.mkdtemp(), "fresh.db")
        old_path, old_conn = settings.database_path, db._conn
        settings.database_path, db._conn = path, None
        try:
            db.init_db()
            db.init_db()  # a second start must not fail either
        finally:
            db._conn.close()
            db._conn, settings.database_path = old_conn, old_path
        con = sqlite3.connect(path)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        done = con.execute("SELECT name FROM migrations").fetchall()
        con.close()
        self.assertNotIn("user_memory", tables)
        self.assertEqual(done, [("user_facts_v1",)])


if __name__ == "__main__":
    unittest.main()
