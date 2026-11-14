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

    def test_short_answer_is_kept(self):
        # A 37-character answer used to be dropped as "no answer" (benchmark wiki-bm-stands, 2026-11-14).
        self.assertEqual(self._generate("BM stands for “best matching” [1]."), "BM stands for “best matching” [1].")
        self.assertIsNone(self._generate(rag.NO_ANSWER + "."))
        self.assertIsNone(self._generate("  "))
        self.assertIsNone(self._generate("Sorry. " + rag.NO_ANSWER))

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

    def test_web_answers_use_the_code_check_unless_they_admit_a_gap(self):
        results = [{"content": "Mount Everest is 8,848.86 metres tall, as measured by Nepal and China in 2020."}]
        verdict = {"sufficient": True, "grounded": True, "score": 5, "missing": ""}
        base = {"tool": "web_search", "standalone_query": "q", "web_results": results, "sources": [], "notices": []}
        ok = "Mount Everest is 8 848.86 metres tall, measured by Nepal and China in 2020 [1]."
        gap = ok + "\n\nThe search results don't give its height in 1950."
        with mock.patch.object(nodes, "evaluate_answer", return_value=verdict) as ev:
            out = nodes.evaluate_node(dict(base, answer=ok))
        ev.assert_not_called()
        self.assertEqual(out["evaluation"]["checked_by"], "code")
        for answer in (gap, ok.replace("2020", "2021")):  # partial answer; year not in the result
            with mock.patch.object(nodes, "evaluate_answer", return_value=verdict) as ev:
                nodes.evaluate_node(dict(base, answer=answer))
            ev.assert_called_once()

    def test_every_answer_writer_keeps_the_model_private(self):
        from backend.tools import IDENTITY, FORMAT_RULES, direct_answer, rag, web_search
        prompts = {"direct": direct_answer.system_prompt(False), "open": direct_answer.system_prompt(True),
                   "documents": rag.SYSTEM, "web": web_search.SYSTEM.format(today="x", format_rules=FORMAT_RULES)}
        for name, prompt in prompts.items():
            self.assertIn(IDENTITY.strip(), prompt, name)

    def test_checker_reads_month_abbreviations(self):
        from backend.evaluation import citations
        r = citations.check("Python 3.15.1 was released on 21 October 2026 [1].", ["Python 3.15.1 - Oct. 21, 2026"])
        self.assertTrue(r.ok, r.problems)

    def test_gate_history_is_capped(self):
        from backend.tools import direct_answer
        long_answer = "word " * 2000
        history = [{"role": "user", "content": "q"}, {"role": "assistant", "content": long_answer}]
        with mock.patch.object(direct_answer, "structured") as structured:
            direct_answer._run_gate("and then?", history, [])
        prompt = structured.return_value.with_config.return_value.invoke.call_args[0][0]
        self.assertLess(len(prompt), len(direct_answer.GATE_PROMPT) + 2500)


class RouterSkippedInCode(unittest.TestCase):
    """Messages the router model doesn't need to see (each skip saves ~1,000 tokens)."""

    def _route(self, query: str, docs: list[str] | None = None, history: list[dict] | None = None):
        """The update if decided in code, None if the router model was asked."""
        with mock.patch.object(nodes, "verified_decision", side_effect=RuntimeError("router called")) as router:
            update = nodes.router_node({"query": query, "doc_names": docs or [], "history": history or []})
        return None if router.called else update

    def test_plain_greetings_and_thanks_go_to_small_talk(self):
        for q in ("hi", "Hello!", "hey there", "good morning", "thanks a lot!", "Thank you so much.",
                  "thx", "bye", "see you later", "hi, hello"):
            update = self._route(q)
            self.assertIsNotNone(update, q)
            self.assertEqual(update["intent"], "greeting", q)
            self.assertFalse(update["confidence_required"], q)

    def test_greetings_with_a_question_still_go_to_the_router(self):
        for q in ("hi, what is BM25?", "thanks, but why is that?", "hello, are you sure?", "again",
                  "thank you for nothing, that answer was wrong"):
            self.assertIsNone(self._route(q, docs=["report.pdf"]), q)

    def test_first_message_asking_for_documents_goes_to_rag(self):
        update = self._route("According to my documents, how many districts are there?", docs=["report.pdf"])
        self.assertEqual(update["intent"], "rag")
        self.assertEqual(update["standalone_query"], "According to my documents, how many districts are there?")

    def test_document_request_needs_the_router_after_the_first_message_or_without_uploads(self):
        q = "what do my notes say about it?"
        self.assertIsNone(self._route(q, docs=["report.pdf"], history=[{"role": "user", "content": "x"}]))
        self.assertIsNone(self._route(q, docs=[]))


