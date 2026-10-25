from backend.config.llm import structured
from backend.tools import today
from backend.tools.direct_answer import KnowledgeGate, GATE_PROMPT, _gate_passes

tests = [
    "Who wrote Pride and Prejudice?",                       # should pass
    "How do React hooks work?",                             # should pass
    "What is the population of Bhaktapur?",                 # borderline: specific number
    "Summarize the 2019 paper 'Sparse Graph Tricks for Tiny Transformers'",  # invented, must fail
    "What's the capital of Australia and its population?",  # mixed: easy part plus a number
]

for q in tests:
    gate = structured(KnowledgeGate, fast=False, backup=True).invoke(
        GATE_PROMPT.format(today=today(), query=q)
    )
    print(f"\n{q}\n  passes={_gate_passes(gate)}  {gate.model_dump()}")