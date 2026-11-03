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
            query_is_clear=True, standalone_query="is it worth learning Rust?", confidence="high",
            confidence_required=True,
        )
        # A neutral message: "what does the report say?" is now an explicit document request (asks_for_documents).
        with mock.patch.object(nodes, "verified_decision", return_value=(decision, [])):
            return nodes.router_node({"query": "is it worth learning Rust?", "doc_names": docs})["intent"]

    def test_auto_starts_with_model_knowledge_even_with_documents(self):
        # Starting with documents sent casual questions into RAG whenever any were uploaded.
        self.assertEqual(self._route("auto", ["report.pdf"]), "direct")
        self.assertEqual(self._route("auto", []), "direct")

    def test_documents_preference_goes_to_rag(self):
        self.assertEqual(self._route("documents", ["report.pdf"]), "rag")

    def test_explicit_document_request_overrides_the_router(self):
        # The router once chose model_knowledge for "According to my documents, ..." with high confidence.
        decision = nodes.Route(
            message_type="information_request", source_preference="model_knowledge", reasoning="r",
            query_is_clear=True, standalone_query="q", confidence="high", confidence_required=True)
        query = "According to my documents, how many local governments does Nepal have?"
        with mock.patch.object(nodes, "verified_decision", return_value=(decision, [])):
            out = nodes.router_node({"query": query, "doc_names": ["who.pdf"]})
        self.assertEqual((out["intent"], out["source_preference"]), ("rag", "documents"))

    def test_document_request_rule(self):
        docs = ["okapi_bm25.md"]
        for q in ["According to my documents, how many local governments does Nepal have?",
                  "what do my notes say about tf-idf?", "pull the definition from the regulation I uploaded",
                  "according to the uploaded paper, what accuracy did it get?", "in okapi_bm25.md what is k1?",
                  "what does the WHO document say about primary care?"]:
            self.assertTrue(nodes.asks_for_documents(q, docs), q)
        for q in ["Without my notes, explain stemming.", "Answer from your own knowledge, not my documents: what is GDPR?",
                  "What should I put in my notes for a lecture?", "I uploaded my CV yesterday, any interview tips?",
                  "how do I organise my files on Windows?", "instead of my documents, search the web for BM25",
                  "What does the Bible say about forgiveness?"]:
            self.assertFalse(nodes.asks_for_documents(q, docs), q)
        # "what does the X document say" counts only when the user has uploaded files
        self.assertFalse(nodes.asks_for_documents("what does the WHO document say about primary care?", []))

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


class KnowledgeGateRules(unittest.TestCase):
    def _gate(self, kind, confidence, time_sensitive=False, user_material=False):
        from backend.tools.direct_answer import KnowledgeGate
        return KnowledgeGate(question_kind=kind, reason="", time_sensitive=time_sensitive,
                             references_user_material=user_material, confidence=confidence)

    def test_open_questions_are_answered_at_any_confidence(self):
        from backend.tools.direct_answer import _gate_passes
        for confidence in ("high", "medium", "low"):
            self.assertTrue(_gate_passes(self._gate("open", confidence)), confidence)

    def test_factual_questions_need_high_confidence(self):
        from backend.tools.direct_answer import _gate_passes
        self.assertTrue(_gate_passes(self._gate("factual", "high")))
        self.assertFalse(_gate_passes(self._gate("factual", "medium")))

    def test_current_information_and_user_files_always_go_elsewhere(self):
        from backend.tools.direct_answer import _gate_passes
        self.assertFalse(_gate_passes(self._gate("open", "high", time_sensitive=True)))
        self.assertFalse(_gate_passes(self._gate("open", "high", user_material=True)))


class MemoryPrecheck(unittest.TestCase):
    def test_skips_messages_with_nothing_personal(self):
        for message in ("thanks, that's helpful!", "what is BM25?", "explain the GIL"):
            self.assertFalse(memory._may_contain_memory(message), message)

    def test_runs_on_personal_statements(self):
        for message in ("milk tea is my favourite", "I play futsal", "remember that I'm vegetarian",
                        "call me Ash", "From now on, keep your answers short.", "Keep your replies brief",
                        "I live in Pokhara, any good cafes?", "Help me, I'm a beginner at Python",
                        "my birthday is on March 3", "Can you remember that I'm vegetarian?"):
            self.assertTrue(memory._may_contain_memory(message), message)

    def test_skips_questions_and_requests_that_only_mention_i_or_my(self):
        for message in ("how do I center a div", "What's my favourite colour?", "If I were a doctor, which field?",
                        "Give me an overview of BM25", "check my files: what does article 17 say",
                        "show me one in javascript", "How do I calculate a moving average in pandas?"):
            self.assertFalse(memory._may_contain_memory(message), message)


