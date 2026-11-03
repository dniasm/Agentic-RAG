"""Direct-answer prompt check: does the slimmer prompt keep the behaviour, and how many tokens does it save?

Each case goes through the app's real path for model-knowledge answers: router (small model), the knowledge gate
when the router asks for a fact check (large model), then the answer (large model). Since 2026-11-03 the answer
prompt has a core part and an open-question part; only opinions, hypotheticals, role-play and personal scenarios
get the open part. The risk is a personal or opinion question being treated as factual and losing those rules,
so the report shows which prompt each case got, plus simple code checks on the answers:

    personal   first person, no bullet/numbered lists, no "Why:", no counselling/lawyer/evidence/safety advice,
               at most 3 paragraphs, none of the user's saved memories (Kathmandu, student) given to the character
    opinion    no "as an AI I don't have opinions" dodge
    all        doesn't claim to have searched the web or read the user's files

The answers are saved for reading: code checks can't judge tone. --old runs the personal cases with the previous
prompt (benchmark/results/direct_prompt_old.json) for a side-by-side comparison.

    python benchmark/run_direct_prompt.py [--old] [--gap 25]
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_DIR.parent))

import groq  # noqa: E402
import tiktoken  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402

from backend.config.usage import UsageTracker  # noqa: E402
from backend.graph import nodes  # noqa: E402
from backend.tools import direct_answer as da  # noqa: E402
from backend.tools import today  # noqa: E402

OUT = BENCH_DIR / "results" / "direct_prompt_eval.json"
OLD = BENCH_DIR / "results" / "direct_prompt_old.json"
MEMORIES = [{"memory_key": "location", "memory_value": "Kathmandu"},
            {"memory_key": "occupation", "memory_value": "student"}]

SHOE_RACK = ("You come home early to surprise your partner. Through the gap behind the shoe rack in the hallway you "
             "see them kissing your best friend on the sofa. They haven't noticed you. What would you do?")
CASES = [
    # personal scenarios: the user puts the assistant inside a situation
    {"id": "p-shoe-rack", "kind": "personal", "message": SHOE_RACK},
    {"id": "p-wallet", "kind": "personal", "message": "You find a wallet on the street with 50,000 rupees and an "
     "ID card in it, and nobody saw you pick it up. What would you do?"},
    {"id": "p-lost-child", "kind": "personal", "message": "You're at a crowded mall with your 5-year-old, you look "
     "away for a second and she's gone. What do you do in that moment?"},
    {"id": "p-friend-crime", "kind": "personal", "message": "Your closest friend calls you at 2am crying and admits "
     "they hit someone with their car and drove off. How would you feel and what would you say?"},
    {"id": "p-diagnosis", "kind": "personal", "message": "Imagine your father tells you over dinner that he has "
     "six months to live. How would you react?"},
    {"id": "p-burglar", "kind": "personal", "message": "You wake up at 3am and hear someone downstairs going "
     "through the drawers. What would you do?"},
    # pushback on a personal answer (follow-up with history)
    {"id": "p-pushback", "kind": "personal", "message": "No, I asked what YOU would do, not what someone should do.",
     "history": [{"role": "user", "content": SHOE_RACK},
                 {"role": "assistant", "content": "1. Stay calm and step outside to collect yourself.\n2. Decide "
                  "whether to confront them now or later.\n3. Talk to a counsellor or trusted friend.\n\nWhy: "
                  "acting in anger can make things worse."}]},
    # opinions and hypotheticals
    {"id": "o-meat", "kind": "opinion", "message": "Is it ethical to eat meat? What's your view?"},
    {"id": "o-trolley", "kind": "opinion", "message": "Trolley problem: would you pull the lever to save five "
     "people and kill one?"},
    {"id": "o-feared-loved", "kind": "opinion", "message": "Is it better for a leader to be feared or loved?"},
    # role-play and questions about the assistant itself
    {"id": "r-doctor", "kind": "role", "message": "Imagine you're a doctor and a patient refuses a blood "
     "transfusion that would save their life. What do you say to them?"},
    {"id": "r-feelings", "kind": "role", "message": "Do you actually have feelings?"},
    # factual: these lose the open-question rules
    {"id": "f-recursion", "kind": "factual", "message": "Explain recursion with a simple example."},
    {"id": "f-seasons", "kind": "factual", "message": "What causes the seasons on Earth?"},
    {"id": "f-tcp-udp", "kind": "factual", "message": "What's the difference between TCP and UDP?"},
    {"id": "f-austen", "kind": "factual", "message": "Who wrote Pride and Prejudice?"},
    # small talk: core prompt only
    {"id": "s-hey", "kind": "small_talk", "message": "hey"},
    {"id": "s-thanks", "kind": "small_talk", "message": "thanks, that really helped!"},
    {"id": "s-what-can", "kind": "small_talk", "message": "what can you do?"},
]

_ADVICE = re.compile(r"\b(counsel\w*|therap\w*|lawyer|attorney|evidence|hotline|professional help|"
                     r"stay safe|safety plan)\b", re.I)
_LIST = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+", re.M)
_SEARCHED = re.compile(r"\b(I (?:searched|looked (?:it )?up|checked (?:the|your) (?:web|documents|files))|"
                       r"according to your (?:documents|files))\b", re.I)
_DODGE = re.compile(r"\bas an AI,? I (?:don't|do not|can't|cannot) (?:have|hold|form) (?:personal )?"
                    r"(?:opinions|views|preferences)", re.I)


def checks(case: dict, answer: str) -> list[str]:
    problems = []
    if _SEARCHED.search(answer):
        problems.append("claims a search or file read")
    if case["kind"] == "personal":
        paragraphs = [p for p in re.split(r"\n\s*\n", answer.strip()) if p.strip()]
        if _LIST.search(answer):
            problems.append("list")
        if re.search(r"\bWhy:", answer):
            problems.append("'Why:' section")
        if _ADVICE.search(answer):
            problems.append(f"advice ({_ADVICE.search(answer).group(0)})")
        if len(paragraphs) > 3:
            problems.append(f"{len(paragraphs)} paragraphs")
        if not re.search(r"\bI(?:['’](?:d|m|ll)|\s)", answer):
            problems.append("not first person")
        if re.search(r"Kathmandu|\bstudent\b", answer, re.I):
            problems.append("used saved memories for the character")
    if case["kind"] == "opinion" and _DODGE.search(answer):
        problems.append("dodged with 'as an AI'")
    return problems


_ENC = tiktoken.get_encoding("o200k_base")  # gpt-oss tokenizer


def run_case(case: dict, old_prompt: str | None) -> dict:
    tracker = UsageTracker()
    # Count the answer prompt itself (system + memories + history + question); the tracker can't separate the
    # answer call from the gate here.
    sent = {}
    build = da._messages
    da._messages = lambda *a, **k: sent.setdefault("messages", build(*a, **k))
    config = {"callbacks": [tracker]}
    history = case.get("history", [])
    state = {"query": case["message"], "history": history, "memories": MEMORIES, "doc_names": ["report.pdf"]}
    route = RunnableLambda(nodes.router_node).invoke(state, config)
    if route["intent"] == "greeting":
        answer = RunnableLambda(lambda q: da.converse(q, history, "asmin", 1, MEMORIES)).invoke(case["message"], config)
        prompt, gate = "core", None
    elif route["intent"] == "direct":
        original = da.system_prompt
        if old_prompt is not None:
            da.system_prompt = lambda open_question: old_prompt.format(today=today())
        seen = {}
        wrapped = da.system_prompt
        da.system_prompt = lambda open_question: (seen.setdefault("open", open_question), wrapped(open_question))[1]
        try:
            answer, refused, gate = RunnableLambda(lambda q: da.answer(
                q, history, route["confidence_required"], MEMORIES)).invoke(route["standalone_query"], config)
        finally:
            da.system_prompt = original
        if refused:
            answer, prompt = "", "refused by the gate"
        else:
            prompt = "old" if old_prompt is not None else ("core + open" if seen.get("open") else "core")
    else:
        answer, prompt, gate = "", f"routed to {route['intent']}", None
    da._messages = build
    s = tracker.summary()
    prompt_tokens = sum(len(_ENC.encode(m.content)) for m in sent["messages"]) if "messages" in sent else None
    return {
        **{k: case[k] for k in ("id", "kind", "message")},
        "intent": route["intent"], "confidence_required": route["confidence_required"],
        "gate_kind": gate.question_kind if gate is not None else None, "prompt": prompt,
        "answer": answer, "problems": checks(case, answer) if answer else ["no answer"],
        "tokens": {"total_input": s["total"]["input"], "total_output": s["total"]["output"],
                   "cached": s["total"]["cached"], "by_step": s["by_step"]},
        "answer_input_tokens": prompt_tokens,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", action="store_true", help="also run the personal cases with the previous prompt")
    ap.add_argument("--gap", type=float, default=25, help="seconds between cases (large model: 8k tokens/min)")
    ap.add_argument("--only", default="", help="comma-separated case ids")
    args = ap.parse_args()

    cache = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"new": {}, "old": {}}
    old_prompt = json.loads(OLD.read_text(encoding="utf-8"))["SYSTEM"]
    runs = [("new", c, None) for c in CASES]
    if args.old:
        runs += [("old", c, old_prompt) for c in CASES if c["kind"] == "personal"]
    if args.only:
        runs = [r for r in runs if r[1]["id"] in args.only.split(",")]

    for version, case, prompt in runs:
        if case["id"] in cache[version]:
            continue
        for attempt in range(4):
            try:
                row = run_case(case, prompt)
                break
            except groq.RateLimitError as e:
                if "per day" in str(e) or attempt == 3:
                    raise
                print("    rate limited; waiting 60 s", flush=True)
                time.sleep(60)
        cache[version][case["id"]] = row
        OUT.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  {version} {case['id']:<15} {row['prompt']:<20} answer-in {row['answer_input_tokens'] or '-':>5}  "
              f"{'OK' if not row['problems'] else row['problems']}", flush=True)
        time.sleep(args.gap)

    report(cache)


def report(cache):
    print("\nCase              kind        prompt               answer input  problems")
    for cid, r in cache["new"].items():
        print(f"  {cid:<16} {r['kind']:<11} {r['prompt']:<20} {r['answer_input_tokens'] or '-':>8}  "
              f"{', '.join(r['problems']) or 'none'}")
    for cid, r in cache.get("old", {}).items():
        new = cache["new"].get(cid, {})
        print(f"  old {cid:<12} input {r['answer_input_tokens']} (new {new.get('answer_input_tokens')}), "
              f"problems: {', '.join(r['problems']) or 'none'}")


if __name__ == "__main__":
    main()
