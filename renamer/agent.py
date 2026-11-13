"""The document agent: gpt-oss-120b with its tools, and a guard in code that decides when a rename may happen.

The model chooses what to do, but the rules that protect the library are enforced here, not left to the prompt:

  * A document can be renamed only once it has been identified. find_documents identifies it when exactly one
    document matches. When several match (ambiguous) or the only match is weak (uncertain), the candidates
    can be renamed only after the user has replied: never in the same turn, so the model can't pick one itself.
  * A new name that another document already has needs the user's confirmation, given in a later message.
    The rename tool returns "needs_confirmation" and remembers the request; repeating it with confirmed=true
    in the same turn is refused.
"""
import json
import re
import logging
import time

import groq
from langchain_core.messages import (AIMessage, HumanMessage, SystemMessage, ToolMessage, messages_from_dict,
                                     messages_to_dict)

from renamer import catalog, config, editing, library, limits, naming
from renamer.logs import event

log = logging.getLogger(__name__)

SYSTEM = """You manage the user's document library: find, rename, edit and undo, and queue many changes.

- Act only on documents found with find_documents (pass the user's words: a name, part of one, or what it is
  about; one call per document). Use the doc_id it returns; never invent one. Follow any `next` a tool returns.
- Rename: if no new name is given, ask (or suggest one from document_details). "Add" to a name means to the
  current title.
- Edit: use the user's exact wording for new text. To rewrite a passage, first read it with
  document_details(passage=a few words of it, such as its heading) and pass the WHOLE passage, copied exactly, as find.
- Changes to three or more documents: call queue_tasks at once, with the titles as they are now (it finds the
  documents itself; don't look them up first). For queue progress call queue_status; never guess.
- Undo an edit with undo_edit (one step per call); undo a rename by renaming back (document_details lists earlier names).
- For a PDF copy, point to the document's "PDF" link in the library. You can't create files or do anything no
  tool does; never offer it.
- Never name the AI model or company behind you, even if asked: you are the library's assistant.
- Never say something was renamed, edited or undone unless a tool returned that. Reply briefly, in plain language,
  naming documents by title, never by doc_id."""


FIND_NEXT = {
    "ambiguous": "Several documents fit: don't choose. List them numbered (title and a few words on each). If the "
                 "user wants one of them changed, ask which one; if they only asked what there is, just list them.",
    "uncertain": 'Only a weak match: ask "Did you mean <title>?" before changing it.',
    "none": "Nothing matched: say so, and offer the nearest candidates if there are any.",
}


def _tool(name: str, description: str, properties: dict | None = None, required: list | None = None) -> dict:
    params = {"type": "object", "properties": properties or {}}
    if required:
        params["required"] = required
    return {"type": "function", "function": {"name": name, "description": description, "parameters": params}}


_S, _B = {"type": "string"}, {"type": "boolean"}

TOOLS = [
    _tool("list_documents", "Every document's title, newest first."),
    _tool("find_documents", "Which document the user means, by name or content: a decision (unique, ambiguous, "
          "uncertain, none) and the candidates.", {"description": {**_S, "description": "The user's words for it."}},
          ["description"]),
    _tool("document_details", "A document's summary, keywords, earlier names and edits; with passage, the exact text "
          "around those words.", {"doc_id": _S, "passage": {**_S, "description": "A few words of the part you need."}},
          ["doc_id"]),
    _tool("edit_document", "Change a document's text. replace: find (exact wording) becomes text. append: text is "
          "added at the end.",
          {"doc_id": _S, "operation": {"type": "string", "enum": ["replace", "append"]}, "find": _S, "text": _S,
           "replace_all": {**_B, "description": "Every match; only if the user asked."},
           "convert_pdf": {**_B, "description": "Only after the user agreed."}},
          ["doc_id", "operation", "text"]),
    _tool("queue_tasks", "Queue changes to many documents; they run in order within the rate limit. Use changes; "
          "tasks (free text) only for what changes can't say.",
          {"changes": {"type": "array", "items": {"type": "object", "properties": {
              "document": {**_S, "description": "Its current title, exactly."},
              "action": {"type": "string", "enum": ["rename", "replace", "append", "undo"],
                         "description": "undo: the document's latest edit."},
              "new_title": {**_S, "description": "rename: the new title, decided now, unlike the current one."},
              "find": _S, "text": _S, "replace_all": _B}, "required": ["document", "action"]}},
           "tasks": {"type": "array", "items": _S}}),
    _tool("queue_status", "Progress of the queued changes."),
    _tool("undo_edit", "Undo a document's latest edit.", {"doc_id": _S}, ["doc_id"]),
    _tool("rename_document", "Rename a document. confirmed=true only after the user agreed to a needs_confirmation.",
          {"doc_id": _S, "new_title": {**_S, "description": "The complete new title, no extension."},
           "confirmed": _B}, ["doc_id", "new_title"]),
]


