# The `graph` folder, explained line by line

This document explains every line of code in `backend/graph/` in plain language. No programming background is assumed.

The folder has four files:

| File | Lines | What it is, in one sentence |
|---|---|---|
| [`__init__.py`](#1-__init__py) | 0 | An empty label that tells Python "this folder is part of the program." |
| [`state.py`](#2-statepy--the-backpack) | 35 | The **backpack**: a list of everything the robot carries while it thinks. |
| [`nodes.py`](#3-nodespy--the-stops) | 333 | The **stops**: each step the robot can take (decide, reword, search, check, answer). |
| [`workflow.py`](#4-workflowpy--the-map) | 159 | The **map**: which stop comes after which, and how to start the journey. |

---

## The big picture first

Think of answering a question as a **board game**.

- The robot is the **playing piece**.
- It carries a **backpack** (`state.py`) with your question and everything it learns along the way.
- The board has **stops** (`nodes.py`). At each stop the robot does one job and puts the result in the backpack.
- **Arrows** between the stops (`workflow.py`) decide where it goes next. Some arrows point *backwards*. That is how the robot tries again when its first attempt wasn't good enough.

```
You ask a question
        │
        ▼
    [ router ]  "What kind of question is this?"
        │
        ├── just saying hi ─────────► [ greeting ] ───────────────────────────► done
        │
        ├── general knowledge ──────► [ direct ] ──► [ evaluate ] ──► [ finalize ] ► done
        │                                 │               │
        │                                 └── "I don't know / not good enough" ──► go to web search
        │
        ├── about your documents ───► [ retrieve ] ► [ grade ] ► [ generate_rag ] ► [ evaluate ] ► [ finalize ] ► done
        │        ▲                                      │              │                │
        │        └────────── [ rewrite ] ◄──────────────┴──────────────┴────────────────┘
        │                    (reword the question and try again, up to 3 searches;
        │                     then [ no_results ]: "No matches found")
        │
        └── needs the internet ─────► [ web_search ] ► [ generate_web ] ► [ evaluate ] ► [ finalize ] ► done
```

### Words used in this document

| Word | Plain meaning |
|---|---|
| **state** | The backpack. One shared collection of facts about the current question. |
| **node** | One stop on the board. In code, it is a function. |
| **edge** | An arrow from one stop to the next. |
| **conditional edge** | An arrow that *chooses* where to go, based on what is in the backpack. |
| **function** | A named set of instructions. `def name(...):` starts one. |
| **return** | "Hand this result back." |
| **dict** (dictionary) | A set of labelled boxes: `{"name": "Sam", "age": 7}`. The labels are called **keys**. |
| **list** | Things in a row: `["a", "b", "c"]`. |
| **string** | A piece of text, written in quotes. |
| **LLM / the model** | The AI that reads a prompt and writes a reply. |
| **prompt** | The instructions and question we send to the AI. |
| **trace** | The robot's diary: one line per stop, shown in the app under "Agent steps." |

### How to read the code blocks

Each block shows real code with its line number on the left. Under it, every line is explained. Lines that are blank or that only hold a closing bracket are grouped with their neighbours.

---

# 1. `__init__.py`

This file is **completely empty** (0 lines).

Its only job is to exist. When Python sees a folder containing a file named `__init__.py`, it treats the folder as a **package**: a named group of code that other files can borrow from. Because it is there, other files can write:

```python
from backend.graph.workflow import run_agent
```

Without it, some tools would not recognise `graph` as part of the program. Think of it as the name tag on a drawer.

---

# 2. `state.py` — the backpack

**Purpose:** describe what the robot's backpack can hold. This file contains no actions. It is only a list of pockets and what goes in each one.

Why it matters: every stop reads from this backpack and writes to it. It is the only way the stops talk to each other.

```python
 1  import operator
 2  from typing import Annotated, Any, TypedDict
```

| Line | Plain meaning |
|---|---|
| 1 | Borrow Python's built-in `operator` toolbox. We need exactly one thing from it: `operator.add`, which means "join two things together." |
| 2 | Borrow three labelling tools from Python's `typing` toolbox. `TypedDict` lets us describe a dictionary with fixed labels. `Annotated` lets us attach a special note to a pocket. `Any` means "this can be anything." |
| 3–4 | Blank lines, for readability. |

```python
 5  class AgentState(TypedDict, total=False):
```

| Line | Plain meaning |
|---|---|
| 5 | "Here is a description of a backpack called `AgentState`." `TypedDict` says it is a dictionary whose labels are listed below. `total=False` means **no pocket is required to be filled**. The backpack starts nearly empty and fills up as the robot travels. |

### The "inputs" pockets: what the robot is given at the start

```python
 6      # --- inputs
 7      query: str                      # raw user message
 8      user_id: str
 9      username: str
10      history: list[dict]             # [{"role": "user"|"assistant", "content": str}]
11      doc_names: list[str]            # user's indexed documents (helps routing & rewriting)
```

A line starting with `#` is a **comment**: a note for humans that the computer ignores. Each pocket is written as `name: type`, meaning "a pocket called *name* that holds this kind of thing."

| Line | Pocket | Plain meaning |
|---|---|---|
| 6 | | A comment heading: the next pockets are the starting information. |
| 7 | `query` | Your message, exactly as you typed it. `str` means text. |
| 8 | `user_id` | Your private ID. The robot uses it so it only ever searches **your** documents. |
| 9 | `username` | Your name, so a greeting can say "Hi Sam." |
| 10 | `history` | The last few messages of the conversation, as a list. Each one records who spoke (`role`) and what they said (`content`). This is the robot's short-term memory. |
| 11 | `doc_names` | The file names of the documents you have uploaded. Knowing them helps the robot decide whether your question is about your files. |
| 12 | | Blank line. |

### The "routing" pockets: what the robot decided

```python
13      # --- routing
14      intent: str                     # greeting | rag | web_search | direct
15      standalone_query: str           # query made self-contained using chat history
16      query_is_clear: bool
17      route_reason: str
```

| Line | Pocket | Plain meaning |
|---|---|---|
| 13 | | Comment heading. |
| 14 | `intent` | The kind of question this is. One of four words: `greeting` (small talk), `rag` (look in your documents), `web_search` (look on the internet), `direct` (answer from the AI's own knowledge). |
| 15 | `standalone_query` | Your question rewritten so it makes sense **on its own**. If you ask "what about his age?", this becomes "What is the age of the person in the résumé?" A search can't understand "his." |
| 16 | `query_is_clear` | A yes/no answer (`bool` means true or false): is the question clear enough to search with? |
| 17 | `route_reason` | One sentence explaining why the robot chose that intent. |
| 18 | | Blank line. |

### The "query transformation loop" pockets: used when retrying

```python
19      # --- query transformation loop
20      search_queries: list[str]       # queries used for the next retrieval / web search round
21      rewritten_queries: Annotated[list[str], operator.add]
22      rewrite_reason: str             # vague_query | no_relevant_docs | insufficient_answer | no_web_results
23      attempts: int                   # retrieval / search rounds executed
```

| Line | Pocket | Plain meaning |
|---|---|---|
| 19 | | Comment heading. |
| 20 | `search_queries` | The list of phrasings the robot will search with **next**. |
| 21 | `rewritten_queries` | A running list of every reworded question so far. **This pocket is special**; see the box below. |
| 22 | `rewrite_reason` | A short code saying *why* the robot needs to reword. Four possible codes: the question was vague, nothing relevant was found, the answer wasn't good enough, or the web search found nothing. |
| 23 | `attempts` | How many searches have been done. `int` means a whole number. This counter is what stops the robot from retrying forever. |
| 24 | | Blank line. |

> **The special pockets (lines 21 and 35).**
> Normally, when a stop puts something in a pocket, it **replaces** what was there. A new `answer` throws away the old one.
>
> `Annotated[list[str], operator.add]` changes that rule for this pocket. It says: "this is a list of text, and when a stop adds to it, **join the new items onto the end** instead of replacing."
>
> So if the pocket holds `["first rewording"]` and a stop adds `["second rewording"]`, the pocket becomes `["first rewording", "second rewording"]`. Nothing is lost. The robot needs that history so it never tries the same rewording twice.

### The "evidence" pockets: what the robot found

```python
25      # --- evidence
26      documents: list[dict]           # graded, relevant chunks
27      web_results: list[dict]
```

| Line | Pocket | Plain meaning |
|---|---|---|
| 25 | | Comment heading. |
| 26 | `documents` | The pieces of your documents that were found. After checking, only the useful pieces remain. |
| 27 | `web_results` | The web pages that were found. |
| 28 | | Blank line. |

### The "output" pockets: what the robot hands back

```python
29      # --- output
30      answer: str
31      tool: str                       # greeting | rag | web_search | direct | no_results
32      sources: list[dict]
33      evaluation: dict[str, Any] | None
34      best: dict[str, Any] | None     # best-scoring candidate across retries
35      trace: Annotated[list[dict], operator.add]
```

| Line | Pocket | Plain meaning |
|---|---|---|
| 29 | | Comment heading. |
| 30 | `answer` | The text of the reply you will read. |
| 31 | `tool` | Which method actually produced the answer. The app shows it as a coloured badge. `no_results` means "nothing was found." |
| 32 | `sources` | The list of pages or links the answer is based on. The app shows them as cards. |
| 33 | `evaluation` | The report card for the answer: was it complete, was it backed by the evidence, a score from 1 to 5, and what was missing. `| None` means it may also be empty (a greeting is never graded). |
| 34 | `best` | The highest-scoring answer seen so far. Retries sometimes make things worse; this lets the robot fall back to its best attempt. |
| 35 | `trace` | The robot's diary. Another **special** pocket: every stop adds its diary line to the end, so the full story is kept. |

---

# 3. `nodes.py` — the stops

**Purpose:** each function in this file is one stop on the board. It receives the backpack, does one job, and hands back **only the pockets it changed**.

The file has nine parts: setup, the router, greeting, direct answer, rewriting, the document path, the web path, checking, and the endings.

## 3.1 Setup (lines 1–23)

```python
 1  """LangGraph nodes for the agentic RAG workflow.
 2
 3  Every node returns a partial state update and appends one or more `trace` steps so the UI can
 4  show exactly which path the agent took.
 5  """
```

| Line | Plain meaning |
|---|---|
| 1–5 | Text between triple quotes at the top of a file is a **description for humans**. It says: this file holds the stops; each stop hands back only what it changed and writes at least one diary line, so the app can show the robot's path. The computer does not act on it. |

```python
 6  import logging
 7  from typing import Literal
 8
 9  from pydantic import BaseModel, Field
```

| Line | Plain meaning |
|---|---|
| 6 | Borrow the tool for writing warning messages into the server's terminal window. |
| 7 | Borrow `Literal`, a way of saying "only these exact words are allowed." |
| 8 | Blank line. |
| 9 | Borrow two tools for designing **forms the AI must fill in**. `BaseModel` is the blank form. `Field` lets us write a hint beside each blank. |

```python
11  from backend.config.llm import structured
12  from backend.config.settings import settings
13  from backend.evaluation.evaluator import evaluate_answer, grade_documents
14  from backend.graph.state import AgentState
15  from backend.rag.retrieval import retrieve
16  from backend.tools import direct_answer, greeting, rag, web_search
17  from backend.tools import history_to_text, today
```

These lines borrow helpers from other parts of this project.

| Line | What is borrowed | Plain meaning |
|---|---|---|
| 11 | `structured` | "Ask the AI, and make it answer by filling in this form." |
| 12 | `settings` | The project's adjustable numbers, such as how many web results to keep. |
| 13 | `evaluate_answer`, `grade_documents` | The two checkers: one grades a finished answer, the other sorts useful document pieces from useless ones. |
| 14 | `AgentState` | The backpack description from `state.py`. |
| 15 | `retrieve` | The function that searches your documents. |
| 16 | `direct_answer`, `greeting`, `rag`, `web_search` | The four helpers that produce answers in the four different ways. |
| 17 | `history_to_text`, `today` | Two small helpers: turn the chat history into plain text, and give today's date. |

Notice what this tells you: **this file does very little work itself.** It is a coordinator. It calls the helpers, then records what happened.

```python
19  log = logging.getLogger(__name__)
```

| Line | Plain meaning |
|---|---|
| 19 | Create a named notepad for warnings. Later, `log.warning(...)` writes a line into the server terminal, labelled with this file's name so you know where it came from. |

```python
22  def _step(node: str, detail: str, **data) -> dict:
23      return {"node": node, "detail": detail, "data": data}
```

This tiny function makes **one diary entry**.

| Line | Plain meaning |
|---|---|
| 22 | Define a helper called `_step`. It takes the stop's name (`node`), a sentence describing what happened (`detail`), and any extra labelled facts (`**data` means "collect whatever other named values are given"). The underscore at the start of the name means "only used inside this file." `-> dict` means it hands back a dictionary. |
| 23 | Hand back a dictionary with three labelled boxes: which stop, what happened, and the extra facts. |

Every line you see under "Agent steps" in the app was made by this function.

## 3.2 The router: deciding what kind of question this is (lines 26–97)

### The form the AI fills in

```python
27  class Route(BaseModel):
28      intent: Literal["greeting", "rag", "web_search", "direct"]
29      reasoning: str = Field(description="One short sentence explaining the choice.")
30      query_is_clear: bool = Field(
31          description="False if the query is vague, ambiguous, too short, misspelled, keyword soup, "
32                      "or depends on unresolved references - i.e. it would search poorly as-is."
33      )
34      standalone_query: str = Field(
35          description="The user's request rewritten minimally to be self-contained given the chat history. "
36                      "Same language and intent; do not add new facts."
37      )
```

| Line | Plain meaning |
|---|---|
| 26 | A comment heading. |
| 27 | Design a form called `Route`. The AI must reply by filling in this form and nothing else. |
| 28 | Blank 1, `intent`: the AI must pick **exactly one of these four words**. It cannot invent a fifth. This is the actual decision. |
| 29 | Blank 2, `reasoning`: one short sentence explaining the choice. The text inside `description=` is a hint that is shown to the AI. |
| 30–33 | Blank 3, `query_is_clear`: true or false. The hint tells the AI to answer false when the question is vague, too short, misspelled, a jumble of keywords, or uses words like "it" and "his" that haven't been explained. |
| 34–37 | Blank 4, `standalone_query`: the question rewritten so it makes sense alone. The hint says to change as little as possible, keep the same language and meaning, and add no new facts. |

Two pieces of text sitting on neighbouring lines (as on 31–32 and 35–36) are joined into one sentence automatically. They are split only to keep the lines short.

### The instructions the AI reads

```python
40  ROUTER_PROMPT = """You are the intent router of an agentic RAG assistant. Today is {today}.
41
42  Choose ONE intent for the latest user message:
43  - greeting: small talk only (hi, thanks, bye, who are you, what can you do).
44  - rag: the answer should come from the user's uploaded documents. Choose this when the user refers
45    to "the document/file/pdf/report", their own/company/internal material, or the topic plausibly
46    matches their documents listed below.
47  - web_search: needs current, real-time or recent information (news, prices, weather, sports results,
48    "latest"/"current"/"today", events after your training cutoff), or the user explicitly asks to search
49    the web / internet / online.
50  - direct: stable general knowledge you can answer yourself (concepts, history, science, coding, math,
51    writing help, advice).
52
53  Tie-breakers:
54  - If the user has documents and the message could reasonably be about them, prefer rag.
55  - Vague or keyword-style messages ("skills?", "pricing stuff", "main points") are usually about the
56    user's documents when they have any: choose rag and mark query_is_clear=false so it gets rewritten.
57  - Only choose direct for vague messages when the user has no documents.
58
59  User's indexed documents ({doc_count}): {doc_names}
60
61  Recent conversation:
62  {history}
63
64  Latest user message: {query}"""
```

This is one long piece of text, stored under the name `ROUTER_PROMPT`. It is a **letter to the AI** with blanks in it. Each word in curly brackets, like `{today}`, is a blank that gets filled in later.

| Lines | Plain meaning |
|---|---|
| 40 | Tell the AI its job ("you sort questions into types") and today's date. The date lets it judge what counts as "recent." |
| 42 | "Pick one type for the newest message." |
| 43 | Type 1, greeting: only small talk. |
| 44–46 | Type 2, your documents: choose this when you mention "the file" or "the report," talk about your own material, or the topic matches your file names. |
| 47–49 | Type 3, the internet: choose this for news, prices, weather, anything "latest," or when you say "search the web." |
| 50–51 | Type 4, the AI's own knowledge: things that don't change, such as science, history, coding, maths. |
| 53–57 | Rules for unclear cases. If you have documents and the question could be about them, lean towards documents. A short, vague message ("skills?") is probably about your documents, so choose documents **and** mark the question as unclear so it gets reworded. Choose "own knowledge" for a vague message only when you have no documents. |
| 59 | Blank for how many documents you have, and their names. |
| 61–62 | Blank for the recent conversation. |
| 64 | Blank for your newest message. |

### The router function itself

```python
67  def router_node(state: AgentState) -> dict:
68      docs = state.get("doc_names", [])
```

| Line | Plain meaning |
|---|---|
| 67 | Define the stop called `router_node`. It receives the backpack (`state`) and hands back a dictionary of changes. |
| 68 | Take the list of your document names out of the backpack and call it `docs`. `.get("doc_names", [])` means "give me that pocket, and if it's missing, give me an empty list instead of failing." |

```python
69      prompt = ROUTER_PROMPT.format(
70          today=today(),
71          doc_count=len(docs),
72          doc_names=", ".join(docs[:40]) or "(none uploaded)",
73          history=history_to_text(state.get("history", [])[-6:]),
74          query=state["query"],
75      )
```

| Line | Plain meaning |
|---|---|
| 69 | Take the letter and fill in its blanks. The finished letter is called `prompt`. |
| 70 | Fill `{today}` with today's date. |
| 71 | Fill `{doc_count}` with how many documents you have. `len` means "count them." |
| 72 | Fill `{doc_names}` with your file names, separated by commas. `docs[:40]` takes at most the first 40 so the letter doesn't get too long. `or "(none uploaded)"` means: if there are no names, write "(none uploaded)" instead. |
| 73 | Fill `{history}` with the last 6 messages of the conversation, turned into plain text. `[-6:]` means "the last six." |
| 74 | Fill `{query}` with your message. |
| 75 | Close the bracket. |

```python
76      try:
77          r: Route = structured(Route).invoke(prompt)
78          intent, clear, standalone, reason = r.intent, r.query_is_clear, r.standalone_query.strip(), r.reasoning
79      except Exception as e:
80          log.warning("Router failed, defaulting: %s", e)
81          intent = "rag" if docs else "direct"
82          clear, standalone, reason = True, state["query"], f"Router error, defaulted to {intent}."
```

`try` and `except` work together: "**try** this; if anything goes wrong, do the **except** part instead of crashing."

| Line | Plain meaning |
|---|---|
| 76 | "Try the following." |
| 77 | **This is the line where the decision is made.** Send the letter to the AI and make it fill in the `Route` form. The filled-in form is called `r`. |
| 78 | Copy the four answers off the form into four short names. `.strip()` trims stray spaces from the ends of the rewritten question. |
| 79 | "If anything went wrong above (say, the internet dropped)…" The error itself is called `e`. |
| 80 | Write a warning into the server terminal, including the error. |
| 81 | Make a sensible guess instead: if you have documents, treat it as a document question; otherwise answer from the AI's own knowledge. |
| 82 | Treat the question as clear, use your message unchanged, and record a reason that says the router failed and what it fell back to. |

So even if the AI is unreachable, the chat still gets an answer.

```python
84      standalone = standalone or state["query"]
85      detail = f"Intent → {intent}. {reason}"
86      if intent in ("rag", "web_search") and not clear:
87          detail += " Query looks vague — will rewrite before searching."
```

| Line | Plain meaning |
|---|---|
| 84 | Safety check: if the rewritten question came back empty, use your original message. |
| 85 | Build the diary sentence, for example "Intent → rag. The user asks about the uploaded file." The `f` before the quotes lets us drop values into the text using curly brackets. |
| 86 | If this is a document or internet question **and** it is unclear… |
| 87 | …add a note to the diary sentence that it will be reworded first. `+=` means "add onto the end." |

```python
88      return {
89          "intent": intent,
90          "query_is_clear": clear,
91          "standalone_query": standalone,
92          "route_reason": reason,
93          "search_queries": [standalone],
94          "attempts": 0,
95          "rewrite_reason": "" if clear else "vague_query",
96          "trace": [_step("router", detail, intent=intent, query_is_clear=clear, standalone_query=standalone)],
97      }
```

| Line | Plain meaning |
|---|---|
| 88 | Hand back these changes to the backpack: |
| 89 | The kind of question. |
| 90 | Whether it is clear. |
| 91 | The self-contained version of it. |
| 92 | Why that kind was chosen. |
| 93 | The first thing to search for: a list holding just the self-contained question. |
| 94 | Set the search counter to zero. No searches yet. |
| 95 | If the question is clear, leave the "why reword" note empty. Otherwise write `vague_query`. |
| 96 | Add one diary entry. It is wrapped in a list `[...]` because the diary pocket joins lists together. |
| 97 | Close the bracket. |

## 3.3 Greeting (lines 100–105)

```python
101  def greeting_node(state: AgentState) -> dict:
102      text = greeting.respond(state["query"], state.get("history", []), state.get("username", "there"),
103                              len(state.get("doc_names", [])))
104      return {"answer": text, "tool": "greeting", "sources": [], "evaluation": None,
105              "trace": [_step("greeting", "Responded conversationally.")]}
```

| Line | Plain meaning |
|---|---|
| 100 | Comment heading. |
| 101 | Define the greeting stop. |
| 102–103 | Ask the greeting helper for a friendly reply. It is given your message, the conversation so far, your name (or the word "there" if the name is missing, giving "Hi there"), and how many documents you have. The reply is called `text`. |
| 104 | Hand back: the reply as the answer; `greeting` as the method used; no sources; and no report card (`None` means "nothing"), because small talk isn't graded. |
| 105 | Add a diary entry. |

## 3.4 Direct answer: the AI's own knowledge (lines 108–121)

```python
109  def direct_node(state: AgentState) -> dict:
110      text, needs_web = direct_answer.answer(state["standalone_query"], state.get("history", []))
```

| Line | Plain meaning |
|---|---|
| 108 | Comment heading. |
| 109 | Define the direct-answer stop. |
| 110 | Ask the AI to answer from what it already knows. The helper hands back **two things**: the answer (`text`), and a yes/no flag (`needs_web`) that is true when the AI said "this is too recent, or I don't know." |

```python
111      if needs_web:
112          return {
113              "intent": "web_search",
114              "answer": "",
115              "search_queries": [state["standalone_query"]],
116              "attempts": 0,
117              "trace": [_step("direct_answer",
118                              "Question is beyond the model's knowledge cutoff → escalating to web search.")],
119          }
```

| Line | Plain meaning |
|---|---|
| 111 | If the AI admitted it doesn't know… |
| 112 | …hand back these changes: |
| 113 | **Change the plan**: this is now an internet question. |
| 114 | No answer yet. |
| 115 | Set up what to search for. |
| 116 | Reset the search counter, so the web search gets its full number of tries. |
| 117–118 | Add a diary entry saying it is switching to a web search. |
| 119 | Close the bracket. |

```python
120      return {"answer": text, "tool": "direct", "sources": [],
121              "trace": [_step("direct_answer", "Answered from the model's own knowledge.")]}
```

| Line | Plain meaning |
|---|---|
| 120 | Otherwise (the AI did know): hand back the answer, mark the method as `direct`, with no sources. |
| 121 | Add a diary entry. |

## 3.5 Rewording the question (lines 124–195)

### The form

```python
125  class Rewrite(BaseModel):
126      rewritten_query: str = Field(description="A clearer, more specific search query.")
127      expansions: list[str] = Field(
128          description="2-3 alternative queries: synonyms / domain terms, a broader step-back query, "
129                      "or sub-questions. Each must differ meaningfully from queries already tried."
130      )
131      strategy: str = Field(description="One short sentence describing what was changed and why.")
```

| Line | Plain meaning |
|---|---|
| 124 | Comment heading. |
| 125 | Design a second form, called `Rewrite`. |
| 126 | Blank 1: a clearer, more specific version of the question. |
| 127–130 | Blank 2: a list of two or three **other ways** to ask it. The hint suggests using different words for the same thing, asking a broader question, or splitting it into smaller questions. Each must be genuinely different from what was already tried. |
| 131 | Blank 3: one sentence saying what was changed and why. |

### The instructions

```python
134  REWRITE_PROMPT = """You improve search queries for a {target}. Today is {today}.
135
136  Original user question: {query}
137  Self-contained version: {standalone}
138  Recent conversation:
139  {history}
140  {docs_line}
141  Why a rewrite is needed: {reason}
142  {feedback}
143  Queries already tried (do NOT repeat them): {tried}
144
145  Attempt {attempt}. Strategy guidance:
146  - Attempt 1: fix ambiguity/typos, resolve references, add the precise terminology the source would use.
147  - Attempt 2+: broaden with a step-back query and decompose into sub-questions; try different vocabulary.
148  Keep the user's intent. {extra}"""
```

| Lines | Plain meaning |
|---|---|
| 134 | Tell the AI its job is improving search phrasing, for either the internet or your documents (`{target}`), and give today's date. |
| 136–137 | Your original question and the self-contained version. |
| 138–139 | The recent conversation. |
| 140 | Your document names (left empty for internet searches). |
| 141 | Why a rewording is needed. |
| 142 | What the checker said was missing, if anything. |
| 143 | Everything already tried, with an order **not to repeat any of it**. |
| 145–147 | Which attempt this is, and advice. First try: fix unclear wording and typos, and use the terms the source would use. Later tries: ask a broader question, split it into parts, use different words. |
| 148 | Keep your original meaning, plus one extra style tip (`{extra}`). |

### The reasons, in full sentences

```python
150  _REASONS = {
151      "vague_query": "The query is vague or under-specified and would retrieve poorly as written.",
152      "no_relevant_docs": "The last search returned no relevant passages.",
153      "insufficient_answer": "An answer was generated but the evaluator judged it insufficient.",
154      "no_web_results": "The web search returned no usable results.",
155  }
```

| Line | Plain meaning |
|---|---|
| 150 | A small lookup table. Other stops leave a short code; this table turns each code into a sentence the AI can understand. |
| 151 | The question was too vague. |
| 152 | The last search found nothing useful. |
| 153 | An answer was written but judged not good enough. |
| 154 | The web search found nothing usable. |
| 155 | Close the bracket. |

### The rewording function

```python
158  def rewrite_node(state: AgentState) -> dict:
159      is_web = state["intent"] == "web_search"
160      tried = list(dict.fromkeys([state["standalone_query"], *state.get("rewritten_queries", [])]))
161      reason_key = state.get("rewrite_reason") or "vague_query"
162      missing = (state.get("evaluation") or {}).get("missing", "")
```

| Line | Plain meaning |
|---|---|
| 158 | Define the rewording stop. |
| 159 | Work out a yes/no: is this an internet question? `==` means "is equal to." |
| 160 | Build the list of everything already tried: the self-contained question plus every earlier rewording. The `*` spreads the earlier rewordings out into the list. `dict.fromkeys(...)` is a trick that **removes duplicates while keeping the order**. |
| 161 | Read the code for why we are rewording. If the pocket is empty, assume "vague question." |
| 162 | Read what the checker said was missing. The pocket may be empty, so `or {}` swaps "nothing" for an empty dictionary first, which keeps the next step from failing. If there is no note, use empty text. |

```python
163      prompt = REWRITE_PROMPT.format(
164          target="web search engine" if is_web else "semantic vector search over the user's documents",
165          today=today(),
166          query=state["query"],
167          standalone=state["standalone_query"],
168          history=history_to_text(state.get("history", [])[-4:]),
169          docs_line="" if is_web else f"User's documents: {', '.join(state.get('doc_names', [])[:30])}",
170          reason=_REASONS.get(reason_key, reason_key),
171          feedback=f"Evaluator feedback on what was missing: {missing}" if missing else "",
172          tried="; ".join(tried),
173          attempt=max(1, state.get("attempts", 0)),
174          extra="Write queries like a search engine user would (keywords, include the year if time-sensitive)."
175          if is_web else "Phrase queries like statements that could appear in the document.",
176      )
```

| Line | Plain meaning |
|---|---|
| 163 | Fill in the blanks of the rewording letter. |
| 164 | The target: "web search engine" for internet questions, otherwise "search over the user's documents." The pattern `A if condition else B` means "use A when the condition is true, otherwise B." |
| 165 | Today's date. |
| 166 | Your original message. |
| 167 | The self-contained version. |
| 168 | The last 4 messages of the conversation. |
| 169 | Your document names (up to 30), or nothing for internet questions. |
| 170 | The reason as a full sentence, looked up in the table. If the code isn't in the table, use the code itself. |
| 171 | The checker's note about what was missing, or nothing if there was no note. |
| 172 | Everything already tried, joined with semicolons. |
| 173 | The attempt number: the search counter, but never less than 1. `max(1, x)` picks the larger of the two. |
| 174–175 | The style tip. For the internet: write like someone typing keywords into a search engine, and include the year for time-sensitive topics. For documents: write like a sentence that might appear in the document, because that matches document text better than a question does. |
| 176 | Close the bracket. |

```python
177      try:
178          r: Rewrite = structured(Rewrite).invoke(prompt)
179          rewritten = r.rewritten_query.strip() or state["standalone_query"]
180          expansions = [e.strip() for e in r.expansions if e.strip()][:3]
181          strategy = r.strategy
182      except Exception as e:
183          log.warning("Rewrite failed: %s", e)
184          rewritten, expansions, strategy = state["standalone_query"], [], "Rewrite failed; reusing query."
```

| Line | Plain meaning |
|---|---|
| 177 | "Try the following." |
| 178 | Send the letter to the AI and have it fill in the `Rewrite` form. |
| 179 | Take the reworded question, with stray spaces trimmed. If it came back empty, keep the self-contained question. |
| 180 | Go through the alternative phrasings: trim each one, skip any that are empty, and keep at most three. |
| 181 | Take the one-sentence explanation. |
| 182 | "If anything went wrong…" |
| 183 | Write a warning in the server terminal. |
| 184 | Fall back to the same question with no alternatives, and note that the rewording failed. |

```python
186      # RAG benefits from multi-query retrieval; for the web we keep the single best query (+1 alt).
187      queries = [rewritten, *expansions] if not is_web else [rewritten, *expansions[:1]]
188      queries = list(dict.fromkeys(queries))
```

| Line | Plain meaning |
|---|---|
| 186 | A comment explaining the next line. |
| 187 | Decide what to search next. For documents: the rewording plus all the alternatives (up to 4 searches, because document searches are quick). For the internet: the rewording plus only one alternative (web searches are slow). |
| 188 | Remove any duplicates, keeping the order. |

```python
189      return {
190          "search_queries": queries,
191          "rewritten_queries": [rewritten],
192          "trace": [_step("rewrite_query",
193                          f"{'Expanded' if expansions else 'Rewrote'} query ({reason_key.replace('_', ' ')}): {strategy}",
194                          rewritten=rewritten, expansions=expansions)],
195      }
```

| Line | Plain meaning |
|---|---|
| 189 | Hand back these changes: |
| 190 | The new list of phrasings to search with. This replaces the old list. |
| 191 | The rewording, to be **added onto** the running list of rewordings (the special pocket). |
| 192–194 | A diary entry. The sentence starts with "Expanded" if there are alternatives, otherwise "Rewrote"; then the reason with underscores turned into spaces; then the AI's explanation. |
| 195 | Close the bracket. |

## 3.6 The document path: search, check, write (lines 198–235)

### Search your documents

```python
199  def retrieve_node(state: AgentState) -> dict:
200      queries = state.get("search_queries") or [state["standalone_query"]]
201      hits = retrieve(queries, state["user_id"])
202      attempt = state.get("attempts", 0) + 1
203      top = hits[0]["score"] if hits else None
```

| Line | Plain meaning |
|---|---|
| 198 | Comment heading. |
| 199 | Define the search stop. |
| 200 | Get the phrasings to search with. If there are none, use the self-contained question. |
| 201 | **Search your documents.** Only your own documents are searched, because your ID is passed in. The pieces found are called `hits`. |
| 202 | Add one to the search counter. |
| 203 | Note the best match score, for the diary. The best match is first in the list. If nothing was found, record "nothing." |

```python
204      return {
205          "documents": hits,
206          "attempts": attempt,
207          "trace": [_step("retrieve",
208                          f"Attempt {attempt}: searched {len(queries)} quer{'y' if len(queries) == 1 else 'ies'}, "
209                          f"{len(hits)} candidate chunk(s) above similarity threshold.",
210                          queries=queries, candidates=len(hits), top_score=top)],
211      }
```

| Line | Plain meaning |
|---|---|
| 204 | Hand back: |
| 205 | The pieces found (not yet checked). |
| 206 | The updated search counter. |
| 207–210 | A diary entry such as "Attempt 1: searched 4 queries, 6 candidate chunk(s) above similarity threshold." The small piece on line 208 picks "query" for one and "queries" for several, so the grammar is right. |
| 211 | Close the bracket. |

### Check which pieces are actually useful

```python
214  def grade_node(state: AgentState) -> dict:
215      candidates = state.get("documents", [])
216      relevant = grade_documents(state["standalone_query"], candidates)
```

| Line | Plain meaning |
|---|---|
| 214 | Define the checking stop. |
| 215 | Take the pieces that were found. |
| 216 | **Have the AI read each piece and keep only the ones that help answer the question.** A search finds text that looks similar; this step checks that it is actually useful. |

```python
217      update = {
218          "documents": relevant,
219          "trace": [_step("grade_documents",
220                          f"{len(relevant)}/{len(candidates)} chunk(s) judged relevant.",
221                          relevant=len(relevant), candidates=len(candidates))],
222      }
223      if not relevant:
224          update["rewrite_reason"] = "no_relevant_docs"
225      return update
```

| Line | Plain meaning |
|---|---|
| 217 | Start building the changes, in a dictionary called `update`. |
| 218 | Replace the found pieces with only the useful ones. |
| 219–221 | A diary entry such as "3/4 chunk(s) judged relevant." |
| 222 | Close the bracket. |
| 223 | If no piece was useful… |
| 224 | …leave a note saying why a rewording is needed: nothing relevant was found. |
| 225 | Hand back the changes. |

### Write the answer from the useful pieces

```python
228  def generate_rag_node(state: AgentState) -> dict:
229      docs = state["documents"]
230      text = rag.generate(state["standalone_query"], docs, state.get("history", []))
231      if text is None:
232          return {"answer": "", "documents": [], "rewrite_reason": "no_relevant_docs",
233                  "trace": [_step("generate", f"The {len(docs)} chunk(s) don't actually answer the question.")]}
234      return {"answer": text, "tool": "rag", "sources": rag.to_sources(docs),
235              "trace": [_step("generate", f"Generated grounded answer from {len(docs)} chunk(s).")]}
```

| Line | Plain meaning |
|---|---|
| 228 | Define the answer-writing stop for documents. |
| 229 | Take the useful pieces. |
| 230 | **Ask the AI to write an answer using only those pieces.** If the pieces turn out not to contain the answer, the helper hands back "nothing" (`None`) instead of text. |
| 231 | If it handed back nothing… |
| 232 | …clear the answer, clear the pieces, and leave the note "nothing relevant was found." |
| 233 | Add a diary entry saying the pieces don't actually answer the question. |
| 234 | Otherwise: hand back the answer, mark the method as `rag`, and turn the pieces into source cards for the app. |
| 235 | Add a diary entry. |

Line 231 is a second safety net. The checker may have approved the pieces, but if the writer finds they don't hold the answer, the robot says so instead of inventing something.

## 3.7 The internet path (lines 238–264)

### Search the web

```python
239  def web_search_node(state: AgentState) -> dict:
240      queries = state.get("search_queries") or [state["standalone_query"]]
241      results, seen = [], set()
242      for q in queries[:2]:
243          for r in web_search.search(q):
244              if r["url"] not in seen:
245                  seen.add(r["url"])
246                  results.append(r)
247      results = results[: settings.web_results + 2]
```

| Line | Plain meaning |
|---|---|
| 238 | Comment heading. |
| 239 | Define the web-search stop. |
| 240 | Get the phrasings to search with, or fall back to the self-contained question. |
| 241 | Prepare two empty containers: a list for the results, and a set for the web addresses already seen. A **set** is a collection that can tell you instantly whether something is already in it. |
| 242 | For each of the first two phrasings… |
| 243 | …search the web, and for each page found… |
| 244 | …if we have not seen this address before… |
| 245 | …remember the address… |
| 246 | …and keep the page. |
| 247 | Keep at most 7 pages (the setting is 5, plus 2), so the next step isn't overloaded. |

Lines 244–246 stop the same page being counted twice when two searches find it.

```python
248      attempt = state.get("attempts", 0) + 1
249      update = {
250          "web_results": results,
251          "attempts": attempt,
252          "trace": [_step("web_search", f"Attempt {attempt}: found {len(results)} web result(s).",
253                          queries=queries[:2], urls=[r["url"] for r in results])],
254      }
255      if not results:
256          update["rewrite_reason"] = "no_web_results"
257      return update
```

| Line | Plain meaning |
|---|---|
| 248 | Add one to the search counter. |
| 249 | Start building the changes. |
| 250 | The pages found. |
| 251 | The updated counter. |
| 252–253 | A diary entry such as "Attempt 1: found 5 web result(s)," along with the phrasings used and the list of addresses. |
| 254 | Close the bracket. |
| 255 | If no pages were found… |
| 256 | …leave the note "the web search found nothing." |
| 257 | Hand back the changes. |

### Write the answer from the web pages

```python
260  def generate_web_node(state: AgentState) -> dict:
261      results = state["web_results"]
262      text = web_search.generate(state["standalone_query"], results, state.get("history", []))
263      return {"answer": text, "tool": "web_search", "sources": web_search.to_sources(results),
264              "trace": [_step("generate", f"Synthesized answer from {len(results)} web source(s).")]}
```

| Line | Plain meaning |
|---|---|
| 260 | Define the answer-writing stop for the web. |
| 261 | Take the pages found. |
| 262 | Ask the AI to write an answer from those pages. |
| 263 | Hand back the answer, mark the method as `web_search`, and turn the pages into clickable source cards. |
| 264 | Add a diary entry. |

## 3.8 Checking the answer (lines 267–296)

```python
268  def evaluate_node(state: AgentState) -> dict:
269      tool = state.get("tool")
270      if tool == "rag":
271          context = rag.format_context(state.get("documents", []))
272      elif tool == "web_search":
273          context = "\n\n".join(f"[{i}] {r['content'][:1200]}" for i, r in enumerate(state.get("web_results", []), 1))
274      else:
275          context = None
276      ev = evaluate_answer(state["standalone_query"], state["answer"], context)
```

| Line | Plain meaning |
|---|---|
| 267 | Comment heading. |
| 268 | Define the checking stop. |
| 269 | Find out which method produced the answer. |
| 270–271 | If it came from your documents, gather the same document pieces the writer used. |
| 272–273 | Otherwise, if it came from the web (`elif` means "else, if"), gather the text of each page, numbered from 1 and cut to 1,200 characters each, separated by blank lines. |
| 274–275 | Otherwise (the AI's own knowledge), there is no material to compare against. |
| 276 | **Have the AI grade the answer**, comparing it with that material. The report card is called `ev`. It says whether the answer is complete, whether it is backed by the material, a score from 1 to 5, and what is missing. |

Giving the checker the same material the writer saw is how it catches made-up claims.

```python
278      candidate = {"answer": state["answer"], "tool": tool, "sources": state.get("sources", []), "evaluation": ev}
279      best = state.get("best")
280      if best is None or ev.get("score", 0) > best["evaluation"].get("score", 0):
281          best = candidate
```

| Line | Plain meaning |
|---|---|
| 278 | Bundle this attempt together: the answer, the method, the sources and the report card. |
| 279 | Look at the best attempt saved so far. |
| 280 | If nothing is saved yet, **or** this attempt scored higher than the saved one… |
| 281 | …this attempt becomes the new best. |

```python
283      ok = ev.get("sufficient") and ev.get("grounded", True)
284      detail = (f"Answer {'sufficient' if ok else 'NOT sufficient'} "
285                f"(score {ev.get('score')}/5, grounded={ev.get('grounded')}).")
286      if not ok and ev.get("missing"):
287          detail += f" Missing: {ev['missing']}"
288      update = {"evaluation": ev, "best": best, "trace": [_step("evaluate", detail, **ev)]}
```

| Line | Plain meaning |
|---|---|
| 283 | Decide pass or fail. The answer passes only if it is complete **and** backed by the material. |
| 284–285 | Build the diary sentence, such as "Answer NOT sufficient (score 2/5, grounded=True)." |
| 286–287 | If it failed and the checker said what was missing, add that to the sentence. |
| 288 | Start building the changes: the report card, the best attempt so far, and a diary entry. `**ev` unpacks the whole report card into the diary entry's extra facts. |

```python
289      if not ok:
290          update["rewrite_reason"] = "insufficient_answer"
291          if tool == "direct":
292              # The model's own knowledge wasn't enough: escalate to a fresh web search.
293              update.update({"intent": "web_search", "attempts": 0,
294                             "search_queries": [state["standalone_query"]], "rewrite_reason": ""})
295              update["trace"].append(_step("escalate", "Direct answer judged insufficient → escalating to web search."))
296      return update
```

| Line | Plain meaning |
|---|---|
| 289 | If the answer failed… |
| 290 | …leave the note "the answer wasn't good enough." |
| 291 | And if that answer came from the AI's own knowledge… |
| 292 | (A comment: its own knowledge wasn't enough, so go to the web.) |
| 293–294 | …change the plan to an internet question, reset the search counter, set up what to search for, and clear the rewording note (we are switching method, not rewording). |
| 295 | Add a second diary entry saying it is switching to a web search. `.append` adds to the end of a list. |
| 296 | Hand back the changes. |

## 3.9 The endings (lines 299–333)

### When nothing was found

```python
300  def no_results_node(state: AgentState) -> dict:
301      if state["intent"] == "web_search":
302          text = ("I searched the web but couldn't find results relevant to your question, even after "
303                  "rephrasing the search. Try adding more specific details (names, dates, places).")
304      elif not state.get("doc_names"):
305          text = ("You haven't uploaded any documents yet, so there's nothing to search. "
306                  "Upload files on the Documents page and ask again.")
307      else:
308          tried = [state["standalone_query"], *state.get("rewritten_queries", [])]
309          text = ("No matches found in your documents for this question. I searched with "
310                  f"{len(dict.fromkeys(tried))} different phrasing(s) but none of the passages were relevant. "
311                  "Try rephrasing, naming the specific document or section, or upload a document that covers this topic.")
312      return {"answer": text, "tool": "no_results", "sources": [], "evaluation": None,
313              "trace": [_step("no_results", "No relevant evidence found — informing the user.")]}
```

| Line | Plain meaning |
|---|---|
| 299 | Comment heading. |
| 300 | Define the "nothing found" stop. |
| 301–303 | If this was an internet question: the message says the web search found nothing relevant, even after rewording, and suggests adding names, dates or places. |
| 304–306 | Otherwise, if you have no documents at all: the message says there is nothing to search yet, and to upload files first. |
| 307 | Otherwise (you have documents, but none matched)… |
| 308 | …build the list of every phrasing tried. |
| 309–311 | The message says **"No matches found in your documents,"** states how many different phrasings were tried (duplicates removed before counting), and suggests next steps. |
| 312 | Hand back that message as the answer, mark the method as `no_results`, with no sources and no report card. |
| 313 | Add a diary entry. |

This is the honest ending. The robot says it could not find an answer instead of making one up.

### Handing over the final answer

```python
316  def finalize_node(state: AgentState) -> dict:
317      ev = state.get("evaluation")
318      best = state.get("best")
319      if not ev or (ev.get("sufficient") and ev.get("grounded", True)):
320          return {"trace": [_step("finalize", "Returning answer.")]}
```

| Line | Plain meaning |
|---|---|
| 316 | Define the last stop. |
| 317 | Read the latest report card. |
| 318 | Read the best attempt saved so far. |
| 319 | If there is no report card, **or** the latest answer passed… |
| 320 | …change nothing. Just add a diary entry and finish. This is the normal, happy ending. |

```python
322      # Retries exhausted and the latest answer is still weak: fall back to the best candidate seen.
323      chosen = best or {"answer": state["answer"], "tool": state["tool"], "sources": state.get("sources", []),
324                        "evaluation": ev}
325      note = {
326          "rag": "Note: your documents may not fully cover this question — the answer above is limited to what they contain.",
327          "web_search": "Note: the web results may not fully answer this question; consider verifying with a primary source.",
328      }.get(chosen["tool"])
329      answer = f"{chosen['answer']}\n\n{note}" if note else chosen["answer"]
```

| Line | Plain meaning |
|---|---|
| 322 | A comment: we are out of retries and the latest answer is still weak, so use the best one seen. |
| 323–324 | Choose the best attempt. If none was saved, use the current answer. |
| 325–328 | Pick a warning note depending on the method: one wording for documents, another for the web. For any other method there is no note. |
| 329 | If there is a note, put it under the answer after a blank line (`\n\n` means "two line breaks"). Otherwise leave the answer as it is. |

```python
330      return {
331          "answer": answer, "tool": chosen["tool"], "sources": chosen["sources"], "evaluation": chosen["evaluation"],
332          "trace": [_step("finalize", "Retry budget exhausted; returning the best-scoring answer.")],
333      }
```

| Line | Plain meaning |
|---|---|
| 330 | Hand back: |
| 331 | The chosen answer (with its note), and the method, sources and report card that belong to **that** answer, so everything shown matches. |
| 332 | A diary entry saying the retries ran out and the best-scoring answer is being returned. |
| 333 | Close the bracket. |

---

# 4. `workflow.py` — the map

**Purpose:** connect the stops with arrows, and provide the two ways to start a journey: all at once, or step by step with live updates.

## 4.1 The picture at the top (lines 1–17)

```python
 1  """Agentic RAG workflow graph.
 2
 3                          ┌────────────► greeting ─────────────────────────────────┐
 ...
16                          └─(web)──────────► web_search ─► generate_web ─► evaluate ...
17  """
```

| Lines | Plain meaning |
|---|---|
| 1–17 | A description for humans, written as a drawing made of text characters. It shows the same map as the diagram at the top of this document. The computer does nothing with it. |

## 4.2 Borrowing tools (lines 18–25)

```python
18  from functools import lru_cache
19  from typing import Iterator
20
21  from langgraph.graph import END, START, StateGraph
22
23  from backend.config.settings import settings
24  from backend.graph import nodes
25  from backend.graph.state import AgentState
```

| Line | Plain meaning |
|---|---|
| 18 | Borrow `lru_cache`, a tool that remembers a result so the work is done only once. |
| 19 | Borrow `Iterator`, a label meaning "something that hands out items one at a time." |
| 20 | Blank line. |
| 21 | Borrow three things from LangGraph, the map-building toolkit: `START` (the starting point), `END` (the finish), and `StateGraph` (the blank board we draw the map on). |
| 22 | Blank line. |
| 23 | Borrow the project's adjustable numbers, such as how many retries are allowed. |
| 24 | Borrow the whole `nodes.py` file, so we can refer to each stop. |
| 25 | Borrow the backpack description. |

## 4.3 The arrows that choose (lines 28–81)

Each function below looks in the backpack and hands back **the name of the next stop**. They contain no AI. They are plain "if this, go there" rules.

### After the router

```python
29  def route_after_router(state: AgentState) -> str:
30      intent = state["intent"]
31      if intent == "greeting":
32          return "greeting"
33      if intent == "direct":
34          return "direct"
35      if intent == "rag" and not state.get("doc_names"):
36          return "no_results"
37      if not state.get("query_is_clear", True):
38          return "rewrite"
39      return "retrieve" if intent == "rag" else "web_search"
```

| Line | Plain meaning |
|---|---|
| 28 | Comment heading. |
| 29 | Define the rule used after the router. `-> str` means it hands back text: the name of a stop. |
| 30 | Read the kind of question. |
| 31–32 | If it is small talk, go to the greeting stop. |
| 33–34 | If it is general knowledge, go to the direct-answer stop. |
| 35–36 | If it is a document question but you have no documents, go straight to "nothing found." There is no point searching an empty library. |
| 37–38 | If the question is unclear, go to the rewording stop first. |
| 39 | Otherwise: document questions go to the document search; everything else goes to the web search. |

The order matters. The rules are checked from top to bottom, and the first one that matches wins.

### After the direct answer

```python
42  def route_after_direct(state: AgentState) -> str:
43      return "web_search" if state["intent"] == "web_search" else "evaluate"
```

| Line | Plain meaning |
|---|---|
| 42 | Define the rule used after the direct-answer stop. |
| 43 | If the plan was changed to an internet question (the AI said it didn't know), go to the web search. Otherwise go to the checking stop. |

### After rewording

```python
46  def route_after_rewrite(state: AgentState) -> str:
47      return "web_search" if state["intent"] == "web_search" else "retrieve"
```

| Line | Plain meaning |
|---|---|
| 46 | Define the rule used after rewording. |
| 47 | Internet questions go back to the web search; document questions go back to the document search. |

### After checking the document pieces

```python
50  def route_after_grade(state: AgentState) -> str:
51      if state.get("documents"):
52          return "generate_rag"
53      if state.get("attempts", 0) < settings.max_retrieval_attempts:
54          return "rewrite"
55      return "no_results"
```

| Line | Plain meaning |
|---|---|
| 50 | Define the rule used after the document pieces are checked. |
| 51–52 | If any useful pieces remain, go and write the answer. |
| 53–54 | If there are none, but fewer searches have been done than the allowed maximum (3), go and reword the question. `<` means "is less than." |
| 55 | Otherwise (no pieces, and out of tries), go to "nothing found." |

**This is the loop and its stopping rule.** Line 54 sends the robot backwards to try again. Line 53 only allows that while the counter is below the limit.

### After writing an answer from documents

```python
58  def route_after_generate_rag(state: AgentState) -> str:
59      if state.get("answer"):
60          return "evaluate"
61      return "rewrite" if state.get("attempts", 0) < settings.max_retrieval_attempts else "no_results"
```

| Line | Plain meaning |
|---|---|
| 58 | Define the rule used after the document answer is written. |
| 59–60 | If there is an answer, go and check it. |
| 61 | If there isn't (the writer said the pieces don't hold the answer): reword if tries remain, otherwise go to "nothing found." |

### After the web search

```python
64  def route_after_web(state: AgentState) -> str:
65      if state.get("web_results"):
66          return "generate_web"
67      if state.get("attempts", 0) < settings.max_web_attempts:
68          return "rewrite"
69      return "no_results"
```

| Line | Plain meaning |
|---|---|
| 64 | Define the rule used after a web search. |
| 65–66 | If pages were found, go and write the answer. |
| 67–68 | If none were found but tries remain (the web limit is 2), reword and search again. |
| 69 | Otherwise go to "nothing found." |

### After checking the answer

```python
72  def route_after_evaluate(state: AgentState) -> str:
73      ev = state.get("evaluation") or {}
74      if ev.get("sufficient") and ev.get("grounded", True):
75          return "finalize"
76      if state["intent"] == "web_search" and state.get("tool") == "direct":
77          return "web_search"  # escalation from a weak direct answer
78      budget = settings.max_web_attempts if state["intent"] == "web_search" else settings.max_retrieval_attempts
79      if state.get("tool") in ("rag", "web_search") and state.get("attempts", 0) < budget:
80          return "rewrite"
81      return "finalize"
```

| Line | Plain meaning |
|---|---|
| 72 | Define the rule used after an answer is checked. |
| 73 | Read the report card. If there is none, use an empty one so the next lines don't fail. |
| 74–75 | If the answer is complete and backed by the material, go to the final stop. |
| 76–77 | If the plan is now "internet" but the answer came from the AI's own knowledge, that answer was judged weak and the plan was switched. Go to the web search. |
| 78 | Work out the allowed number of tries: 2 for internet questions, 3 for document questions. |
| 79–80 | If the answer came from documents or the web, and tries remain, go and reword. |
| 81 | Otherwise (out of tries), go to the final stop, which will return the best attempt. |

## 4.4 Building the map (lines 84–114)

```python
85  def build_graph():
86      g = StateGraph(AgentState)
```

| Line | Plain meaning |
|---|---|
| 84 | Comment heading. |
| 85 | Define the function that builds the map. |
| 86 | Take a blank board and tell it which backpack the robot carries. The board is called `g`. |

```python
87      g.add_node("router", nodes.router_node)
88      g.add_node("greeting", nodes.greeting_node)
89      g.add_node("direct", nodes.direct_node)
90      g.add_node("rewrite", nodes.rewrite_node)
91      g.add_node("retrieve", nodes.retrieve_node)
92      g.add_node("grade", nodes.grade_node)
93      g.add_node("generate_rag", nodes.generate_rag_node)
94      g.add_node("web_search", nodes.web_search_node)
95      g.add_node("generate_web", nodes.generate_web_node)
96      g.add_node("evaluate", nodes.evaluate_node)
97      g.add_node("no_results", nodes.no_results_node)
98      g.add_node("finalize", nodes.finalize_node)
```

Each line places **one stop** on the board. The first part is the stop's name; the second is the function from `nodes.py` that runs there.

| Line | Stop placed |
|---|---|
| 87 | `router`: decide the kind of question. |
| 88 | `greeting`: say hello. |
| 89 | `direct`: answer from the AI's own knowledge. |
| 90 | `rewrite`: reword the question. |
| 91 | `retrieve`: search your documents. |
| 92 | `grade`: keep only the useful pieces. |
| 93 | `generate_rag`: write an answer from the pieces. |
| 94 | `web_search`: search the internet. |
| 95 | `generate_web`: write an answer from web pages. |
| 96 | `evaluate`: check the answer. |
| 97 | `no_results`: say nothing was found. |
| 98 | `finalize`: hand over the answer. |

These names are the same words the arrow rules above hand back.

```python
100      g.add_edge(START, "router")
101      g.add_conditional_edges("router", route_after_router,
102                              ["greeting", "direct", "rewrite", "retrieve", "web_search", "no_results"])
103      g.add_edge("greeting", END)
104      g.add_conditional_edges("direct", route_after_direct, ["web_search", "evaluate"])
105      g.add_conditional_edges("rewrite", route_after_rewrite, ["retrieve", "web_search"])
106      g.add_edge("retrieve", "grade")
107      g.add_conditional_edges("grade", route_after_grade, ["generate_rag", "rewrite", "no_results"])
108      g.add_conditional_edges("generate_rag", route_after_generate_rag, ["evaluate", "rewrite", "no_results"])
109      g.add_conditional_edges("web_search", route_after_web, ["generate_web", "rewrite", "no_results"])
110      g.add_edge("generate_web", "evaluate")
111      g.add_conditional_edges("evaluate", route_after_evaluate, ["finalize", "rewrite", "web_search"])
112      g.add_edge("no_results", END)
113      g.add_edge("finalize", END)
114      return g.compile()
```

Now the arrows. There are two kinds:

- `add_edge(A, B)` draws a **fixed** arrow: after A, always go to B.
- `add_conditional_edges(A, rule, [choices])` draws a **choosing** arrow: after A, ask the rule where to go. The list names every place the rule might choose.

| Line | Plain meaning |
|---|---|
| 100 | Every journey begins at the router. |
| 101–102 | After the router, ask `route_after_router`. It may choose any of six stops. |
| 103 | After a greeting, the journey ends. |
| 104 | After the direct answer, ask its rule: web search or checking. |
| 105 | After rewording, ask its rule: document search or web search. |
| 106 | After a document search, always check the pieces. |
| 107 | After checking the pieces, ask the rule: write the answer, reword, or "nothing found." |
| 108 | After writing a document answer, ask the rule: check it, reword, or "nothing found." |
| 109 | After a web search, ask the rule: write the answer, reword, or "nothing found." |
| 110 | After writing a web answer, always check it. |
| 111 | After checking an answer, ask the rule: finish, reword, or web search. |
| 112 | After "nothing found," the journey ends. |
| 113 | After the final stop, the journey ends. |
| 114 | Lock the board into a finished, usable map and hand it back. `compile` also checks the map makes sense. |

## 4.5 Build the map only once (lines 117–119)

```python
117  @lru_cache
118  def get_graph():
119      return build_graph()
```

| Line | Plain meaning |
|---|---|
| 117 | A label on the function below meaning "remember the result." |
| 118 | Define the function that fetches the map. |
| 119 | Build it. Because of line 117, this happens only the **first** time. Every later call gets the same map instantly, instead of redrawing it for each question. |

## 4.6 Packing the backpack (lines 122–133)

```python
122  def _initial_state(query: str, user: dict, history: list[dict], doc_names: list[str]) -> AgentState:
123      return {
124          "query": query,
125          "user_id": user["id"],
126          "username": user["username"],
127          "history": history,
128          "doc_names": doc_names,
129          "rewritten_queries": [],
130          "trace": [],
131          "best": None,
132          "evaluation": None,
133      }
```

| Line | Plain meaning |
|---|---|
| 122 | Define a helper that packs the backpack before the journey. It is given your message, who you are, the recent conversation, and your document names. |
| 123 | Hand back a backpack containing: |
| 124 | Your message. |
| 125 | Your ID. |
| 126 | Your name. |
| 127 | The recent conversation. |
| 128 | Your document names. |
| 129 | An empty list of rewordings. |
| 130 | An empty diary. |
| 131 | No best attempt yet. |
| 132 | No report card yet. |
| 133 | Close the bracket. |

Every other pocket starts empty and is filled by the stops.

## 4.7 The safety limit (lines 136–137)

```python
136  # Worst case: router, rewrite + 3×(retrieve, grade, generate, evaluate, rewrite), finalize ≈ 20 steps.
137  _RECURSION_LIMIT = 40
```

| Line | Plain meaning |
|---|---|
| 136 | A comment working out the longest normal journey: about 20 stops. |
| 137 | Set a hard limit of 40 stops per question. If something ever went wrong and the robot kept circling, it would be stopped here. It is a backstop; the counters in the arrow rules are what normally end the loops. |

## 4.8 Running the whole journey at once (lines 140–142)

```python
140  def run_agent(query: str, user: dict, history: list[dict], doc_names: list[str]) -> AgentState:
141      return get_graph().invoke(_initial_state(query, user, history, doc_names),
142                                {"recursion_limit": _RECURSION_LIMIT})
```

| Line | Plain meaning |
|---|---|
| 140 | Define the function that answers a question from start to finish. |
| 141 | Fetch the map, pack the backpack, and send the robot on its journey. `invoke` means "run it to the end." |
| 142 | Apply the 40-stop limit. The finished backpack, with the answer inside, is handed back. |

This is used when the answer is wanted all in one go (the test script uses it).

## 4.9 Running the journey with live updates (lines 145–159)

```python
145  def stream_agent(query: str, user: dict, history: list[dict], doc_names: list[str]) -> Iterator[tuple[str, dict]]:
146      """Yield ("step", trace_step) as nodes finish, then ("final", final_state)."""
147      state: dict = _initial_state(query, user, history, doc_names)
```

| Line | Plain meaning |
|---|---|
| 145 | Define the function that reports progress as it goes. It hands out pairs one at a time: a label and some information. |
| 146 | A short description: hand out each diary line as its stop finishes, then the finished backpack. |
| 147 | Pack a backpack. This copy is kept here so the function can track the whole journey itself. |

```python
148      for update in get_graph().stream(state, {"recursion_limit": _RECURSION_LIMIT}, stream_mode="updates"):
149          for node_update in update.values():
150              if not node_update:
151                  continue
```

| Line | Plain meaning |
|---|---|
| 148 | Start the journey in "streaming" mode. Instead of waiting for the end, we get a report **each time a stop finishes**, containing the changes that stop made. For each report… |
| 149 | …look at the changes inside it… |
| 150–151 | …and if a stop changed nothing, skip it. `continue` means "move on to the next one." |

```python
152              for step in node_update.get("trace", []):
153                  yield "step", step
```

| Line | Plain meaning |
|---|---|
| 152 | For each new diary line in those changes… |
| 153 | …hand it out immediately, labelled `"step"`. `yield` means "hand this out now, then carry on." This is how the app shows "searching… checking…" while the robot is still working. |

```python
154              for key, value in node_update.items():
155                  if key in ("trace", "rewritten_queries"):
156                      state[key] = state.get(key, []) + value
157                  else:
158                      state[key] = value
159      yield "final", state
```

| Line | Plain meaning |
|---|---|
| 154 | For each changed pocket and its new contents… |
| 155–156 | …if it is one of the two special pockets (the diary, or the list of rewordings), **add** the new items to the end of what we already have. |
| 157–158 | For any other pocket, **replace** the old contents. |
| 159 | When the journey is over, hand out the finished backpack, labelled `"final"`. |

Lines 154–158 repeat, by hand, the same "replace or add" rule that `state.py` set up. The function needs its own up-to-date copy of the backpack so it can hand over the complete result at the end.

---

# Putting it together: one question, start to finish

Suppose you have uploaded a résumé and you type **"wat edu"**.

| # | Stop | What happens | Arrow rule that decides the next stop |
|---|---|---|---|
| 1 | `router` | The AI fills in the form: kind = documents, clear = no. | `route_after_router`: unclear, so go to `rewrite`. |
| 2 | `rewrite` | The AI turns it into "What is the person's education?" plus three other phrasings. | `route_after_rewrite`: a document question, so go to `retrieve`. |
| 3 | `retrieve` | Your résumé is searched with all four phrasings. Four pieces are found. The counter becomes 1. | Fixed arrow to `grade`. |
| 4 | `grade` | The AI keeps the one piece that is about education. | `route_after_grade`: a piece remains, so go to `generate_rag`. |
| 5 | `generate_rag` | The AI writes the answer from that piece. | `route_after_generate_rag`: there is an answer, so go to `evaluate`. |
| 6 | `evaluate` | The AI grades it: complete, backed by the résumé, 5 out of 5. | `route_after_evaluate`: it passed, so go to `finalize`. |
| 7 | `finalize` | Nothing to fix. The answer is handed over. | Fixed arrow to the end. |

Each stop added one line to the diary, and `stream_agent` sent each line to your screen as it happened.

Now suppose you asked about a **refund policy** that is not in the résumé. Steps 3 and 4 find nothing useful. `route_after_grade` sends the robot back to `rewrite`, then `retrieve` again, three searches in all. On the third failure the counter has reached the limit, so the rule chooses `no_results`, and you are told **"No matches found in your documents."**

---

# Quick reference

**The backpack (`state.py`):** 21 pockets in five groups: inputs, routing, the retry loop, evidence, output. Two pockets (`rewritten_queries`, `trace`) grow instead of being replaced.

**The stops (`nodes.py`):**

| Stop | Job | Uses the AI? |
|---|---|---|
| `router_node` | Decide the kind of question | Yes |
| `greeting_node` | Reply to small talk | Yes |
| `direct_node` | Answer from the AI's knowledge | Yes |
| `rewrite_node` | Reword and expand the question | Yes |
| `retrieve_node` | Search your documents | No (search only) |
| `grade_node` | Keep only useful pieces | Yes |
| `generate_rag_node` | Write an answer from pieces | Yes |
| `web_search_node` | Search the internet | No (search only) |
| `generate_web_node` | Write an answer from web pages | Yes |
| `evaluate_node` | Grade the answer | Yes |
| `no_results_node` | Say nothing was found | No |
| `finalize_node` | Hand over the best answer | No |

**The arrows (`workflow.py`):** seven choosing rules, none of which use the AI. They read the backpack and name the next stop. Loops end when the search counter reaches 3 (documents) or 2 (web), with a hard limit of 40 stops as a backstop.