class EpisodesOnlyForTheAnswer(unittest.TestCase):
    MEMORIES = [
        {"memory_key": "name", "memory_value": "Asha", "memory_source": "sqlite"},
        {"memory_key": "past_conversation_episodic", "memory_value": "User asked about BM25.",
         "memory_source": "semantic"},
    ]

    def test_router_and_gate_get_long_episodes_shortened(self):
        from backend.tools import DECISION_EPISODE_CHARS, memories_for_decision
        long_episode = {"memory_key": "past_conversation_episodic", "memory_value": "e" * 800,
                        "memory_source": "semantic"}
        kept = memories_for_decision(self.MEMORIES + [long_episode], "what is tf-idf?")
        self.assertEqual([m["memory_value"] for m in kept[:2]], ["Asha", "User asked about BM25."])  # short: whole
        self.assertLessEqual(len(kept[2]["memory_value"]), DECISION_EPISODE_CHARS + 2)
        self.assertEqual(len(long_episode["memory_value"]), 800)  # the answer writer's copy is untouched

    def test_episodes_kept_when_the_message_refers_to_a_past_conversation(self):
        from backend.tools import memories_for_decision
        for q in ("what did you recommend last time?", "do you remember what we discussed?",
                  "in our previous chat you said something about BM25"):
            self.assertEqual(len(memories_for_decision(self.MEMORIES, q)), 2, q)

    def test_router_prompt_keeps_short_episodes(self):
        decision = nodes.Route(
            message_type="information_request", source_preference="model_knowledge", reasoning="r",
            query_is_clear=True, standalone_query="q", confidence="high", confidence_required=True)
        with mock.patch.object(nodes, "verified_decision", return_value=(decision, [])) as router:
            nodes.router_node({"query": "what is tf-idf?", "doc_names": [], "memories": self.MEMORIES})
        prompt = router.call_args.args[0]
        self.assertIn("name: Asha", prompt)
        self.assertIn("BM25", prompt)  # a message can refer back without saying "last time"

    def test_answer_writer_still_gets_episodes(self):
        from backend.tools import direct_answer
        messages = direct_answer._messages("q", [], self.MEMORIES)
        self.assertIn("User asked about BM25.", messages[1].content)


class SmallTalkPrompt(unittest.TestCase):
    def test_small_talk_uses_the_short_prompt_with_the_persona(self):
        from backend.tools import IDENTITY, direct_answer
        with mock.patch.object(direct_answer, "invoke_answer", return_value="Hi!") as invoke:
            direct_answer.converse("hi", [], "Asha", 2, [])
        system = invoke.call_args.args[0][0].content
        self.assertIn(IDENTITY.strip(), system)
        self.assertIn("Asha", system)
        self.assertNotIn("## Accuracy", system)
        self.assertLess(len(system), len(direct_answer.system_prompt(False)))