# ---------------------------------------------------------------- guard
class Guard:
    """What has been identified, and which renames wait for the user. Saved with the session."""

    def __init__(self, state: dict, turn: int):
        self.resolved: dict = state.get("resolved", {})       # doc_id -> {"turn", "by"}: uniquely identified
        self.needs_user: dict = state.get("needs_user", {})   # doc_id -> turn it was shown as one of several
        self.listed: dict = state.get("listed", {})           # doc_id -> turn it was first listed
        self.pending: list = state.get("pending", [])         # renames waiting for confirmation
        self.turn = turn
        self.session_id = ""     # set by chat(); not saved
        self.in_batch = False    # True while a queued task runs: tasks can't queue more tasks

    def state(self) -> dict:
        return {"resolved": self.resolved, "needs_user": self.needs_user, "listed": self.listed,
                "pending": self.pending}

    def saw_find(self, result: dict) -> None:
        ids = [c["doc_id"] for c in result.get("candidates", [])]
        if result["decision"] == "unique":
            self.resolved[ids[0]] = {"turn": self.turn, "by": result["reason"]}
        elif result["decision"] in ("ambiguous", "uncertain"):
            for doc_id in ids:
                self.needs_user[doc_id] = self.turn

    def saw_list(self, doc_ids: list[str]) -> None:
        for doc_id in doc_ids:
            self.listed.setdefault(doc_id, self.turn)

    def identification_problem(self, doc_id: str) -> str | None:
        shown_now = self.needs_user.get(doc_id) == self.turn
        resolved = self.resolved.get(doc_id)
        if shown_now and not (resolved and resolved["by"] == "name"):
            return ("This document was one of several possible matches (or a weak match) in this message. "
                    "Ask the user which document they mean before renaming.")
        if resolved or doc_id in self.needs_user:
            return None  # identified, or shown to the user as one of several and they have replied since
        if self.listed.get(doc_id, self.turn) < self.turn:
            return None  # the user saw it in a list and has replied since
        return "This document hasn't been identified. Call find_documents with the user's description first."

    def confirmed_earlier(self, doc_id: str, title: str) -> bool:
        return any(p["doc_id"] == doc_id and naming.same_name(p["new_title"], title) and p["turn"] < self.turn
                   for p in self.pending)

    def ask_confirmation(self, doc_id: str, title: str, reason: str) -> None:
        # one open question per document and kind: asking about a PDF conversion keeps a pending rename, and back
        self.pending = [p for p in self.pending if not (p["doc_id"] == doc_id and p.get("reason") == reason)]
        self.pending.append({"doc_id": doc_id, "new_title": title, "turn": self.turn, "reason": reason})

    def used_confirmation(self, doc_id: str, title: str) -> None:
        """An agreement counts once: after it was acted on, the same change needs asking again."""
        self.pending = [p for p in self.pending if not (p["doc_id"] == doc_id and naming.same_name(p["new_title"], title))]

    def renamed(self, doc_id: str) -> None:
        self.pending = [p for p in self.pending if not (p["doc_id"] == doc_id and p.get("reason") != "pdf_conversion")]
        self.resolved[doc_id] = {"turn": self.turn, "by": "renamed"}


