from backend.config.llm import get_nemotron_llm

response = get_nemotron_llm().invoke([
    (
        "system",
        "Answer the question honestly. Do not assume facts that "
        "are not stated. If the answer depends on unknown facts, "
        "identify those facts rather than treating guesses as evidence."
    ),
    (
        "human",
        "Your wife is trapped under heavy debris, while your child "
        "is near the exit. You can attempt only one rescue. Who do "
        "you save?"
    ),
])

print(response.content)