from backend.tools.direct_answer import _gate_passes, _run_gate

tests = [
    "Who wrote Pride and Prejudice?",                       # should pass
    "How do React hooks work?",                             # should pass
    "What is the population of Bhaktapur?",                 # borderline: specific number
    "Summarize the 2019 paper 'Sparse Graph Tricks for Tiny Transformers'",  # invented, must fail
    "What's the capital of Australia and its population?",  # mixed: easy part plus a number
]

for q in tests:
    gate = _run_gate(q)
    print(f"\n{q}\n  passes={_gate_passes(gate)}  {gate.model_dump()}")