# ---------------------------------------------------------------- tools
def run_tool(name: str, args: dict, guard: Guard, actions: list) -> dict:
    if name == "list_documents":
        return _list(guard)

    if name == "find_documents":
        description = (args.get("description") or "").strip()
        if not description:
            return {"error": "Give a description of the document."}
        result = library.find(description)
        guard.saw_find(result)
        if result["decision"] in FIND_NEXT:   # what to do, only in the results that need it (not in every prompt)
            result = {**result, "next": FIND_NEXT[result["decision"]]}
        return result

    if name == "document_details":
        doc = catalog.get_document(args.get("doc_id", ""))
        if not doc:
            return {"error": "No document with that doc_id."}
        out = {"doc_id": doc["id"], "title": doc["title"], "original_name": doc["original_name"],
               "summary": doc["summary"], "keywords": doc["keywords"], "pages": doc["pages"],
               "previous_names": [h["old_title"] for h in catalog.rename_history(doc["id"])],
               "edits": [e["detail"] for e in catalog.edit_history(doc["id"])]}
        if (args.get("passage") or "").strip():
            try:
                text = editing.passage(doc, args["passage"])
            except Exception as e:
                log.warning("Couldn't read %s: %s", doc["title"], str(e)[:200])
                text = None
            out["passage"] = text if text is not None else "Those words aren't in the document."
        return out

    if name == "rename_document":
        return _rename(args, guard, actions)

    if name == "edit_document":
        return _edit(args, guard, actions)

    if name == "undo_edit":
        return _undo(args, guard, actions)

    if name == "queue_tasks":
        return _queue(args, guard, actions)

    if name == "queue_status":
        from renamer import batch
        return batch.summary()

    return {"error": f"Unknown tool {name}."}


def _list(guard: Guard) -> dict:
    """Titles only, newest first, at most LIST_MAX_DOCUMENTS: about 20 tokens a document. With summaries a list cost
    76 tokens a document, so about 100 documents filled Groq's whole minute and "list my documents" failed."""
    docs = list(reversed(catalog.list_documents()))
    shown = docs[: config.LIST_MAX_DOCUMENTS]
    guard.saw_list([d["id"] for d in shown if d["status"] == "indexed"])
    out = {"total": len(docs), "documents": [
        {"doc_id": d["id"], "title": d["title"], **({"status": d["status"]} if d["status"] != "indexed" else {})}
        for d in shown]}
    if len(docs) > len(shown):
        out["next"] = (f"Only the newest {len(shown)} of {len(docs)} are listed. Say so: the library panel shows them "
                       "all, and find_documents finds any one by name or content.")
    return out


def _edit(args: dict, guard: Guard, actions: list) -> dict:
    doc_id = args.get("doc_id", "")
    doc = catalog.get_document(doc_id)
    if not doc:
        return {"status": "error", "error": "No document with that doc_id. Use find_documents."}
    problem = guard.identification_problem(doc_id)
    if problem:
        return {"status": "needs_clarification", "error": problem.replace("renaming", "editing")}
    try:
        result = editing.edit(doc_id, args.get("operation", ""), find=args.get("find") or "",
                              text=args.get("text") or "", replace_all=bool(args.get("replace_all")),
                              convert_pdf=bool(args.get("convert_pdf")) and guard.confirmed_earlier(doc_id, PDF_CONVERSION))
    except editing.NeedsConversion:
        guard.ask_confirmation(doc_id, PDF_CONVERSION, "pdf_conversion")
        return {"status": "needs_confirmation", "reason": "pdf_conversion", "document": doc["title"],
                "next": "The user was asked to agree to converting the PDF to a Markdown copy (layout and images lost; "
                        "undo restores the PDF). If they agree, call edit_document again with convert_pdf=true."}
    except editing.EditError as e:
        return {"status": "error", "error": str(e)}
    if result["status"] == "ambiguous":   # the instruction goes under the same key as the other tools' instructions
        result = {**{k: v for k, v in result.items() if k != "message"}, "next": result["message"]}
    if result.get("converted"):
        guard.used_confirmation(doc_id, PDF_CONVERSION)   # after an undo brings the PDF back, ask again
    if result["status"] == "edited":
        actions.append({"type": "edit", "doc_id": doc_id, "title": doc["title"], "operation": result["operation"],
                        "changes": result["changes"], "previews": result["previews"]})
    return result