class MemoryBatching(unittest.TestCase):
    """Fact extraction batched per conversation pause (backend/memory/batching.py). No API calls."""

    def _batcher(self, max_pending=3, max_age=2):
        from backend.memory.batching import MemoryBatcher
        self.calls = []
        return MemoryBatcher(lambda uid, turns: self.calls.append((uid, [t["message"] for t in turns])),
                             idle_seconds=3600, max_pending=max_pending, max_age=max_age)

    def test_messages_wait_until_the_batch_is_full(self):
        b = self._batcher()
        b.add("u", "c", "I live in Pokhara.", [])
        b.add("u", "c", "I play futsal.", [])
        self.assertEqual(self.calls, [])
        b.add("u", "c", "My favourite drink is milk tea.", [])
        self.assertEqual(self.calls, [("u", ["I live in Pokhara.", "I play futsal.",
                                             "My favourite drink is milk tea."])])
        self.assertFalse(b.has_pending("u"))

    def test_messages_without_facts_are_not_queued(self):
        b = self._batcher()
        self.assertFalse(b.add("u", "c", "what is BM25?", []))
        self.assertFalse(b.has_pending("u"))

    def test_flushed_before_the_fact_leaves_the_history_window(self):
        b = self._batcher(max_age=2)
        b.add("u", "c", "I live in Pokhara.", [])
        b.add("u", "c", "what is BM25?", [])
        self.assertEqual(self.calls, [])
        b.add("u", "c", "and tf-idf?", [])
        self.assertEqual(self.calls, [("u", ["I live in Pokhara."])])

    def test_another_conversation_flushes_the_previous_one_first(self):
        b = self._batcher()
        b.add("u", "c1", "I live in Pokhara.", [])
        self.assertTrue(b.has_pending("u", other_than_conversation="c2"))
        self.assertFalse(b.has_pending("u", other_than_conversation="c1"))
        b.add("u", "c2", "I play futsal.", [])
        self.assertEqual(self.calls, [("u", ["I live in Pokhara."])])
        b.flush_all()
        self.assertEqual(self.calls[-1], ("u", ["I play futsal."]))

    def test_one_pending_message_uses_the_single_message_prompt(self):
        with mock.patch.object(memory, "extract_and_save_memories", return_value=[]) as single, \
             mock.patch.object(memory, "structured") as batched:
            memory.extract_and_save_batch("u", [{"message": "I live in Pokhara.", "history": []},
                                                {"message": "what is BM25?", "history": []}])
        single.assert_called_once()
        batched.assert_not_called()

    def test_failed_batch_falls_back_to_one_call_per_message(self):
        broken = mock.Mock()
        broken.return_value.invoke.side_effect = RuntimeError("bad tool call")
        with mock.patch.object(memory, "structured", broken), \
             mock.patch.object(memory, "_grouped", return_value={}), \
             mock.patch.object(memory, "extract_and_save_memories", return_value=[]) as single:
            memory.extract_and_save_batch("u", [{"message": "I live in Pokhara.", "history": []},
                                                {"message": "I play futsal.", "history": []}])
        self.assertEqual(single.call_count, 2)

    def test_batch_prompt_keeps_the_rules(self):
        self.assertIn("DO NOT REMEMBER", memory.BATCH_PROMPT)
        self.assertIn("{conversation}", memory.BATCH_PROMPT)
        self.assertNotIn("{message}", memory.BATCH_PROMPT)


class MemoryRetry(unittest.TestCase):
    def _run(self, *replies, message="I live in Pokhara and I play futsal."):
        model = mock.Mock()
        model.return_value.invoke.side_effect = list(replies)
        saved = []
        with mock.patch.object(memory, "structured", model), \
             mock.patch.object(memory, "_grouped", return_value={}), \
             mock.patch.object(memory, "apply_changes", lambda uid, changes: saved.extend(changes)):
            memory.extract_and_save_memories("u", message, [])
        steps = [c.kwargs["config"]["metadata"]["usage_step"] for c in model.return_value.invoke.call_args_list]
        return steps, saved

    def facts(self, *values):
        return memory.MemoryExtraction(facts=[memory.MemoryOp(key="location", value=v, action="remember")
                                              for v in values])

    def test_no_tool_call_is_retried_once(self):
        steps, saved = self._run(None, self.facts("Pokhara"))
        self.assertEqual(steps, ["memory_extract", "memory_extract_retry"])
        self.assertEqual(saved, [("set", "location", "Pokhara")])

    def test_empty_list_is_retried_once_then_given_up(self):
        steps, saved = self._run(self.facts(), self.facts())
        self.assertEqual(steps, ["memory_extract", "memory_extract_retry"])
        self.assertEqual(saved, [])

    def test_no_retry_when_facts_were_found(self):
        steps, _ = self._run(self.facts("Pokhara"))
        self.assertEqual(steps, ["memory_extract"])

    def test_no_call_when_the_pre_check_rejects_the_message(self):
        steps, _ = self._run(message="what is BM25?")
        self.assertEqual(steps, [])


