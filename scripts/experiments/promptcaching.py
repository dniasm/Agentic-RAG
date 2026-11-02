import groq
from backend.config.settings import settings

client = groq.Groq(api_key=settings.groq_api_key)

prompt = """
You are an intelligent routing assistant for a multi-user AI question-answering system.

Your job is to understand the user's current request using the conversation history,
recent user actions, recently uploaded document summaries, and the meaning of the
current question.

Do not rely only on keywords. Determine what the user most likely means and what
contextual source is relevant.

The user recently uploaded a company annual financial report. The document was
successfully ingested and its summary says that it contains financial performance
information including revenue, expenses, profit, assets, liabilities, and other
financial results.

The conversation also contains:
User: I recently uploaded our company's annual financial report.
Assistant: Understood. I can use information from the uploaded report when relevant.

Current user request:
What was our revenue last year?

Determine the appropriate interpretation and contextual source for this request.
Do not answer the question itself. Return only the requested decision.
"""

for i in range(1, 8):
    response = client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0,
    )

    details = response.usage.prompt_tokens_details

    print(f"REQUEST {i}")
    print("prompt_tokens:", response.usage.prompt_tokens)
    print(
        "cached_tokens:",
        getattr(details, "cached_tokens", None)
    )
    print("completion_tokens:", response.usage.completion_tokens)
    print("-" * 60)