class StructuredOutputRecovery(unittest.TestCase):
    def test_misnamed_tool_call_is_recovered_from_the_rejected_generation(self):
        import groq
        from langchain_core.runnables import RunnableLambda
        from backend.config.llm import _recover_tool_call
        generation = ('<tool_call>\n<function=MemoryExclusion>\n<parameter=facts>\n'
                      '[{"key": "liked_foods", "value": "pizza", "action": "forget"}]\n</parameter>\n</function>')

        def fail(_):
            raise groq.BadRequestError("tool call validation failed", response=mock.Mock(status_code=400),
                                       body={"error": {"code": "tool_use_failed", "failed_generation": generation}})
        result = _recover_tool_call(RunnableLambda(fail), memory.MemoryExtraction).invoke("prompt")
        self.assertEqual([(f.key, f.value, f.action) for f in result.facts], [("liked_foods", "pizza", "forget")])

    def test_call_without_arguments_goes_to_the_next_fallback(self):
        import groq
        from langchain_core.runnables import RunnableLambda
        from backend.config.llm import _recover_tool_call

        def fail(_):
            raise groq.BadRequestError("tool call validation failed", response=mock.Mock(status_code=400),
                                       body={"error": {"failed_generation": "<function=MemoryExcovery>\n</function>"}})
        with self.assertRaises(groq.BadRequestError):
            _recover_tool_call(RunnableLambda(fail), memory.MemoryExtraction).invoke("prompt")


class TokenTrims(unittest.TestCase):
    def test_checker_gets_only_the_cited_passages(self):
        docs = [{"source": "a.pdf", "content": f"passage {i}"} for i in range(1, 7)]
        cited = nodes._cited(docs, "Flask serves it [2], MySQL stores it [5, 6].")
        self.assertEqual([i for i, _ in cited], [2, 5, 6])

    def test_checker_gets_everything_when_nothing_usable_is_cited(self):
        docs = [{"source": "a.pdf", "content": "x"}] * 3
        self.assertEqual(len(nodes._cited(docs, "No citations here.")), 3)
        self.assertEqual(len(nodes._cited(docs, "Out of range [9].")), 3)

    def test_evaluator_context_keeps_the_original_numbers(self):
        docs = [{"source": "a.pdf", "page": i, "content": f"passage {i}"} for i in range(1, 5)]
        state = {"tool": "rag", "answer": "It is X [3].", "standalone_query": "q", "documents": docs,
                 "sources": [], "notices": []}
        with mock.patch.object(nodes, "evaluate_answer", return_value={"sufficient": True, "grounded": True,
                                                                        "score": 5, "missing": ""}) as ev:
            nodes.evaluate_node(state)
        context = ev.call_args[0][2]
        self.assertIn("[3] (source: a.pdf, page 3)", context)
        self.assertNotIn("passage 1", context)

    def test_cited_answer_on_strong_evidence_skips_the_model_check(self):
        docs = [{"source": "a.pdf", "content": "Nepal has seven provincial governments and 753 local governments."}]
        state = {"tool": "rag", "answer": "Nepal has 753 local governments [1].", "standalone_query": "q",
                 "documents": docs, "sources": [], "notices": [], "match_zone": "high"}
        with mock.patch.object(nodes, "evaluate_answer") as ev:
            out = nodes.evaluate_node(state)
        ev.assert_not_called()
        self.assertEqual(out["evaluation"]["checked_by"], "code")
        self.assertTrue(out["evaluation"]["sufficient"])

    def test_flagged_or_weak_evidence_answers_go_to_the_model(self):
        docs = [{"source": "a.pdf", "content": "Nepal has seven provincial governments and 753 local governments."}]
        verdict = {"sufficient": True, "grounded": True, "score": 5, "missing": ""}
        for answer, zone in [("Nepal has 760 local governments [1].", "high"),   # number not in the passage
                             ("Nepal has 753 local governments [1].", "mid"),    # evidence not strong
                             ("Nepal has 753 local governments [1].", "")]:      # reranker unavailable
            state = {"tool": "rag", "answer": answer, "standalone_query": "q", "documents": docs,
                     "sources": [], "notices": [], "match_zone": zone}
            with mock.patch.object(nodes, "evaluate_answer", return_value=verdict) as ev:
                nodes.evaluate_node(state)
            ev.assert_called_once()
        with mock.patch.object(settings, "citation_precheck", False),                 mock.patch.object(nodes, "evaluate_answer", return_value=verdict) as ev:
            nodes.evaluate_node(dict(state, answer="Nepal has 753 local governments [1].", match_zone="high"))
        ev.assert_called_once()

    def test_gate_history_is_capped(self):
        from backend.tools import direct_answer
        long_answer = "word " * 2000
        history = [{"role": "user", "content": "q"}, {"role": "assistant", "content": long_answer}]
        with mock.patch.object(direct_answer, "structured") as structured:
            direct_answer._run_gate("and then?", history, [])
        prompt = structured.return_value.with_config.return_value.invoke.call_args[0][0]
        self.assertLess(len(prompt), len(direct_answer.GATE_PROMPT) + 2500)


if __name__ == "__main__":
    unittest.main()
