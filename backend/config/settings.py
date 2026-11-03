"""Central application settings, loaded from the project-root .env file."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Provider keys ---
    groq_api_key: str = ""
    gemini_api_key: str = ""
    tavily_api_key: str = ""  # optional but recommended; DuckDuckGo/Wikipedia are used otherwise
    open_router_api_key: str = ""
    voyage_api_key: str = ""
    # Wikimedia requires automated clients to identify themselves with a contact URL or email.
    # Set this to your project's repo URL or a contact address.
    bot_contact: str = "https://github.com/"

    # --- LLMs (Groq) ---
    llm_model: str = "openai/gpt-oss-120b"        # answer generation
    fast_llm_model: str = "openai/gpt-oss-20b"    # routing and other short decisions
    # Memory extraction: its own Groq rate limit, and no hidden reasoning tokens (gpt-oss always reasons).
    memory_llm_model: str = "qwen/qwen3.8-27b"
    open_router_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    open_router_base_url: str = "https://openrouter.ai/api/v1"
    llm_temperature: float = 0.0
    # Small talk and open questions (opinions, personal scenarios): a warmer, less repetitive voice.
    # Factual answers keep LLM_TEMPERATURE.
    chat_temperature: float = 0.8

    # --- Embeddings (Gemini) ---
    embedding_model: str = "models/gemini-embedding-001"
    embedding_dim: int = 768

    # --- Memory embeddings (Voyage AI) ---
    voyage_memory_model: str = "voyage-4-lite"
    voyage_memory_dimensions: int = 1024
    voyage_memory_collection: str = "user_memories"

    # --- Vector store (Qdrant) ---
    qdrant_url: str = ""  # e.g. http://localhost:6333 ; empty => embedded on-disk store
    qdrant_api_key: str = ""
    qdrant_path: str = str(BACKEND_DIR / "vector_db" / "qdrant_data")
    # v2 stores a named dense vector plus a BM25 sparse vector per chunk (hybrid search).
    # The original dense-only collection is left untouched; see backend/scripts/migrate_hybrid.py.
    collection_name: str = "agentic_rag_docs_v2"

    # --- Ingestion ---
    upload_dir: str = str(BACKEND_DIR / "documents")
    chunk_size: int = 1000
    chunk_overlap: int = 200
    max_upload_mb: int = 20

    # --- Retrieval / agent loop ---
    retrieval_k: int = 6
    # "hybrid" = dense + BM25 fused with Reciprocal Rank Fusion; "dense" = meaning-only search (old behaviour).
    retrieval_mode: str = "hybrid"
    hybrid_candidates: int = 20         # results taken from EACH search list before fusion
    rrf_k: int = 60                     # RRF constant: score = sum(1 / (rrf_k + rank)); 60 is the standard value
    bm25_k1: float = 1.2                # term-frequency saturation
    bm25_b: float = 0.75                # document-length normalisation
    bm25_avg_len: float = 120.0         # typical chunk length in tokens (after stop-word removal)

    # --- Stage 2: reranking + MMR (replaces the LLM relevance grader and the cosine cut-off)
    rerank_enabled: bool = True
    # "voyage" = hosted reranker (no torch or model in memory: deployable); "local" = the MiniLM cross-encoder.
    reranker_provider: str = "voyage"
    # Calibrated 2026-11-02 (benchmark/calibrate_reranker.py, all 74 benchmark questions): rerank-2.5 put the
    # right passage in its top 5 for 67/67 answerable questions (top-1 88%, MRR 0.930; local MiniLM: top-1 76%,
    # MRR 0.830). On the first 35 questions it also beat rerank-2.5-lite (top-1 100% vs 97%) at the same cost.
    voyage_rerank_model: str = "rerank-2.5"
    voyage_rerank_timeout: float = 15.0  # seconds; a slow call falls back instead of holding up the answer
    # Keep the local model loaded as a backup for Voyage. Costs ~450 MB of RAM and torch, so it is off by
    # default; without it, a failed Voyage call falls back to the LLM relevance check.
    reranker_local_fallback: bool = False
    # rerank-2.5's score scale: unrelated passages still score ~0.3. Measured best scores (74 questions):
    # answerable 0.58-0.99; out of scope 0.34-0.51, except one near-miss at 0.81 (K2 vs a passage on Everest's first
    # ascent). With these values all 67 answerable questions are answered from the documents, 5/7 out-of-scope go
    # straight to the next source, 1 gets one extra search first. Recalibrate if VOYAGE_RERANK_MODEL changes.
    voyage_relevance_high: float = 0.55  # best relevance >= this: the documents answer it
    voyage_relevance_low: float = 0.45   # best relevance < this: the documents don't cover it
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"  # local; ~90 MB, downloaded once
    # Word-pieces read per passage. Benchmarked 2026-10-02: 256 gave the same quality as 512 (chunks are
    # <= 1,000 characters, so the cap rarely truncates) and ~18% faster reranking.
    reranker_max_length: int = 256
    rerank_candidates: int = 20         # fused candidates scored by the cross-encoder
    rerank_top_n: int = 12              # best-scored candidates MMR chooses from
    mmr_lambda: float = 0.7             # 1.0 = pure relevance, 0.0 = pure diversity
    # Calibrated on the benchmark (2026-10-02): out-of-scope questions scored <= 0.0003, answerable ones
    # >= 0.052, and in every answerable case the correct passage was ranked 1st-4th even at low scores.
    # These are specific to the local MiniLM model's score scale: recalibrate them if RERANKER_MODEL changes.
    relevance_high: float = 0.05        # local: best relevance >= this: the documents answer it
    relevance_low: float = 0.01         # local: best relevance < this: the documents don't cover it
    weak_match_retries: int = 1         # extra reworded searches allowed in the "mid" zone
    # Used only when the reranker is unavailable (disabled, or failed to load), together with
    # RAG_SKIP_SCORE below: then the cosine score decides relevance and an LLM grades the passages.
    min_relevance_score: float = 0.45   # cosine floor before LLM grading
    # Documents are searched ONCE per question. In testing, re-searching with reworded queries never found
    # anything the first search missed (embeddings already match meaning, so unrelated content stays
    # unrelated). Vague questions are still reworded BEFORE that single search.
    max_retrieval_attempts: int = 1
    max_web_attempts: int = 2           # web search rounds before falling back to model knowledge

    # Reranker unavailable only (see MIN_RELEVANCE_SCORE).
    # If the best document match scores below this, the documents don't cover the question:
    # skip grading/retries and go straight to the next source. Measured on sample data:
    # answerable questions scored 0.58-0.76, unrelated ones 0.47-0.59.
    rag_skip_score: float = 0.52

    # --- Answer check
    # Check document answers in code first (backend/evaluation/citations.py: every claim cited, citations exist,
    # cited passages contain the claim). When that passes and the reranker rated the evidence "high", the large
    # model's check is skipped (~1,000-1,900 tokens). Anything the code flags still goes to the large model.
    # Benchmarked 2026-11-03 (benchmark/run_checker.py, 43 answers): no problem the large model found was missed;
    # 37/43 answers would skip the large-model check; 187/190 deliberately broken answers were caught.
    citation_precheck: bool = True

    # --- Decision verification loop (large model reviews the small model's decisions)
    verify_decisions: bool = True
    max_review_rounds: int = 2
    review_only_uncertain: bool = True  # review only decisions the small model isn't sure about (saves tokens)

    # gpt-oss models reason before answering; "low" makes the small model's decisions faster and cheaper.
    fast_llm_reasoning_effort: str = "low"
    judge_reasoning_effort: str = "low"  # large model when reviewing/evaluating (answer writing keeps the default)
    web_results: int = 5
    tavily_search_depth: str = "basic"   # basic = 1 credit/search, advanced = 2 (free plan: 1,000 credits/month)
    web_cache_ttl_seconds: int = 3600    # identical searches within this window reuse results (saves credits)
    history_turns: int = 6

    # --- Auth / persistence ---
    database_path: str = str(BACKEND_DIR / "data" / "app.db")
    jwt_secret: str = ""                # auto-generated & persisted when empty
    jwt_expire_minutes: int = 60 * 24
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