class PreviousAnswerCap(unittest.TestCase):
    def test_long_previous_answer_keeps_its_start_and_end(self):
        from backend.tools import history_to_messages
        answer = "START " + "x" * 5000 + " END"
        msgs = history_to_messages([{"role": "user", "content": "q" * 3000}, {"role": "assistant", "content": answer}])
        self.assertEqual(len(msgs[0].content), 3000)  # the user's message stays whole
        self.assertTrue(msgs[1].content.startswith("START"))
        self.assertTrue(msgs[1].content.endswith("END"))
        self.assertIn("omitted", msgs[1].content)
        self.assertLess(len(msgs[1].content), 1700)

    def test_normal_answers_and_code_answers_are_unchanged(self):
        from backend.tools import history_to_messages
        for answer in ("y" * 1500, "Here:\n```python\n" + "x = 1\n" * 1000 + "```"):
            self.assertEqual(history_to_messages([{"role": "assistant", "content": answer}])[0].content, answer)


class ShortEpisodes(unittest.TestCase):
    def _remember(self, question, answer):
        from backend.api.routes import chat
        state = {"answer": answer, "standalone_query": question, "tool": "direct", "intent": "direct"}
        with mock.patch.object(chat, "summarize_exchange", return_value="Summary.") as summarize, \
             mock.patch.object(chat, "save_memory") as save, \
             mock.patch.object(chat, "record_usage"), \
             mock.patch.object(chat._memory_batcher, "add"), \
             mock.patch.object(chat._episode_worker, "submit", side_effect=lambda fn, *args: fn(*args)):
            chat._remember_exchange("u", "c", question, [], state, mock.Mock(), {})
        return summarize.called, save.call_args.args[1]

    def test_short_exchange_is_saved_without_a_summary(self):
        summarized, episode = self._remember("What is the capital of Nepal?", "Kathmandu.")
        self.assertFalse(summarized)
        self.assertEqual(episode, "User asked: What is the capital of Nepal?\nAssistant answered: Kathmandu.")

    def test_long_exchange_is_summarised(self):
        summarized, episode = self._remember("Explain how BM25 ranks documents?", "z" * 900)
        self.assertTrue(summarized)
        self.assertEqual(episode, "Summary.")


def _rate_limit(text="Rate limit reached ... tokens per day (TPD). Please try again in 7m12.5s."):
    import groq
    import httpx
    return groq.RateLimitError(text, response=httpx.Response(429, request=httpx.Request("POST", "http://x")),
                               body=None)


