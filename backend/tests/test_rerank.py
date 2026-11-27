"""Reranker selection, per-provider thresholds and fallbacks (no API calls).

    python -m unittest backend.tests.test_rerank -v
"""
import unittest
from unittest import mock

from backend.config.settings import settings
from backend.graph import nodes
from services.document import rerank


def hits(n=3):
    return [{"id": str(i), "content": f"passage {i}", "source": "a.pdf"} for i in range(n)]


class Thresholds(unittest.TestCase):
    def test_zones(self):
        with mock.patch.object(settings, "voyage_relevance_high", 0.6),              mock.patch.object(settings, "voyage_relevance_low", 0.4):
            self.assertEqual(rerank.zone(0.3), "low")
            self.assertEqual(rerank.zone(0.5), "mid")
            self.assertEqual(rerank.zone(0.7), "high")
            self.assertEqual(rerank.zone(None), "low")


class Voyage(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(settings, "rerank_enabled", True),
                   mock.patch.object(settings, "voyage_api_key", "test-key")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_scores_and_sorts(self):
        with mock.patch.object(rerank, "_score_voyage", return_value=[0.2, 0.9, 0.5]):
            ranked = rerank.score("q", hits())
        self.assertEqual([h["id"] for h in ranked], ["1", "2", "0"])
        self.assertTrue(all(h["scorer"] == "voyage" for h in ranked))

    def test_failure_returns_none(self):
        with mock.patch.object(rerank, "_score_voyage", side_effect=RuntimeError("timeout")):
            self.assertIsNone(rerank.score("q", hits()))

    def test_not_available_without_a_key(self):
        with mock.patch.object(settings, "voyage_api_key", ""),              mock.patch.object(rerank, "_score_voyage") as voyage:
            self.assertFalse(rerank.available())
            self.assertIsNone(rerank.score("q", hits()))
        voyage.assert_not_called()


class GradeFallback(unittest.TestCase):
    def test_failed_rerank_falls_back_to_the_llm_check_on_a_few_candidates(self):
        state = {"standalone_query": "q", "documents": hits(20)}
        # (the Document service could not rerank: its candidates come back without a "relevance")
        with mock.patch.object(nodes.doc_rag, "available", return_value=True), \
             mock.patch.object(nodes, "grade_documents", side_effect=lambda q, d: (d[:2], [])) as grade:
            update = nodes.grade_node(state)
        self.assertEqual(len(grade.call_args[0][1]), settings.retrieval_k + 2)
        self.assertEqual(len(update["documents"]), 2)
        self.assertEqual(update["match_zone"], "")

    def test_voyage_scores_use_voyage_thresholds(self):
        # (candidates as the Document service returns them: reranked best first, the MMR picks numbered)
        ranked = [dict(h, relevance=r, scorer="voyage", **({"selected": n} if n else {}))
                  for h, r, n in zip(hits(), (0.45, 0.3, 0.2), (1, 2, 0))]
        with mock.patch.object(settings, "voyage_relevance_high", 0.6), \
             mock.patch.object(settings, "voyage_relevance_low", 0.4), \
             mock.patch.object(nodes.doc_rag, "available", return_value=True):
            update = nodes.grade_node({"standalone_query": "q", "documents": ranked})
        self.assertEqual(update["match_zone"], "mid")
        self.assertEqual(update["documents"], [])
        self.assertIn("with Voyage", update["trace"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
