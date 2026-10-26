"""Unit tests for routing, fallback, answer handling and search-query rules. No API calls.

    python -m unittest backend.tests.test_routing -v
"""
import unittest
from unittest import mock

from backend.config.settings import settings
from backend.graph import nodes, workflow
from backend.tools import memory, rag, web_search


def order(preference: str, docs: bool = True) -> list[str]:
    """Every source the fallback chain visits, in order."""
    state = {"source_preference": preference, "doc_names": ["a.pdf"] if docs else [], "tried_sources": []}
    visited = []
    while (source := nodes.next_source(state)) is not None:
        visited.append(source)
        state["tried_sources"] = visited
    return visited


class SourceOrder(unittest.TestCase):
    def test_orders_follow_the_users_preference(self):
        self.assertEqual(order("documents"), ["rag", "direct", "web_search"])
        self.assertEqual(order("model_knowledge"), ["direct", "rag", "web_search"])
        self.assertEqual(order("web"), ["web_search", "rag", "direct"])
        self.assertEqual(order("auto"), ["rag", "direct", "web_search"])

    def test_documents_are_skipped_when_the_user_has_none(self):
        for preference in ("documents", "model_knowledge", "web", "auto"):
            self.assertNotIn("rag", order(preference, docs=False), preference)

    def test_graph_uses_the_single_fallback_node(self):
        self.assertFalse(hasattr(workflow, "fallback_node"))
        self.assertFalse(hasattr(workflow, "SOURCE_ORDERS"))

    def test_fallback_resets_source_state_and_adds_notice(self):
        update = nodes.fallback_node({
            "intent": "rag", "query": "q", "standalone_query": "q", "source_preference": "auto",
            "doc_names": ["a.pdf"], "tried_sources": ["rag"], "notices": [],
            "documents": [{"x": 1}], "attempts": 2,
        })
        self.assertEqual(update["intent"], "direct")
        self.assertEqual(update["attempts"], 0)
        self.assertEqual(update["documents"], [])
        self.assertIn(nodes.FALLBACK_NOTICES[("rag", "direct")], update["notices"])

    def test_exhausted_when_every_source_was_tried(self):
        update = nodes.fallback_node({
            "intent": "web_search", "source_preference": "auto", "doc_names": [],
            "tried_sources": ["direct", "web_search"],
        })
        self.assertEqual(update["intent"], "exhausted")


class RouterAuto(unittest.TestCase):
    def _route(self, preference: str, docs: list[str]) -> str:
        decision = nodes.Route(
            message_type="information_request", source_preference=preference, reasoning="r",
            query_is_clear=True, standalone_query="what does the report say?", confidence="high",
            confidence_required=True,
        )
        with mock.patch.object(nodes, "verified_decision", return_value=(decision, [])):
            return nodes.router_node({"query": "what does the report say?", "doc_names": docs})["intent"]

    def test_auto_starts_with_model_knowledge_even_with_documents(self):
        # Starting with documents sent casual questions into RAG whenever any were uploaded.
        self.assertEqual(self._route("auto", ["report.pdf"]), "direct")
        self.assertEqual(self._route("auto", []), "direct")

    def test_documents_preference_goes_to_rag(self):
        self.assertEqual(self._route("documents", ["report.pdf"]), "rag")

    def test_auto_reaches_documents_after_the_gate_refuses(self):
        # direct was tried (gate refused: the question refers to the user's material) -> documents next.
        state = {"source_preference": "auto", "doc_names": ["report.pdf"], "tried_sources": ["direct"]}
        self.assertEqual(nodes.next_source(state), "rag")


class RetryBudgets(unittest.TestCase):
    def test_partial_match_gets_its_reworded_retry(self):
        state = {"intent": "rag", "match_zone": "mid", "attempts": 1}
        with mock.patch.object(settings, "max_retrieval_attempts", 1), \
             mock.patch.object(settings, "weak_match_retries", 1):
            self.assertEqual(workflow.route_after_grade(state), "rewrite")
            self.assertEqual(workflow.route_after_grade({**state, "attempts": 2}), "fallback")

    def test_strong_and_weak_matches(self):
        self.assertEqual(workflow.route_after_grade({"intent": "rag", "match_zone": "high", "attempts": 1}),
                         "generate_rag")
        self.assertEqual(workflow.route_after_grade({"intent": "rag", "match_zone": "low", "attempts": 1}),
                         "fallback")


class RagSentinel(unittest.TestCase):
    def _generate(self, reply: str):
        docs = [{"source": "a.pdf", "page": 1, "content": "text"}]
        with mock.patch.object(rag, "invoke_answer", return_value=reply):
            return rag.generate("q", docs, [])

    def test_bare_sentinel_means_no_answer(self):
        self.assertIsNone(self._generate(rag.NO_ANSWER))

    def test_partial_answer_with_sentinel_is_kept(self):
        reply = ("The project was supervised by Mr. Pratik Joshi [1] and used an SVM classifier [2]. "
                 + rag.NO_ANSWER)
        text = self._generate(reply)
        self.assertIsNotNone(text)
        self.assertIn("Pratik Joshi", text)
        self.assertNotIn(rag.NO_ANSWER, text)


class EvaluatorUnavailable(unittest.TestCase):
    def test_unchecked_answer_is_kept_and_flagged(self):
        state = {"tool": "rag", "answer": "An answer [1].", "standalone_query": "q", "documents": [],
                 "sources": [], "notices": []}
        with mock.patch.object(nodes, "evaluate_answer", side_effect=RuntimeError("rate limited")):
            update = nodes.evaluate_node(state)
        self.assertTrue(update["evaluation"]["sufficient"])
        self.assertFalse(update["evaluation"]["verified"])
        self.assertIn(nodes.UNVERIFIED_NOTICE, update["notices"])
        self.assertEqual(update["best"]["answer"], "An answer [1].")
        self.assertEqual(workflow.route_after_evaluate({**state, **update}), "finalize")


class WebQueries(unittest.TestCase):
    def test_recency_questions_get_the_year_not_the_news_filter(self):
        q = "what is the latest stable Python release?"
        self.assertFalse(web_search._is_news_query(q))
        self.assertRegex(web_search._with_year(q), r" 20\d\d$")

    def test_queries_with_a_year_or_no_recency_are_unchanged(self):
        for q in ("who won the 2022 world cup", "what is BM25", "top tech news headlines today"):
            self.assertEqual(web_search._with_year(q), q)

    def test_news_window(self):
        self.assertTrue(web_search._is_news_query("top tech news headlines today"))
        self.assertTrue(web_search._TODAY_TERMS.search("top tech news headlines today"))
        self.assertFalse(web_search._TODAY_TERMS.search("news about the Nepal election"))


class MemoryPrecheck(unittest.TestCase):
    def test_skips_messages_with_nothing_personal(self):
        for message in ("thanks, that's helpful!", "what is BM25?", "explain the GIL"):
            self.assertFalse(memory._may_contain_memory(message), message)

    def test_runs_on_personal_statements(self):
        for message in ("milk tea is my favourite", "I play futsal", "remember that I'm vegetarian",
                        "call me Ash"):
            self.assertTrue(memory._may_contain_memory(message), message)


if __name__ == "__main__":
    unittest.main()