def _describe(change: dict) -> str:
    if change["action"] == "rename":
        return f'Rename "{change["document"]}" to "{change["new_title"]}"'
    if change["action"] == "replace":
        return f'In "{change["document"]}", replace "{change["find"]}" with "{change["text"]}"'
    if change["action"] == "undo":
        return f'Undo the last edit to "{change["document"]}"'
    return f'To "{change["document"]}", append "{change["text"][:60]}"'


def _check_changes(raw: list) -> tuple[list[dict], list[str]]:
    """Validate structured changes before queueing. Returns (changes, problems). Nothing is queued if there are
    problems, so a bad batch is fixed now instead of failing quietly later."""
    changes, problems, new_titles = [], [], {}
    for i, c in enumerate(raw, start=1):
        c = c if isinstance(c, dict) else {}
        action, document = c.get("action"), " ".join(str(c.get("document") or "").split())
        if action not in ("rename", "replace", "append", "undo") or not document:
            problems.append(f"Change {i}: needs a document title and an action (rename, replace, append or undo).")
            continue
        change = {"action": action, "document": document}
        if action == "rename":
            try:
                change["new_title"] = naming.clean_title(c.get("new_title") or "")
            except naming.TitleError as e:
                problems.append(f'Change {i} ("{document}"): {e}')
                continue
            key = naming.normalize(change["new_title"])
            if naming.same_name(document, change["new_title"]):
                problems.append(f'Change {i}: "{document}" would keep the same name. Choose a different new title.')
            elif key in new_titles:
                problems.append(f'Change {i}: "{change["new_title"]}" is also the new title of change '
                                f'{new_titles[key]}. Make them distinct.')
            new_titles.setdefault(key, i)
        elif action == "replace":
            change.update(find=str(c.get("find") or "").strip(), text=str(c.get("text") or ""),
                          replace_all=bool(c.get("replace_all")))
            if not change["find"]:
                problems.append(f'Change {i} ("{document}"): say which text to replace.')
        elif action == "undo":
            pass                       # nothing more to say: the document's latest edit
        else:
            change["text"] = str(c.get("text") or "").strip()
            if not change["text"]:
                problems.append(f'Change {i} ("{document}"): give the text to append.')
        changes.append(change)
    return changes, problems


def _queue(args: dict, guard: Guard, actions: list) -> dict:
    if guard.in_batch:
        return {"status": "error", "error": "This is already a queued task. Do just this one change."}
    tasks = [" ".join(str(t).split()) for t in (args.get("tasks") or []) if str(t).strip()]
    changes, problems = _check_changes(args.get("changes") or [])
    if problems:
        return {"status": "error", "error": "Nothing was queued. Fix these and call queue_tasks again.",
                "problems": problems[:20]}
    items = [(_describe(c), c) for c in changes] + tasks
    if not items:
        return {"status": "error", "error": "Give at least one change."}
    if len(items) > config.BATCH_MAX_TASKS:
        return {"status": "error", "error": f"At most {config.BATCH_MAX_TASKS} per queue. Queue the rest after."}
    if any(len(t) > config.BATCH_TASK_MAX_CHARS for t in tasks):
        return {"status": "error", "error": f"Keep each instruction under {config.BATCH_TASK_MAX_CHARS} characters."}
    from renamer import batch  # imported here: batch uses this module
    created = catalog.add_batch(guard.session_id, items)
    batch.wake()
    event("queued", batch=created["id"], tasks=len(items), structured=len(changes), free_text=len(tasks))
    actions.append({"type": "queue", "batch_id": created["id"], "tasks": len(items)})
    return {"status": "queued", "batch_id": created["id"], "tasks": len(items),
            "note": "Changes run in code one after another; free-text tasks go through the model within the rate limit."}


