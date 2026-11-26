"""The shared Voyage rate limit: priorities and the slot kept for the reranker (no API calls).

    python -m unittest backend.tests.test_voyage -v
"""
import threading
import time
import unittest
from unittest import mock

from backend.config import voyage
from backend.config.voyage import FIND, RECALL, RERANK, SAVE, RateLimiter


class Limiter(unittest.TestCase):
    def test_recall_and_saves_leave_a_slot_for_the_reranker(self):
        lim = RateLimiter(per_minute=3, reserve=1, window=60)
        self.assertTrue(lim.acquire(RECALL, timeout=0))
        self.assertTrue(lim.acquire(SAVE, timeout=0))
        self.assertFalse(lim.acquire(SAVE, timeout=0))   # would take the last slot
        self.assertFalse(lim.acquire(RECALL, timeout=0))
        self.assertTrue(lim.acquire(RERANK, timeout=0))  # the reserved one
        self.assertFalse(lim.acquire(RERANK, timeout=0))  # the minute is used up

    def test_the_document_agents_find_also_leaves_the_reranker_its_slot(self):
        lim = RateLimiter(per_minute=3, reserve=1, window=60)
        self.assertTrue(lim.acquire(FIND, timeout=0))
        self.assertTrue(lim.acquire(FIND, timeout=0))
        self.assertFalse(lim.acquire(FIND, timeout=0))
        self.assertTrue(lim.acquire(RERANK, timeout=0))

    def test_slots_free_up_after_the_window(self):
        lim = RateLimiter(per_minute=1, reserve=0, window=0.2)
        self.assertTrue(lim.acquire(RERANK, timeout=0))
        started = time.monotonic()
        self.assertTrue(lim.acquire(RERANK, timeout=2))
        self.assertGreaterEqual(time.monotonic() - started, 0.15)

    def test_a_waiting_rerank_goes_before_an_earlier_save(self):
        lim = RateLimiter(per_minute=1, reserve=0, window=0.3)
        self.assertTrue(lim.acquire(RERANK, timeout=0))
        order = []

        def take(priority, name):
            if lim.acquire(priority, timeout=3):
                order.append(name)

        save = threading.Thread(target=take, args=(SAVE, "save"))
        save.start()
        time.sleep(0.05)  # the save is waiting first
        rerank = threading.Thread(target=take, args=(RERANK, "rerank"))
        rerank.start()
        save.join()
        rerank.join()
        self.assertEqual(order, ["rerank", "save"])

    def test_reserve_never_blocks_everything(self):
        lim = RateLimiter(per_minute=1, reserve=5, window=60)
        self.assertTrue(lim.acquire(SAVE, timeout=0))


class Call(unittest.TestCase):
    def test_busy_raises_without_calling_voyage(self):
        busy = mock.Mock()
        busy.acquire.return_value = False
        fn = mock.Mock()
        with mock.patch.object(voyage, "limiter", return_value=busy):
            with self.assertRaises(voyage.VoyageBusy):
                voyage.call(RECALL, fn)
        fn.assert_not_called()

    def test_rerank_and_memory_share_one_client_and_limiter(self):
        from backend.memory import embeddings
        from backend.rag import rerank

        lim, client = mock.Mock(), mock.Mock()
        lim.acquire.return_value = True
        client.rerank.return_value.results = []
        client.embed.return_value.embeddings = [[0.0] * embeddings.settings.voyage_memory_dimensions]
        with mock.patch.object(voyage, "limiter", return_value=lim), \
             mock.patch.object(voyage, "client", return_value=client):
            rerank._score_voyage("q", ["a"])
            embeddings.embed_memory_query("q")
            embeddings.embed_memory("text")
        self.assertEqual([c.args[0] for c in lim.acquire.call_args_list], [RERANK, RECALL, SAVE])


class Shares(unittest.TestCase):
    def test_the_two_shares_sum_to_the_total_and_each_limiter_enforces_its_own(self):
        for total, reserve in ((3, 1), (10, 4), (3, 2)):
            with mock.patch.object(voyage.settings, "voyage_requests_per_minute", total),                  mock.patch.object(voyage.settings, "voyage_rerank_reserve", reserve):
                rerank, memory = voyage.shares()
                self.assertEqual((rerank, memory), (reserve, total - reserve))
                self.assertEqual(rerank + memory, total)
                for role, share in (("rerank", rerank), ("memory", memory)):
                    with mock.patch.object(voyage, "_role", role):
                        lim = voyage.make_limiter()
                        self.assertEqual(lim.per_minute, share)
                        self.assertEqual([lim.acquire(RERANK, 0) for _ in range(share + 1)],
                                         [True] * share + [False])

    def test_each_share_is_at_least_one(self):
        with mock.patch.object(voyage.settings, "voyage_requests_per_minute", 1):
            self.assertEqual(voyage.shares(), (1, 1))


if __name__ == "__main__":
    unittest.main()
