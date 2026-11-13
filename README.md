# Document Renaming Agent

A chat app for renaming documents. Point to a document by its name or by what it's about, and the agent
renames it. If your description fits more than one document, or the new name is already taken, it asks you first.

## How it works

| Step | What happens |
|---|---|
| **Ingest** | day7's own pipeline, **imported, not copied** (`renamer/day7.py`): load → clean → 1,000-character chunks → Gemini embeddings + BM25 → Qdrant. |
| **Title** | Before embedding, gpt-oss-120b reads the first 6,000 characters and adds a **title**, a 2-3 sentence summary and keywords to the document. The title must differ from the titles already in the library. If the model can't be reached, the title comes from the file name. |
| **Find** | `find_documents` matches the user's words against each document's **name** (current title, original file name, earlier names) and its **content** (day7's hybrid search over the chunks, plus each document's title, summary and keywords, scored by day7's Voyage reranker). Result: `unique`, `ambiguous`, `uncertain` or `none`. |
| **Rename** | Changes the title in the catalog, the stored file's name in `data/library/`, and the `source`/`title` fields on its chunks in Qdrant (no re-embedding). Every rename is recorded. |

| **Edit** | `edit_document` changes the text of a `.txt`, `.md` or `.docx` document (replace a passage, or append text). A PDF is edited as a Markdown copy of its text, after the user agrees: layout and images are lost, the PDF is kept and undo restores it. The previous file is kept in `data/versions/<doc>/`, then the document is re-indexed with day7's loading, cleaning and splitting. Only chunks whose text changed are embedded; the others keep their stored vectors, so the index is what a full re-ingest would give (a test checks this). A one-word edit embeds 1-2 chunks: across the 37 documents, 40 Gemini requests instead of 653. New chunks are embedded first and written over the old ones only on success, so a failed embedding leaves the document as it was. If the passage appears in several places, nothing changes until the user says which (or "all"). To rewrite a passage, the agent first reads its exact text with `document_details(passage=...)`, so `find` covers the whole passage. One change to stored files runs at a time (chat and queue share a lock). |

| **Undo** | `undo_edit` restores the document as it was before its latest edit (one step per call) and re-indexes it. To undo a rename, ask for the old name back; the agent can read the earlier names. |
| **Batch** | Asked to change three or more documents at once, the agent calls `queue_tasks`. Structured changes (rename, replace, append, undo) run in code with no model call; free-text tasks run one at a time through the same agent, each in its own session. A background worker (`renamer/batch.py`) runs them in order. Measured live: 2 renames, 2 replacements and an append took 5 seconds, 0 Groq tokens and 3 Gemini requests. See "Batches and the Groq limit". |
| **Finishing a request** | When a step only finished changes, the reply is written in code, but first the 20B is asked one word, DONE or MORE ("has everything the user asked for been done?"; about 150-200 tokens), so a request done one change at a time isn't cut short after the first. If the 20B, standing in for a full 120B, gives an empty answer or stops with words although there is more to do (it once claimed "all five changes have been reverted" with one left), the step is asked again of the 120B, after waiting up to 65 seconds for room. |

### When the agent asks you first

These rules are enforced in code (`renamer/agent.py`, `Guard`), not just in the prompt:

- **Several documents fit** (similar content, or the same name): it lists them and asks which one you mean, or
  for a more specific description. It can't pick one itself in the same message, even by searching again
  more narrowly.
- **Only a weak match**: it asks "Did you mean …?" before renaming.
- **The new name is already used** by another document: it asks you to confirm or choose another name. A
  confirmation counts only if it comes in a later message, and only for that exact name. If you confirm, both
  documents keep the same title and the second file is stored as `Name (2).ext`.
- **The document hasn't been identified**: renaming and editing are refused until `find_documents` has found it.

Find thresholds are day7's calibrated values for Voyage rerank-2.5 (`renamer/config.py`): 0.55 or higher is a
confident match, below 0.45 is no match, and documents within 0.10 of the best match count as too close to tell
apart. Without Voyage, Gemini cosine similarity is used with its own thresholds. These thresholds are still
untuned for this app; see "Not yet done".

### Example (how a conversation is meant to go; not yet run on real documents)

```
you>   Rename the document about word embeddings to "Embeddings Notes"
agent> Two documents fit:
       1. Word2vec Neural Word Embeddings: word vectors from a shallow network (Mikolov, Google)
       2. GloVe Word Embeddings: word vectors from co-occurrence counts (Stanford)
       Which one do you mean?
you>   the Google one
agent> Renamed "Word2vec Neural Word Embeddings" to "Embeddings Notes".
```

### Batches and the Groq limit

`renamer/limits.py` keeps a 60-second window of the tokens every model call used (as reported by Groq). Before each
queued task starts, the worker waits until the window has room for a whole task (the last task's tokens + 15%;
3,500 before any has run, `TASK_ESTIMATE_TOKENS`), and inside a task each model call waits until the window has room for a call
of the size seen last (`TPM_LIMIT`, default 8,000; set `RENAMER_TPM_LIMIT` to match your Groq plan). The Queue panel on
the page shows each task's status, the tokens used this minute and a countdown. Consequences: a batch of 19 documents
takes about 10 minutes (two tasks fit in each minute); a task that can't identify its document, or finds the text missing, ends as "not done"
with the agent's reply instead of stopping the queue; queued tasks survive a server restart.

### Token use

Same approach as day7 (`tools.history_to_messages`): the last exchange is sent whole, tool calls and results
included; older exchanges keep only what was said (the user's message and the final reply, cut to 800 characters,
`OLDER_MAX_CHARS`). This is only for what the model sees; the saved session keeps everything. On a real session this
took the history from about 980 to 420 tokens per model call. Also like day7: low reasoning effort, short tool
descriptions, and a cap of 8 earlier user messages.

The system prompt and tool descriptions, sent with every model call, are kept to about 1,080 tokens (a test fails
above 1,150). Rules for rare cases are not in the prompt: they come in a `next` field of the tool results that need
them (an ambiguous or weak match, a name conflict, a PDF to convert, a passage found in several places), so they cost
tokens only when they happen. A live check: finding documents 2,129 tokens, a rename question 1,415, a short reply 1,217.

**Voyage and Gemini calls when finding documents.** A search is reused while nothing in the library has changed
(a version number in the catalog goes up with every document change; at most 10 minutes), because the agent often
looks the same document up again in a follow-up message, and each search costs a Gemini embedding and a Voyage
rerank call. Voyage's free tier allows about 3 rerank calls a minute (`VOYAGE_CALLS_PER_MINUTE`): past that, the search
uses cosine scores straight away instead of making a call that would fail. The queue reserves room for the summary
refresh (2,200 tokens) only for edits that will need it, worked out from the text before the edit runs.

**The backup model.** Groq limits each model's tokens per minute separately, so gpt-oss-20b is a second allowance.
When the 120B's minute has no room for a call (by the window's count), the chat goes straight to the 20B instead of
waiting; a 429 from the 120B also switches at once (the 120B has no retries, because Groq's client would sleep out
the Retry-After first, 27 seconds in one case). The 20B is also used when the 120B writes a tool call Groq can't parse,
or Groq can't be reached. Its tokens are counted in their own window (`BACKUP_TPM_LIMIT`, default 8,000), and the
meter shows them as "backup model N" while there are any.

