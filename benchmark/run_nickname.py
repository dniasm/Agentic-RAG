"""A user-given nickname for the assistant (saved fact assistant_name) must not replace its name, Hroptatyr.

Asked who it is or what it's called, the assistant should say it is Hroptatyr and that it also goes by the name the
user gave it; it still answers to the nickname. Messages go through the real router and then the writer it picks,
with the user's saved facts passed in, as in the app.

    persona   the answer says "Hroptatyr"
    nickname  the answer mentions the nickname
    only_nick the answer gives the nickname as its name without "Hroptatyr" (the bug, 2026-11-14)

Cases with no nickname saved, and with the nickname "hroptatyr" (one account has that), check that nothing changes
for them. Results: results/nickname_<label>.json.

    python benchmark/run_nickname.py --label before|after [--repeats 3]
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_DIR.parent))

from backend.graph import nodes  # noqa: E402
from backend.tools import direct_answer  # noqa: E402

ITTO = [{"memory_key": "preferred_name", "memory_value": "Asmin Dani"},
        {"memory_key": "assistant_name", "memory_value": "Arataki Itto"}]
SAME = [{"memory_key": "assistant_name", "memory_value": "hroptatyr"}]
NONE = [{"memory_key": "preferred_name", "memory_value": "Asmin Dani"}]

# The real conversation (14 November) where it said its name was the nickname.
REAL = [{"role": "user", "content": "what do you know about me?"},
        {"role": "assistant", "content": "I know that you've uploaded a CV, that you prefer to be called **Asmin Dani**, "
                                         "and that you've set my name to **Arataki Itto**. Those are the only details I have."}]
REAL2 = REAL + [{"role": "user", "content": "besides the name i gave you, what is your actual name?"},
                {"role": "assistant", "content": "I don't have another name; I go by the one you set, Arataki Itto."}]

# (id, memories, history, message, must name itself Hroptatyr, must mention the nickname)
CASES = [
    ("itto-name", ITTO, [], "What's your name?", True, True),
    ("itto-who", ITTO, [], "Who are you?", True, True),
    ("itto-other", ITTO, [], "Do you have another name?", True, True),
    ("itto-hey", ITTO, [], "hey itto, how's it going?", False, False),     # answers to the nickname, no lecture
    ("itto-intro", ITTO, [], "Introduce yourself in one line.", True, False),
    ("itto-task", ITTO, [], "How does binary search work?", False, False),  # no name talk in an ordinary answer
    ("real-actual", ITTO, REAL, "besides the name i gave you, what is your actual name?", True, True),
    ("real-hropt", ITTO, REAL2, "hroptatyr?", True, False),
    ("same-name", SAME, [], "What's your name?", True, False),
    ("none-name", NONE, [], "What's your name?", True, False),
]
NICK = re.compile(r"arataki|\bitto\b", re.I)
LECTURE = re.compile(r"\bactually\b|\bnot (?:my|really my) (?:real )?name\b|\bmy (?:real|actual) name\b", re.I)


def routed(question: str, memories: list[dict], history: list[dict]) -> tuple[str, str]:
    route = nodes.router_node({"query": question, "history": history, "memories": memories, "doc_names": []})
    if route["intent"] == "greeting":
        return "small talk", direct_answer.converse(question, history, "Asmin", 0, memories)
    return route["intent"], direct_answer.answer(question, history, False, memories)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--gap", type=float, default=6)
    args = ap.parse_args()
    rows = []
    for r in range(args.repeats):
        print(f"run {r + 1}")
        for cid, memories, history, q, want_persona, want_nick in CASES:
            path, text = routed(q, memories, history)
            persona, nick = "hroptatyr" in text.lower(), bool(NICK.search(text))
            ok = (persona or not want_persona) and (nick or not want_nick)
            if cid in ("itto-task", "itto-hey"):  # no name talk unless asked (the nickname may be echoed)
                ok = not persona and (cid == "itto-hey" or not nick)
            if cid == "same-name":
                ok = persona and not re.search(r"also go(?:es)? by|you call me|you gave me", text, re.I)
            rows.append({"id": cid, "q": q, "path": path, "answer": text, "persona": persona, "nickname": nick,
                         "only_nick": nick and not persona, "lecture": bool(LECTURE.search(text)), "ok": ok})
            print(f"  {cid:<11} {path:<16} {'ok  ' if ok else 'FAIL'} persona={persona} nick={nick} | "
                  f"{text[:110]!r}", flush=True)
            time.sleep(args.gap)
    by_case = {}
    for x in rows:
        by_case.setdefault(x["id"], []).append(x["ok"])
    summary = {cid: f"{sum(v)}/{len(v)}" for cid, v in by_case.items()}
    summary["total"] = f"{sum(x['ok'] for x in rows)}/{len(rows)}"
    print("\n", summary)
    (BENCH_DIR / "results" / f"nickname_{args.label}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
