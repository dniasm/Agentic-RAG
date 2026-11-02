"""Memory storage and merge rules (no API calls; uses a temporary SQLite database).

    python -m unittest backend.tests.test_memory -v
"""
import json
import os
import tempfile
import unittest

from backend.config.settings import settings
from backend.models import database as db
from backend.tools.memory import MemoryOp, is_single_value, normalize_key, plan_changes


def remember(key, value):
    return MemoryOp(key=key, value=value, action="remember")


def forget(key, value):
    return MemoryOp(key=key, value=value, action="forget")


class Keys(unittest.TestCase):
    def test_normalisation_and_aliases(self):
        self.assertEqual(normalize_key("Favourite Drink"), "favorite_drink")
        self.assertEqual(normalize_key("name"), "preferred_name")
        self.assertEqual(normalize_key("City"), "location")
        self.assertEqual(normalize_key("hobby"), "hobbies")

    def test_single_value_keys(self):
        for key in ("preferred_name", "location", "favorite_drink", "current_project"):
            self.assertTrue(is_single_value(key), key)
        for key in ("hobbies", "liked_foods", "languages"):
            self.assertFalse(is_single_value(key), key)


class MergeRules(unittest.TestCase):
    def test_collections_append_by_default(self):
        changes = plan_changes({"hobbies": ["trekking"]}, [remember("hobbies", "futsal")])
        self.assertEqual(changes, [("add", "hobbies", "futsal")])

    def test_single_value_keys_are_replaced(self):
        changes = plan_changes({"location": ["Pokhara"]}, [remember("city", "Kathmandu")])
        self.assertEqual(changes, [("set", "location", "Kathmandu")])

    def test_known_values_are_not_added_twice(self):
        self.assertEqual(plan_changes({"hobbies": ["Futsal"]}, [remember("hobbies", "futsal")]), [])

    def test_liking_something_new_keeps_the_favourite(self):
        changes = plan_changes({"favorite_drink": ["milk tea"]}, [remember("liked_drinks", "coffee")])
        self.assertEqual(changes, [("add", "liked_drinks", "coffee")])

    def test_correction_to_a_collection_is_forget_plus_remember(self):
        changes = plan_changes({"hobbies": ["trekking", "futsal"]},
                               [forget("hobbies", "futsal"), remember("hobbies", "cricket")])
        self.assertEqual(changes, [("delete", "hobbies", "futsal"), ("add", "hobbies", "cricket")])

    def test_forget_whole_key_and_unknown_key(self):
        self.assertEqual(plan_changes({"hobbies": ["a", "b"]}, [forget("hobbies", "*")]),
                         [("delete", "hobbies", None)])
        self.assertEqual(plan_changes({}, [forget("hobbies", "*")]), [])

    def test_forget_finds_a_value_filed_under_another_key(self):
        changes = plan_changes({"liked_drinks": ["Coffee"]}, [forget("favorite_drink", "coffee")])
        self.assertEqual(changes, [("delete", "liked_drinks", "Coffee")])

    def test_operations_in_one_message_see_each_other(self):
        changes = plan_changes({}, [remember("hobbies", "chess"), remember("hobbies", "chess"),
                                    remember("preferred_name", "Ash"), remember("preferred_name", "Asmin")])
        self.assertEqual(changes, [("add", "hobbies", "chess"), ("set", "preferred_name", "Ash"),
                                   ("set", "preferred_name", "Asmin")])

    def test_invalid_operations_are_skipped(self):
        self.assertEqual(plan_changes({}, [remember("hobbies", ""), remember("x" * 100, "v"),
                                           remember("bio", "y" * 400)]), [])


class Storage(unittest.TestCase):
    def setUp(self):
        self._old_path, self._old_conn = settings.database_path, db._conn
        self.tmp = tempfile.mkdtemp()
        settings.database_path = os.path.join(self.tmp, "test.db")
        db._conn = None
        db.init_db()
        self.uid = db.create_user("memory_test", "x")["id"]

    def tearDown(self):
        db._conn.close()
        db._conn, settings.database_path = self._old_conn, self._old_path

    def test_add_set_delete_and_grouping(self):
        db.add_fact(self.uid, "hobbies", "trekking")
        db.add_fact(self.uid, "hobbies", "Futsal")
        db.add_fact(self.uid, "hobbies", "futsal")  # duplicate, ignoring case
        db.set_fact(self.uid, "location", "Pokhara")
        db.set_fact(self.uid, "location", "Kathmandu")
        mem = {m["memory_key"]: m for m in db.list_memories(self.uid)}
        self.assertEqual(mem["hobbies"]["values"], ["trekking", "Futsal"])
        self.assertEqual(mem["hobbies"]["memory_value"], "trekking; Futsal")
        self.assertEqual(mem["location"]["values"], ["Kathmandu"])
        self.assertEqual(db.delete_fact(self.uid, "hobbies", "FUTSAL"), 1)
        self.assertEqual(db.delete_fact(self.uid, "location"), 1)
        self.assertEqual({m["memory_key"]: m["values"] for m in db.list_memories(self.uid)},
                         {"hobbies": ["trekking"]})

    def test_old_memories_are_migrated_once(self):
        with db.tx() as c:
            c.execute("DELETE FROM migrations")
            for key, value in (("hobbies", json.dumps(["trekking", "futsal"])), ("preferred_name", "Ash")):
                c.execute("INSERT INTO user_memory (id, user_id, memory_key, memory_value, created_at, updated_at) "
                          "VALUES (?, ?, ?, ?, 't', 't')", (key, self.uid, key, value))
        db.init_db()
        self.assertEqual({m["memory_key"]: m["values"] for m in db.list_memories(self.uid)},
                         {"hobbies": ["trekking", "futsal"], "preferred_name": ["Ash"]})
        db.delete_fact(self.uid, "hobbies")
        db.init_db()  # already migrated: deleted facts must not come back
        self.assertNotIn("hobbies", {m["memory_key"] for m in db.list_memories(self.uid)})


if __name__ == "__main__":
    unittest.main()