Replies that need no thinking are written in code, with no closing model call: finished renames, edits and undos,
and the guard's questions (a name another document has; converting a PDF). After an edit or undo the model rewrites
the summary and keywords (about 2,200 tokens) only when at least 5% of the text changed (`SUMMARY_REFRESH_SHARE`);
for a smaller change the old summary still holds. day7's other savings (router and
gate prompts, citation checks in code, episode summaries) belong to its answer pipeline, which this app doesn't use.

## Run it

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

.venv\Scripts\python -m renamer serve          # chat page on http://127.0.0.1:8200
```

API keys are read from `..\day7\.env` (`GROQ_API_KEY`, `GEMINI_API_KEY`, `VOYAGE_API_KEY`). A `.env` in this
folder is optional and overrides them. day7 must be at `..\day7`, or set `RENAMER_DAY7_DIR`.

Add files in the page's **Library** panel (button or drag and drop; PDF, DOCX, TXT, MD up to 20 MB). Files are
indexed one at a time in the background. You can also use the command line:

```powershell
.venv\Scripts\python -m renamer ingest path\to\file.pdf path\to\folder
.venv\Scripts\python -m renamer list
.venv\Scripts\python -m renamer chat           # chat in the terminal
```

Use either the server or the command line, not both at once: the embedded Qdrant store allows one process.

Tests (no API calls; fake embeddings, reranker and model, in a temporary folder):

```powershell
.venv\Scripts\python -m pytest -q
```

## Test corpus

`test_corpus/` has 36 small documents (WHO health reports, AI/ML articles and papers), downloaded but not yet
ingested, picked to exercise every scenario above, with a prompt list per scenario: see
[`test_corpus/README.md`](test_corpus/README.md). Ingesting all of it costs ~649 Gemini requests.

## Files

| Path | Holds |
|---|---|
| `renamer/day7.py` | The bridge: points day7's storage at `data/`, then imports its ingestion, search, reranker and settings |
| `renamer/library.py` | Ingest, title, find, rename, delete |
| `renamer/titles.py` | Title, summary and keywords from the document's opening text |
| `renamer/naming.py` | Title cleanup, safe file names, name similarity |
| `renamer/agent.py` | The tool-calling agent (gpt-oss-120b, 20B when rate-limited) and the rename guard |
| `renamer/catalog.py` | SQLite: documents, rename history, chat sessions |
| `renamer/api.py` | FastAPI routes and the chat page |
| `web/` | Chat page (plain HTML, CSS and JS, no build step) |
| `tests/` | Unit tests |
| `scripts/fetch_test_corpus.py` | Downloads the test corpus |
| `test_corpus/` | The test documents (`files/`, git-ignored), their sources (`manifest.json`) and the test plan |
| `data/` | Created on first run: `library/` (the files), `catalog.db`, `qdrant/`, query-embedding cache |

day7's own vector store and database are never touched: this project uses its own collection (`renamer_docs`)
in `data/qdrant`.

## Not yet done

- Nothing has been ingested yet. The thresholds above come from day7's benchmark and should be checked on
  real documents, especially the 0.10 margin that decides when two documents are "too close to tell apart".
- No login: the server listens on 127.0.0.1 only.
