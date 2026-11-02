"""The streamed chat endpoint keeps working, and saves the answer, when the client goes away (no API calls).

    python -m unittest backend.tests.test_stream -v
"""
import threading
import time
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import get_current_user
from backend.api.routes import chat

USER = {"id": "u1", "username": "tester"}


def slow_agent(*args, **kwargs):
    yield "status", {"node": "router", "label": "Reading your message…"}
    time.sleep(0.3)  # the client disconnects while the agent is still working
    yield "step", {"id": "s1", "node": "router", "detail": "done", "data": {}}
    yield "final", {"answer": "The answer.", "tool": "direct", "intent": "direct", "trace": []}


class StreamSurvivesDisconnect(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(chat.router)
        app.dependency_overrides[get_current_user] = lambda: USER
        self.client = TestClient(app)
        self.saved = threading.Event()
        self.saved_state = {}

        def fake_persist(conv, state, *args, **kwargs):
            self.saved_state.update(state)
            self.saved.set()
            return {"conversation_id": conv["id"], "message_id": "m1", "answer": state["answer"]}

        conv = {"id": "c1", "title": "t"}
        self.patches = [
            mock.patch.object(chat, "_prepare", return_value=(conv, [], [], [])),
            mock.patch.object(chat, "stream_agent", side_effect=slow_agent),
            mock.patch.object(chat, "_persist", side_effect=fake_persist),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_full_stream_ends_with_final(self):
        body = self.client.post("/chat/stream", json={"message": "hi"}).text
        self.assertIn("event: meta", body)
        self.assertIn("event: final", body)
        self.assertTrue(self.saved.is_set())

    def test_answer_is_saved_after_the_client_disconnects(self):
        with self.client.stream("POST", "/chat/stream", json={"message": "hi"}) as response:
            first = next(response.iter_lines())
            self.assertTrue(first.startswith("event: meta"))
        # The connection is closed before the agent finished; the answer must still be saved.
        self.assertTrue(self.saved.wait(timeout=5), "the answer was not saved after the disconnect")
        self.assertEqual(self.saved_state["answer"], "The answer.")


if __name__ == "__main__":
    unittest.main()