def _undo(args: dict, guard: Guard, actions: list) -> dict:
    doc_id = args.get("doc_id", "")
    doc = catalog.get_document(doc_id)
    if not doc:
        return {"status": "error", "error": "No document with that doc_id. Use find_documents."}
    problem = guard.identification_problem(doc_id)
    if problem:
        return {"status": "needs_clarification", "error": problem.replace("renaming", "changing")}
    try:
        result = editing.undo(doc_id)
    except editing.EditError as e:
        return {"status": "error", "error": str(e)}
    actions.append({"type": "undo", "doc_id": doc_id, "title": doc["title"], "undid": result["undid"]})
    return result


def _rename(args: dict, guard: Guard, actions: list) -> dict:
    doc_id = args.get("doc_id", "")
    doc = catalog.get_document(doc_id)
    if not doc:
        return {"status": "error", "error": "No document with that doc_id. Use find_documents."}
    if doc["status"] != "indexed":
        return {"status": "error", "error": f"The document is {doc['status']}, not ready to rename."}
    problem = guard.identification_problem(doc_id)
    if problem:
        return {"status": "needs_clarification", "error": problem}
    try:
        title = naming.clean_title(args.get("new_title", ""))
    except naming.TitleError as e:
        return {"status": "error", "error": str(e)}
    if title == doc["title"]:
        return {"status": "unchanged", "title": title}

    conflicts = library.name_conflicts(title, exclude=doc_id)
    if conflicts and not (args.get("confirmed") and guard.confirmed_earlier(doc_id, title)):
        guard.ask_confirmation(doc_id, title, "name_conflict")
        return {"status": "needs_confirmation", "reason": "name_conflict",
                "next": "The user was asked whether to use this name anyway. If they agree, call rename_document "
                        "again with confirmed=true; if they give another name, use that.",
                "document": doc["title"], "new_title": title,
                "conflicts_with": [{"title": c["title"], "summary": library.snippet(c["summary"], 120)} for c in conflicts]}

    try:
        result = library.rename(doc_id, title)
    except Exception as e:
        log.exception("Rename failed")
        return {"status": "error", "error": f"The rename failed: {str(e)[:200]}"}
    guard.renamed(doc_id)
    actions.append({"type": "rename", "doc_id": doc_id, "old_title": result["old_title"],
                    "new_title": result["new_title"]})
    return {"status": "renamed", **result}


# ---------------------------------------------------------------- the loop
def _llm(model: str, retries: int):
    from langchain_groq import ChatGroq

    from renamer.day7 import settings

    return ChatGroq(model=model, temperature=0, api_key=settings.groq_api_key, max_retries=retries,
                    reasoning_effort=config.AGENT_REASONING or None).bind_tools(TOOLS)


def _checker():
    """The 20B without tools, for one-word questions."""
    from langchain_groq import ChatGroq

    from renamer.day7 import settings

    return ChatGroq(model=config.BACKUP_MODEL, temperature=0, api_key=settings.groq_api_key, max_retries=1,
                    reasoning_effort="low")