class GroqKeyRotation(unittest.TestCase):
    """Each model moves to the next Groq key when its limit is used up. No API calls."""

    def setUp(self):
        from backend.config import llm
        self.llm = llm
        llm.key_pool.reset()
        self.addCleanup(llm.key_pool.reset)
        patcher = mock.patch.object(llm, "groq_keys", return_value=["k1", "k2", "k3", "k4"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _models(self, limited: set[int]):
        """factory(key) -> a fake model that is rate-limited on the keys in `limited`."""
        calls = []

        def factory(key):
            def call():
                calls.append(key)
                if key in limited:
                    raise _rate_limit()
                return f"answer from key {key + 1}"
            return call
        return factory, calls

    def test_reset_time_is_read_from_the_error(self):
        self.assertAlmostEqual(self.llm.cooldown_seconds(_rate_limit()), 432.5)
        self.assertEqual(self.llm.cooldown_seconds(_rate_limit("Please try again in 1h2m3s.")), 3723)
        self.assertEqual(self.llm.cooldown_seconds(_rate_limit("try again in 450ms")), 1.0)
        self.assertEqual(self.llm.cooldown_seconds(_rate_limit("no hint")), 60.0)

    def test_next_key_is_used_and_the_limited_one_skipped_afterwards(self):
        factory, calls = self._models(limited={0, 1})
        self.assertEqual(self.llm.call_with_keys("m", factory, lambda fn: fn()), "answer from key 3")
        self.assertEqual(self.llm.call_with_keys("m", factory, lambda fn: fn()), "answer from key 3")
        self.assertEqual(calls, [0, 1, 2, 2])  # keys 1 and 2 not retried while limited

    def test_limits_are_per_model(self):
        factory, _ = self._models(limited={0})
        self.llm.call_with_keys("a", factory, lambda fn: fn())
        factory_b, calls_b = self._models(limited=set())
        self.llm.call_with_keys("b", factory_b, lambda fn: fn())
        self.assertEqual(calls_b, [0])

    def test_every_key_limited_raises_the_rate_limit_for_the_backups(self):
        import groq
        factory, calls = self._models(limited={0, 1, 2, 3})
        with self.assertRaises(groq.RateLimitError):
            self.llm.call_with_keys("m", factory, lambda fn: fn())
        with self.assertRaises(groq.RateLimitError):  # known to be limited: no calls at all
            self.llm.call_with_keys("m", factory, lambda fn: fn())
        self.assertEqual(calls, [0, 1, 2, 3])

    def test_answer_falls_back_to_the_small_model_only_after_every_key(self):
        big = lambda key: mock.Mock(invoke=mock.Mock(side_effect=_rate_limit()))
        small = lambda key: mock.Mock(invoke=mock.Mock(return_value=mock.Mock(content="small")))
        with mock.patch.object(self.llm, "get_llm", side_effect=lambda t, k: big(k)) as get_big, \
             mock.patch.object(self.llm, "get_backup_writer", side_effect=lambda t, k: small(k)):
            self.assertEqual(self.llm.invoke_answer(["m"]), "small")
        self.assertEqual([c.args[1] for c in get_big.call_args_list], [0, 1, 2, 3])
        self.assertTrue(self.llm.consume_backup_flag())

    def test_structured_output_skips_the_plain_json_retry_on_a_rate_limit(self):
        from pydantic import BaseModel

        class Out(BaseModel):
            x: int

        def fake_llm(key):
            m = mock.Mock(model_name="openai/gpt-oss-20b")
            if key == 0:
                m.with_structured_output.return_value = RunnableLambda(lambda p: (_ for _ in ()).throw(_rate_limit()))
                m.invoke.side_effect = AssertionError("plain-JSON fallback called on a rate limit")
            else:
                m.with_structured_output.return_value = RunnableLambda(lambda p: Out(x=key))
            return m

        from langchain_core.runnables import RunnableLambda
        with mock.patch.object(self.llm, "get_fast_llm", side_effect=fake_llm):
            self.assertEqual(self.llm.structured(Out).invoke("prompt").x, 1)


class GateTokensLoggedSeparately(unittest.TestCase):
    def test_gate_call_is_its_own_step_inside_the_direct_node(self):
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
        from langchain_core.messages import AIMessage
        from langchain_core.runnables import RunnableLambda

        from backend.config.usage import UsageTracker
        from backend.tools import direct_answer

        def fake_structured(model_cls, **kwargs):
            chat = GenericFakeChatModel(messages=iter([AIMessage(
                "x", usage_metadata={"input_tokens": 700, "output_tokens": 50, "total_tokens": 750})]))

            def run(prompt):
                chat.invoke(prompt)  # stands in for the Groq call inside structured()
                return model_cls(question_kind="factual", reason="r", time_sensitive=False,
                                 references_user_material=False, confidence="high")
            return RunnableLambda(run)

        tracker = UsageTracker()
        with mock.patch.object(direct_answer, "structured", fake_structured):
            RunnableLambda(lambda q: direct_answer._run_gate(q)).invoke(
                "q", {"callbacks": [tracker], "metadata": {"langgraph_node": "direct"}})
        steps = tracker.summary()["by_step"]
        self.assertEqual(steps["knowledge_gate"]["input"], 700)
        self.assertNotIn("direct", steps)


if __name__ == "__main__":
    unittest.main()
