# Merge stage 5: one chat. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** day7's chat sends document actions (rename, edit, undo, queue, export) to the library's agent, one route per message, with the agent's questions answered in place.

**Architecture:** The router gains `document_action`, which becomes intent `manage`. A `manage` node calls `agent.chat(conversation_id, message, owner=user_id, on_step=...)` and goes straight to END (no answer checker, no episode). Code sends a message to `manage` before the router only in three narrow cases. Agent state stays in the catalog's `sessions` table, keyed by the conversation id.

**Spec:** `docs/superpowers/specs/2026-11-14-merge-renamer-design.md` (stage 5)

## Global Constraints

- One route per message. "Rename" alone is not routed in code ("how do I rename a file in Linux?" is a question).
- Code routing only for: the conversation's agent guard has `pending` renames, or a `needs_user` entry from its last turn; a bare "undo" ("undo", "undo that", "undo the last change"); and queue status ("what's in the queue", "queue status", "is the batch done").
- The agent's tokens are recorded by day7's `UsageTracker` under step `manage` (LangChain callbacks reach it through the node's config).
- The `manage` route skips the answer checker and the episode summary, but keeps the memory batcher.
- One message at a time per conversation, on every route (both /chat and /chat/stream).
- Never stage `frontend/vite.config.js`.

## Review Focus

- "yes" after "Rename these 3?" reaches the agent, not small talk.
- "rename that one" after a document answer: the previous answer's document ids reach the agent.
- A reload keeps the Preview/Undo buttons (`meta.actions`).
- Two quick messages in one conversation don't interleave.
- A question that mentions a document ("what does the GDPR doc say about consent?") still goes to RAG.

---

### Task 1: Route and node

**Files:** `backend/graph/nodes.py`, `backend/graph/state.py`, `backend/graph/workflow.py`, `backend/library/agent.py`, `backend/tools/rag.py`, `backend/tests/test_manage_route.py`.

- [ ] `Route.message_type: Literal["small_talk", "information_request", "document_action"]`, plus a DOCUMENT ACTION paragraph in `ROUTER_PROMPT`. `_route_update` maps `document_action` to intent `manage`.
- [ ] `nodes.manage_pending(state) -> str | None`: the code-routing rule above. `router_node` returns intent `manage` without a model call when it gives a reason.
- [ ] State gains `conversation_id: str`, `agent_pending: bool`, `cited_doc_ids: list[str]` and `actions: list[dict]`. `run_agent`/`stream_agent` take `conversation_id`, `agent_pending` and `cited_doc_ids`.
- [ ] `agent.chat(..., on_step=None, context_doc_ids=None)`: `on_step(name, args, result)` after each tool call; context ids are added to the message as a note the model sees ("Documents in the previous answer: …").
- [ ] `nodes.manage_node`: runs `agent.chat`, emits each tool call live (`emit_live`), and returns `answer`, `tool="manage_documents"`, `actions` and a trace. Graph: router → manage → END.
- [ ] `to_sources` keeps `doc_id`.
- [ ] Tests (scripted agent model, no network): pending guard → manage without the router; bare undo → manage; "how do I rename a file in Linux" is not code-routed; the manage node's answer and actions; context ids reach the agent.

### Task 2: Chat route

**Files:** `backend/api/routes/chat.py`, `backend/tests/test_manage_route.py`.

- [ ] `_prepare` also returns `agent_pending` (`agent.has_open_question(conv_id)`) and `cited_doc_ids` (the previous assistant message's `meta.sources` doc ids).
- [ ] `_persist` meta gains `actions`. `_remember_exchange` skips the episode when `tool == "manage_documents"`.
- [ ] `_conversation_lock(conv_id)`: one message at a time per conversation, held for the whole graph run (also in the stream's worker thread).
- [ ] Tests: actions are saved in meta; a second message waits for the first; no episode for manage.

### Task 3: Router benchmark

- [ ] `benchmark/router_cases.json` gains ≥30 cases labelled `document_action` / `information_request` (actions, questions that mention documents, borderline ones).
- [ ] With the backend stopped: `python benchmark/run_router.py`. The 80 old cases score no worse than 73 correct / 79 acceptable; borderline cases are run 5 times.