class GroqAgentModel:
    """The 120B, or the 20B when the 120B has no room. Groq limits each model's tokens per minute apart, so the 20B
    is a second allowance, not a slower path:

      * no room in the 120B's minute for a call of the size seen last: straight to the 20B, no waiting;
      * the 120B rate-limited anyway (Groq counts a little differently): the 20B at once. The 120B gets no
        retries, because Groq's retry sleeps out its Retry-After (27 s was seen) before giving up;
      * a tool call Groq can't parse, or Groq unreachable or failing: the 20B (which keeps its retries).
    """

    def __init__(self):
        self.main, self.backup, self.checker = None, None, None
        self.estimate = config.CALL_ESTIMATE_TOKENS   # the size of the 120B's last call
        self.sleep = time.sleep

    def anything_left(self, message: str, done: list[dict]) -> tuple[bool, int]:
        """After a step that only finished changes: has the user's whole request been done, or does the model have more
        to do? A one-word question to the 20B (a few hundred tokens, its own allowance) instead of a full closing call
        of the 120B. Returns (more to do, tokens used). If the check fails, the model is asked as usual."""
        lines = "\n".join("- " + compose_reply([r]).splitlines()[0] for r in done)
        prompt = (f'The user wrote:\n"""{message[:1500]}"""\n\nDone so far:\n{lines}\n\n'
                  "Has everything the user asked for in that message been done? Answer with one word: DONE or MORE.")
        try:
            self.checker = self.checker or _checker()
            reply = self.checker.invoke(prompt)
        except Exception as e:
            log.warning("Couldn't check whether the request is finished: %s", str(e)[:200])
            return True, 0
        used = limits.tokens_of(reply)
        limits.window_for(reply).record(used)
        answer = (reply.content if isinstance(reply.content, str) else str(reply.content)).strip().upper()
        log.debug("anything left? %s", answer[:40])
        return "MORE" in answer or "DONE" not in answer, used

    def _use_backup(self, messages):
        self.backup = self.backup or _llm(config.BACKUP_MODEL, retries=2)
        return self.backup.invoke(messages)

    def retry_on_main(self, messages):
        """The 20B gave up (an empty answer). Wait for room in the 120B's minute, at most RETRY_WAIT_SECONDS, and ask
        the 120B instead: a short wait beats "Sorry, I have no answer"."""
        wait = min(limits.WINDOW.seconds_until(limits.WINDOW.limit - self.estimate), config.RETRY_WAIT_SECONDS)
        log.info("Empty answer; asking %s again%s", config.AGENT_MODEL, f" after {wait:.0f}s" if wait > 0 else "")
        if wait > 0:
            self.sleep(wait)
        self.main = self.main or _llm(config.AGENT_MODEL, retries=0)
        try:
            reply = self.main.invoke(messages)
        except groq.RateLimitError as e:
            log.warning("Agent model still rate-limited, using %s: %s", config.BACKUP_MODEL, str(e)[:200])
            return self._use_backup(messages)
        self.estimate = limits.tokens_of(reply) or self.estimate
        return reply

    def invoke(self, messages):
        if limits.WINDOW.used() + self.estimate > limits.WINDOW.limit:
            log.info("No room in %s's minute (%d of %d tokens used), using %s", config.AGENT_MODEL,
                     limits.WINDOW.used(), limits.WINDOW.limit, config.BACKUP_MODEL)
            return self._use_backup(messages)
        self.main = self.main or _llm(config.AGENT_MODEL, retries=0)
        try:
            reply = self.main.invoke(messages)
            self.estimate = limits.tokens_of(reply) or self.estimate
            return reply
        except groq.RateLimitError as e:
            log.warning("Agent model rate-limited, using %s: %s", config.BACKUP_MODEL, str(e)[:200])
        except groq.BadRequestError as e:
            # gpt-oss now and then writes a tool call Groq can't parse ("tool_use_failed"); the other model usually doesn't
            if "tool_use_failed" not in str(e) and "failed_generation" not in str(e):
                raise
            log.warning("Agent model wrote a broken tool call, using %s: %s", config.BACKUP_MODEL, str(e)[:200])
        except (groq.APIConnectionError, groq.InternalServerError) as e:
            log.warning("Agent model unavailable, using %s: %s", config.BACKUP_MODEL, str(e)[:200])
        return self._use_backup(messages)


_default_model = None


def _trim(history: list) -> list:
    """The last HISTORY_TURNS user messages and everything after each (cut only at a user message)."""
    starts = [i for i, m in enumerate(history) if isinstance(m, HumanMessage)]
    if len(starts) <= config.HISTORY_TURNS:
        return history
    return history[starts[-config.HISTORY_TURNS]:]


def _shorten(history: list) -> list:
    """What the model is sent of earlier messages. The last exchange goes whole, tool calls and results included,
    because follow-ups refer to it ("yes, rename it"). Older exchanges keep only what was said: the user's message
    and the final reply, cut to OLDER_MAX_CHARS (day7's rule). Their tool calls and results (candidate lists,
    summaries) were most of the prompt and are stale anyway; the guard keeps what was identified, in code, and the
    model calls find_documents again when it needs an older doc_id. The saved session keeps everything."""
    last = max((i for i, m in enumerate(history) if isinstance(m, HumanMessage)), default=0)
    out = []
    for i, m in enumerate(history):
        if i < last:
            if isinstance(m, ToolMessage) or (isinstance(m, AIMessage) and m.tool_calls):
                continue
            if isinstance(m.content, str) and len(m.content) > config.OLDER_MAX_CHARS:
                m = m.model_copy(update={"content": m.content[:config.OLDER_MAX_CHARS].rstrip() + " …"})
        out.append(m)
    return out


