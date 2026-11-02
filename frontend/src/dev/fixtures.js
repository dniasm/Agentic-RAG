// SAMPLE DATA for preview.html only (never used by the app itself). The documents are the benchmark account's
// real list; the conversations, answers, page numbers and usage figures are invented for the preview.
const daysAgo = (d, h = 10) => {
  const t = new Date();
  t.setDate(t.getDate() - d);
  t.setHours(h, 12, 0, 0);
  return t.toISOString();
};

export const user = { id: 'preview-user', username: 'asmin' };

export const documents = [
  { id: 'd1', filename: 'CCS_using_SVM.pdf', pages: 37, chunks: 79, size_bytes: 1111420, status: 'indexed', error: null, created_at: daysAgo(31) },
  { id: 'd2', filename: 'nlp_research_gate.pdf', pages: 87, chunks: 120, size_bytes: 4817444, status: 'indexed', error: null, created_at: daysAgo(31) },
  { id: 'd3', filename: 'nlp.pdf', pages: 95, chunks: 163, size_bytes: 11933013, status: 'indexed', error: null, created_at: daysAgo(31) },
  { id: 'd4', filename: 'okapi_bm25.md', pages: 1, chunks: 8, size_bytes: 4627, status: 'indexed', error: null, created_at: daysAgo(31) },
  { id: 'd5', filename: 'tf_idf.md', pages: 1, chunks: 15, size_bytes: 8950, status: 'indexed', error: null, created_at: daysAgo(31) },
  { id: 'd6', filename: 'CELEX_32016R0679_EN_TXT.pdf', pages: 88, chunks: 466, size_bytes: 982296, status: 'indexed', error: null, created_at: daysAgo(30) },
  { id: 'd7', filename: 'World Bank Annual Report 2024.pdf', pages: 49, chunks: 282, size_bytes: 11289865, status: 'indexed', error: null, created_at: daysAgo(30) },
  { id: 'd8', filename: '9789240061262-eng.pdf', pages: 0, chunks: 0, size_bytes: 1068141, status: 'processing', error: null, created_at: daysAgo(30) },
];

export const conversations = [
  { id: 'c1', title: 'Tech stack of the complaint classifier', created_at: daysAgo(0, 9), updated_at: daysAgo(0, 11) },
  { id: 'c2', title: 'GDPR breach reporting deadline', created_at: daysAgo(0, 8), updated_at: daysAgo(0, 8) },
  { id: 'c3', title: 'BM25 vs dense retrieval', created_at: daysAgo(2), updated_at: daysAgo(2) },
  { id: 'c4', title: 'World Bank commitments in fiscal 2024', created_at: daysAgo(4), updated_at: daysAgo(4) },
  { id: 'c5', title: 'Would you take the lifeboat?', created_at: daysAgo(12), updated_at: daysAgo(12) },
  { id: 'c6', title: 'Latest Python release', created_at: daysAgo(19), updated_at: daysAgo(19) },
];

const ccsSources = [
  { type: 'document', title: 'CCS_using_SVM.pdf', document: 'CCS_using_SVM.pdf', page: 19, score: 0.61,
    snippet: 'CHAPTER 5: IMPLEMENTATION 5.1 Tools Used. Python: primary programming language for model training and the backend. Flask: lightweight web framework serving the complaint portal. MySQL: relational database storing complaints, users and predicted categories.' },
  { type: 'document', title: 'CCS_using_SVM.pdf', document: 'CCS_using_SVM.pdf', page: 15, score: 0.58,
    snippet: 'TF-IDF is used to convert the cleaned complaint text into numerical feature vectors, which are then passed to a linear Support Vector Machine that assigns one of four categories: Technical, Financial, Service or Other.' },
  { type: 'document', title: 'CCS_using_SVM.pdf', document: 'CCS_using_SVM.pdf', page: 20, score: 0.44,
    snippet: 'The admin dashboard is built with HTML, CSS and Bootstrap templates rendered by Flask, and shows complaints by category and status.' },
];

const ccsTrace = [
  { id: 't1', node: 'router', detail: 'Information request; the user asks about their uploaded project report.', data: {} },
  { id: 't2', node: 'review', detail: 'Review skipped: the small model is confident in its intake decision.', data: { skipped: true } },
  { id: 't3', node: 'retrieve', detail: 'Attempt 1: hybrid search; 1 query, 20 candidate chunk(s).', data: {} },
  { id: 't4', node: 'grade_documents', detail: 'Reranked 20 candidates locally: best relevance 0.81 -> strong match; selected 6 diverse passages using MMR.', data: {} },
  { id: 't5', node: 'generate', detail: 'Generated an answer from 6 document chunks.', data: {} },
  { id: 't6', node: 'evaluate', detail: 'Answer sufficient (score 5/5, grounded=True).', data: {} },
  { id: 't7', node: 'finalize', detail: 'Returning the sufficient answer.', data: {} },
];

