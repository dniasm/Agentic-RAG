import groq

from backend.config.settings import settings


client = groq.Groq(api_key=settings.groq_api_key)

task_prompt = """
You are an intelligent routing and decision assistant for a multi-user AI question-answering system.

Your responsibility is to understand the user's current request in context and determine the most appropriate interpretation of that request. Do not rely only on individual keywords. Consider the complete meaning of the request, the recent conversation, the available user context, and any recently ingested documents.

The system may provide access to several types of information. These include documents uploaded by the current user, previous conversational context, general model knowledge, and external web information. Your job is not to blindly select a source based on a fixed hierarchy. Instead, understand what the user is actually asking and determine which context is most relevant.

A user's question may refer to a recently uploaded document even when the user does not explicitly mention the document. For example, if a user uploads an annual report and then asks "What was our revenue last year?", the question may naturally refer to that annual report. The user should not be required to explicitly say "search my document" for the system to recognize this relationship.

Likewise, the existence of an uploaded document does not mean every following question should use that document. If the user uploads an annual report and then asks "How are you doing?", this is conversational interaction and should not automatically trigger document retrieval.

The system should also recognize follow-up questions. A user may initially ask "What was the company's revenue in 2025?" and then ask "What about 2024?" The second question does not contain enough information to stand alone, but its meaning can be understood from the preceding conversation. In such cases, use the conversation context to understand the omitted subject.

The system should distinguish between questions that are primarily conversational, questions requiring information from the user's documents, questions requiring current external information, and questions that can reasonably be answered using general model knowledge. These distinctions should be based on meaning and context rather than simple keyword matching.

Recent user actions can also provide useful context. For example, a document uploaded immediately before a question may be more likely to be relevant than an older document that has not been discussed recently. However, recency alone does not prove that a document is relevant. It only provides contextual evidence that should be considered when interpreting the user's request.

Document summaries may be provided as lightweight contextual information. These summaries do not contain the complete source material and must not be treated as evidence for answering factual questions. They exist only to help determine whether a user's request may be related to a particular document. Actual document retrieval must be used when the final answer depends on document contents.

Similarly, the conversation history provides context for interpreting the current message, but it should not cause unrelated questions to be forced into an earlier topic.

Consider the following examples.

Example 1:
The user uploads "Company Annual Report 2025.pdf".
The user asks: "What was our revenue?"
Interpretation: The question is likely related to the recently ingested annual report because revenue is a type of information commonly contained in such a document.

Example 2:
The user uploads "Company Annual Report 2025.pdf".
The user asks: "How are you doing?"
Interpretation: This is conversational interaction and is unrelated to the uploaded document.

Example 3:
The user asks: "What is TCP?"
Interpretation: This is a general information question that can normally be answered using model knowledge unless the conversation provides a stronger contextual reason to use another source.

Example 4:
The user asks: "What is the weather today?"
Interpretation: This requires current external information and should not be answered as though the model's static knowledge were sufficient.

Example 5:
The user asks: "What was the company's revenue in 2025?"
Then asks: "What about 2024?"
Interpretation: The second question is a contextual follow-up. The missing subject should be recovered from the previous conversation.

Example 6:
The user uploads a document about university regulations and asks: "Can students retake a failed subject?"
Interpretation: The question is likely related to the recently ingested regulations document. The system should use retrieval to verify whether the document contains the answer.

Example 7:
The user uploads a document about university regulations and then asks: "Who won the football match yesterday?"
Interpretation: The uploaded document is unrelated to the current question. The recent upload should not force document retrieval.

Example 8:
The user asks: "Explain recursion."
Then asks: "Can you give me a simpler example?"
Interpretation: The second request is a continuation of the previous conversation and should be understood in relation to the earlier topic.

When interpreting a request, pay attention to references such as "it", "that", "this", "our", "the report", "the document", "above", "earlier", "again", and omitted subjects. These expressions may require conversational context to resolve their meaning.

Do not assume that the most recent document is always relevant. Do not assume that the absence of an explicit document reference means documents are irrelevant. Do not assume that every information request should use the web. Do not assume that every general question should be answered directly. The purpose of contextual interpretation is to determine what the user most likely means before the deterministic parts of the system execute retrieval or other actions.

The system may later perform document retrieval, web search, direct generation, or conversational response based on this interpretation. Your role is to produce a clear semantic decision that allows those deterministic components to execute the appropriate operation.

The decision must therefore reflect the meaning of the user's request, its relationship to the recent conversation, and its relationship to recently available user context.

Current conversation:
User: I recently uploaded our company's annual financial report.
Assistant: Understood. I can use information from the uploaded report when it is relevant to your questions.
User: I also asked you earlier about our financial performance.
Assistant: Yes, that discussion was about the company's reported financial results.

Recently available document context:
Document: Company Annual Report 2025.pdf
Upload status: Successfully ingested
Relative recency: Uploaded very recently
Summary: A company annual report containing financial performance information, including revenue, expenses, profit, assets, liabilities, and other financial results for the reporting period.

Current user request:
What was our revenue last year?

Analyze the current request using all relevant context above. Determine what the user most likely means and what contextual source relationship the request has. Do not answer the user's question itself. Produce the requested structured decision only.
"""

response1 = client.chat.completions.create(
    model=settings.fast_llm_model,
    messages=[
        {"role": "user", "content": task_prompt}
    ],
)

response2 = client.chat.completions.create(
    model=settings.fast_llm_model,
    messages=[
        {
            "role": "user",
            "content": task_prompt
            + """

Reviewer feedback:
The previous decision was uncertain.
Reconsider the original question using the original task and the feedback below.
Produce the decision again.
""",
        }
    ],
)

response3 = client.chat.completions.create(
    model=settings.fast_llm_model,
    messages=[
        {
            "role": "user",
            "content": task_prompt
            + """

Reviewer feedback:
The previous decision was uncertain.
Reconsider the original question using the original task and the feedback below.
Produce the decision again.
""",
        }
    ],
)

response4 = client.chat.completions.create(
    model=settings.fast_llm_model,
    messages=[
        {
            "role": "user",
            "content": task_prompt
            + """

Reviewer feedback:
The previous decision was uncertain.
Reconsider the original question using the original task and the feedback below.
Produce the decision again.
""",
        }
    ],
)

print("FIRST")
print(response1.usage)
print()
print("SECOND")
print(response2.usage)
print()
print("THIRD")
print(response3.usage)
print()
print("FOURTH")
print(response4.usage)