PDF_CONVERSION = "(convert pdf to markdown)"   # what a pending PDF confirmation is recorded under
DONE_STATUSES = ("renamed", "edited", "undone")
# results the reply can be written for in code, with no closing model call: finished changes, and the questions
# the guard asks (the next message answers them; the model sees this exchange whole then)
CODE_REPLY_STATUSES = (*DONE_STATUSES, "needs_confirmation")


_MARKDOWN_CHARS = re.compile(r"([\\`*])")


def _md(text: str) -> str:
    """Text from a document or a title, escaped for the chat's Markdown: a Markdown file's own ** or a cut-off half of
    one must not pair with the reply's bold and embolden the wrong words."""
    return _MARKDOWN_CHARS.sub(r"\\\1", str(text or ""))


def _bold(text: str) -> str:
    return f"**{_md(text)}**" if text else ""


def _question(r: dict) -> str:
    """A needs_confirmation result as the question to the user."""
    if r["reason"] == "pdf_conversion":
        return (f'"{_md(r["document"])}" is a PDF. To edit it I would convert it to a Markdown copy of its text: the '
                "layout and images are lost, but the PDF is kept and undo brings it back. Shall I convert it and make the change?")
    n = len(r["conflicts_with"])   # their titles are the new name itself, so say what they are about instead
    about = "; ".join(_md(c["summary"]) for c in r["conflicts_with"] if c["summary"])
    return (f'{"Another document is" if n == 1 else f"{n} other documents are"} already called "{_md(r["new_title"])}"'
            + (f" ({about})" if about else "") + f'. Rename "{_md(r["document"])}" to "{_md(r["new_title"])}" anyway, '
            "or use a different name?")


def compose_reply(results: list[dict]) -> str:
    """The reply written in code: for finished changes (for an edit only the changed passage with a little of the
    text around it, never the whole document), and for the questions the guard asks."""
    parts = []
    for r in results:
        if r["status"] == "needs_confirmation":
            parts.append(_question(r))
        elif r["status"] == "renamed":
            parts.append(f'Renamed "{_md(r["old_title"])}" to "{_md(r["new_title"])}".')
        elif r["status"] == "undone":
            parts.append(f'Undid the last edit to "{_md(r["title"])}" ({_md(r["undid"])}).')
        else:
            n = r["changes"]
            lines = ['Converted the PDF to Markdown (layout and images are not kept); undo restores the PDF.'] if r.get("converted") else []
            lines += [f'Edited "{_md(r["title"])}" ({n} change{"" if n == 1 else "s"}'
                     + (f", first {len(r['previews'])} shown" if n > len(r["previews"]) else "") + "):"]
            for p in r["previews"]:
                lead, trail = _md(p["lead"]), _md(p["trail"])
                if p["old"]:
                    lines.append(f'Before: …{lead}{_bold(p["old"])}{trail}…')
                    lines.append(f'After: …{lead}{_bold(p["new"])}{trail}…')
                else:
                    lines.append(f'Added after …{lead}: {_bold(p["new"])}')
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _action_text(a: dict) -> str:
    """One line for any action (the reply when the model said nothing, and the terminal chat)."""
    if a["type"] == "rename":
        return f'"{a["old_title"]}" is now "{a["new_title"]}"'
    if a["type"] == "edit":
        return f'edited "{a["title"]}" ({a["operation"]}, {a["changes"]} change{"" if a["changes"] == 1 else "s"})'
    if a["type"] == "undo":
        return f'undid the last edit to "{a["title"]}" ({a["undid"]})'
    if a["type"] == "queue":
        return f'queued {a["tasks"]} change{"" if a["tasks"] == 1 else "s"}'
    return a["type"]


def _empty(ai: AIMessage) -> bool:
    """No tool call and no text: the model gave up (the 20B does now and then)."""
    return not ai.tool_calls and not (ai.content if isinstance(ai.content, str) else str(ai.content)).strip()


