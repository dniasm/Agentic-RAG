"""Reranker selection, per-provider thresholds and fallbacks (no API calls).

    python -m unittest backend.tests.test_rerank -v
"""
import unittest
from unittest import mock

from backend.config.settings import settings
from backend.graph import nodes
from backend.rag import rerank


def hits(n=3):
    return [{"id": str(i), "content": f"passage {i}", "source": "a.pdf"} for i in range(n)]


class Thresholds(unittest.TestCase):
    def test_each_scorer_uses_its_own_scale(self):
        with mock.patch.object(settings, "voyage_relevance_high", 0.6), \
             mock.patch.object(settings, "voyage_relevance_low", 0.4), \
             mock.patch.object(settings, "relevance_high", 0.05), \
             mock.patch.object(settings, "relevance_low", 0.01):
            # 0.3 is a strong local score but an off-topic Voyage score.
            self.assertEqual(rerank.zone(0.3, "local"), "high")
            self.assertEqual(rerank.zone(0.3, "voyage"), "low")
            self.assertEqual(rerank.zone(0.5, "voyage"), "mid")
            self.assertEqual(rerank.zone(0.7, "voyage"), "high")
            self.assertEqual(rerank.zone(None, "voyage"), "low")


class Providers(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(settings, "rerank_enabled", True),
                   mock.patch.object(settings, "reranker_provider", "voyage"),
                   mock.patch.object(settings, "voyage_api_key", "test-key")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_voyage_scores_and_sorts(self):
        with mock.patch.object(rerank, "_score_voyage", return_value=[0.2, 0.9, 0.5]):
            ranked = rerank.score("q", hits())
        self.assertEqual([h["id"] for h in ranked], ["1", "2", "0"])
        self.assertTrue(all(h["scorer"] == "voyage" for h in ranked))

    def test_voyage_failure_uses_the_local_backup_when_loaded(self):
        with mock.patch.object(settings, "reranker_local_fallback", True), \
             mock.patch.object(rerank, "_model", object()), \
             mock.patch.object(rerank, "_score_voyage", side_effect=RuntimeError("429")), \
             mock.patch.object(rerank, "_score_local", return_value=[0.01, 0.02, 0.9]):
            ranked = rerank.score("q", hits())
        self.assertEqual(ranked[0]["id"], "2")
        self.assertEqual(ranked[0]["scorer"], "local")

    def test_voyage_failure_without_backup_returns_none(self):
        with mock.patch.object(settings, "reranker_local_fallback", False), \
             mock.patch.object(rerank, "_score_voyage", side_effect=RuntimeError("timeout")):
            self.assertIsNone(rerank.score("q", hits()))

    def test_local_model_is_not_loaded_unless_used(self):
        with mock.patch.object(settings, "reranker_local_fallback", False), \
             mock.patch("threading.Thread") as thread:
            rerank.warm_up_in_background()
        thread.assert_not_called()

    def test_available_without_a_voyage_key_or_local_model(self):
        with mock.patch.object(settings, "voyage_api_key", ""), mock.patch.object(rerank, "_model", None):
            self.assertFalse(rerank.available())


class GradeFallback(unittest.TestCase):
    def test_failed_rerank_falls_back_to_the_llm_check_on_a_few_candidates(self):
        state = {"standalone_query": "q", "documents": hits(20)}
        with mock.patch.object(nodes.rerank, "available", return_value=True), \
             mock.patch.object(nodes.rerank, "score", return_value=None), \
             mock.patch.object(nodes, "grade_documents", side_effect=lambda q, d: (d[:2], [])) as grade:
            update = nodes.grade_node(state)
        self.assertEqual(len(grade.call_args[0][1]), settings.retrieval_k + 2)
        self.assertEqual(len(update["documents"]), 2)
        self.assertEqual(update["match_zone"], "")

    def test_voyage_scores_use_voyage_thresholds(self):
        ranked = [dict(h, relevance=r, scorer="voyage") for h, r in zip(hits(), (0.45, 0.3, 0.2))]
        with mock.patch.object(settings, "voyage_relevance_high", 0.6), \
             mock.patch.object(settings, "voyage_relevance_low", 0.4), \
             mock.patch.object(nodes.rerank, "available", return_value=True), \
             mock.patch.object(nodes.rerank, "score", return_value=ranked), \
             mock.patch.object(nodes.rerank, "mmr_select", side_effect=lambda r: r[:2]):
            update = nodes.grade_node({"standalone_query": "q", "documents": hits()})
        self.assertEqual(update["match_zone"], "mid")
        self.assertEqual(update["documents"], [])
        self.assertIn("with Voyage", update["trace"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