export const messages = {
  c1: [
    { id: 'm1', role: 'user', content: 'Which tech stack was used to build the complaint classification system?', created_at: daysAgo(0, 9), meta: {} },
    { id: 'm2', role: 'assistant', tool: 'rag', created_at: daysAgo(0, 9),
      content: 'The system is written in **Python**, with **Flask** serving the web application and **MySQL** storing the complaints, users and predicted categories [1]. The admin dashboard is rendered by Flask with HTML, CSS and Bootstrap templates [3].\n\nFor the classifier itself, each complaint is cleaned and converted into **TF-IDF** feature vectors, and a linear **Support Vector Machine** assigns one of four categories: Technical, Financial, Service or Other [2].',
      meta: { sources: ccsSources, notices: [], trace: ccsTrace, rewritten_queries: [],
              evaluation: { sufficient: true, grounded: true, score: 5, missing: '' }, latency_ms: 3420 } },
    { id: 'm3', role: 'user', content: 'Is Flask still a good choice for something like this in 2026?', created_at: daysAgo(0, 11), meta: {} },
    { id: 'm4', role: 'assistant', tool: 'web_search', created_at: daysAgo(0, 11),
      content: 'Yes, for a small internal tool like this one Flask remains a reasonable choice: it is actively maintained, and Flask 3.1 added better async support and typing [1]. Teams building larger APIs increasingly pick FastAPI for its built-in request validation and automatic OpenAPI docs [2], so a rewrite would only pay off if the complaint system grows into a public API.',
      meta: {
        sources: [
          { type: 'web', title: 'Changes — Flask Documentation (3.1.x)', url: 'https://flask.palletsprojects.com/en/stable/changes/', snippet: 'Version 3.1.0. Released 2024-11-13. Drop support for Python 3.8. Add async support improvements…' },
          { type: 'web', title: 'Alternatives, Inspiration and Comparisons - FastAPI', url: 'https://fastapi.tiangolo.com/alternatives/', snippet: 'Flask is a "microframework", it doesn\'t include database integrations nor many of the things that come by default in Django… FastAPI adds data validation, serialization and automatic documentation.' },
        ],
        notices: ["Information matching your question wasn't found in your documents, so it was resolved with a web search."],
        trace: [
          { id: 'w1', node: 'router', detail: 'Information request about current software choices; web search.', data: {} },
          { id: 'w2', node: 'web_search', detail: 'Attempt 1: found 6 usable web result(s).', data: {} },
          { id: 'w3', node: 'generate', detail: 'Generated a candidate answer from 6 web source(s).', data: {} },
          { id: 'w4', node: 'evaluate', detail: 'Answer sufficient (score 4/5, grounded=True).', data: {} },
        ],
        rewritten_queries: ['Flask web framework 2026 still recommended'],
        evaluation: { sufficient: true, grounded: true, score: 4, missing: '' }, latency_ms: 5870 } },
  ],
};

// Usage figures; the parts add up to the totals.
export const stats = (() => {
  const by_tool = { rag: 21, direct: 14, web_search: 7, greeting: 4, no_results: 2 };
  const queries = Object.values(by_tool).reduce((a, b) => a + b, 0);
  const ready = documents.filter((d) => d.status === 'indexed');
  const s = {
    documents: ready.length,
    chunks: ready.reduce((a, d) => a + d.chunks, 0),
    conversations: 11,
    queries,
    by_tool,
    queries_rewritten: 6,
    answers_evaluated: 28,
    answers_sufficient: 25,
    avg_latency_ms: 4180,
  };
  console.assert(s.answers_evaluated === by_tool.rag + by_tool.web_search, 'evaluated = document + web answers');
  console.assert(s.answers_sufficient <= s.answers_evaluated, 'sufficient <= evaluated');
  return s;
})();

export const emptyStats = {
  documents: 0, chunks: 0, conversations: 0, queries: 0, by_tool: {}, queries_rewritten: 0,
  answers_evaluated: 0, answers_sufficient: 0, avg_latency_ms: 0,
};