def default_model() -> "GroqAgentModel":
    global _default_model
    _default_model = _default_model or GroqAgentModel()
    return _default_model


def chat(session_id: str, message: str, model=None, batch: bool = False) -> dict:
    """One user message in. Returns {"reply", "actions", "tokens"}; the session is saved."""
    model = model or default_model()

    session = catalog.load_session(session_id)
    session["turn"] += 1
    guard = Guard(session["guard"], session["turn"])
    guard.session_id, guard.in_batch = session_id, batch
    tokens, failed = 0, False
    event("chat", session=session_id[:8], turn=session["turn"], chars=len(message), queued_task=batch)
    log.debug("user message (%s turn %d): %s", session_id[:8], session["turn"], message[:500])
    history = _trim(messages_from_dict(session["llm_messages"]))
    new = [HumanMessage(message)]
    actions: list = []
    reply = ""

    retried, finished, more = False, [], False
    for _ in range(config.AGENT_MAX_STEPS):
        prompt = [SystemMessage(SYSTEM), *_shorten(history), *new]
        try:
            ai: AIMessage = model.invoke(prompt)
            used = limits.tokens_of(ai)
            limits.window_for(ai).record(used)
            tokens += used
            # The 20B gave up (an empty answer), or stopped with words although the check found more to do (it
            # has claimed changes no tool made): one more try, on the main model.
            from_backup = limits.window_for(ai) is limits.BACKUP_WINDOW
            gave_up = _empty(ai) or (from_backup and more and not ai.tool_calls)
            if gave_up and not retried and hasattr(model, "retry_on_main"):
                retried = True
                ai = model.retry_on_main(prompt)
                used = limits.tokens_of(ai)
                limits.window_for(ai).record(used)
                tokens += used
        except Exception as e:
            log.exception("Agent model call failed")
            reply = f"Sorry, I couldn't reach the language model ({type(e).__name__}). Please try again."
            failed = True
            break
        new.append(ai)
        if not ai.tool_calls:
            reply = ai.content if isinstance(ai.content, str) else str(ai.content)
            break
        results = []
        for call in ai.tool_calls:
            try:
                result = run_tool(call["name"], call.get("args") or {}, guard, actions)
            except Exception as e:
                log.exception("Tool %s failed", call["name"])
                result = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            results.append(result)
            log.debug("tool %s args=%s -> %s", call["name"], json.dumps(call.get("args") or {}, ensure_ascii=False)[:600],
                      json.dumps(result, ensure_ascii=False, default=str)[:800])
            new.append(ToolMessage(json.dumps(result, ensure_ascii=False), tool_call_id=call["id"]))
        finished += [r for r in results if r.get("status") in DONE_STATUSES]
        more = False
        if all(r.get("status") in CODE_REPLY_STATUSES for r in results):
            # done, or a fixed question: the reply is written in code, unless the user asked for more than this
            asking = any(r.get("status") == "needs_confirmation" for r in results)
            if not asking and hasattr(model, "anything_left"):
                more, used = model.anything_left(message, finished)
                tokens += used
            if not more:
                reply = compose_reply(finished + [r for r in results if r.get("status") == "needs_confirmation"])
                break
    else:
        reply = "I couldn't finish that in a few steps. Could you say it another way?"

    if not reply.strip():
        reply = ("Done: " + "; ".join(_action_text(a) for a in actions)
                 if actions else "Sorry, I have no answer to that. Could you rephrase?")
        new.append(AIMessage(reply))
    elif not isinstance(new[-1], AIMessage) or new[-1].tool_calls:
        new.append(AIMessage(reply))  # error replies, so the next turn sees a complete exchange

    session["llm_messages"] = messages_to_dict(history + new)
    session["guard"] = guard.state()
    session["display"] += [{"role": "user", "text": message},
                           {"role": "assistant", "text": reply, "actions": actions}]
    catalog.save_session(session)
    event("chat_done", session=session_id[:8], turn=session["turn"], tokens=tokens, actions=len(actions), failed=failed)
    log.debug("reply: %s", reply[:500])
    return {"reply": reply, "actions": actions, "tokens": tokens, "failed": failed}
