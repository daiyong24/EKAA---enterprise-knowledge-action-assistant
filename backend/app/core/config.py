from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="EKAA_",
        extra="ignore",
    )

    app_name: str = "EKAA API"
    environment: Literal["development", "test", "staging", "production"] = "development"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    api_prefix: str = Field(default="", pattern=r"^$|^/.*[^/]$")
    llm_enabled: bool = False
    llm_provider: Literal["openai_compatible"] = "openai_compatible"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    llm_max_retries: int = Field(default=2, ge=0, le=3)
    llm_retry_delay_seconds: float = Field(default=0.1, ge=0, le=5)
    reliability_chat_deadline_seconds: float = Field(default=30.0, gt=0, le=120)
    reliability_rag_deadline_seconds: float = Field(default=30.0, gt=0, le=120)
    reliability_retry_base_seconds: float = Field(default=0.1, ge=0, le=5)
    reliability_retry_max_seconds: float = Field(default=5.0, gt=0, le=30)
    reliability_retry_jitter_ratio: float = Field(default=0.2, ge=0, le=1)
    reliability_circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    reliability_circuit_recovery_seconds: float = Field(default=30.0, gt=0, le=600)
    reliability_health_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    database_url: str = "sqlite:///./ekaa.db"
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=120)
    database_pool_timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    embedding_enabled: bool = False
    embedding_provider: Literal["deterministic"] = "deterministic"
    embedding_model: str = "deterministic-hash-v1"
    embedding_dimensions: int = Field(default=64, ge=8, le=4_096)
    embedding_batch_size: int = Field(default=32, ge=1, le=256)
    retrieval_default_top_k: int = Field(default=5, ge=1, le=20)
    retrieval_max_top_k: int = Field(default=20, ge=1, le=50)
    hybrid_retrieval_enabled: bool = False
    hybrid_semantic_candidate_count: int = Field(default=12, ge=1, le=50)
    hybrid_lexical_candidate_count: int = Field(default=12, ge=1, le=50)
    hybrid_rrf_rank_constant: int = Field(default=60, ge=1, le=1_000)
    reranking_enabled: bool = False
    reranking_provider: Literal["deterministic_overlap"] = "deterministic_overlap"
    reranking_candidate_count: int = Field(default=12, ge=1, le=50)
    reranking_timeout_seconds: float = Field(default=1.0, gt=0, le=10)
    rag_enabled: bool = False
    rag_retrieval_top_k: int = Field(default=5, ge=1, le=50)
    rag_min_evidence_score: float = Field(default=0.2, ge=-1, le=1)
    rag_min_evidence_sources: int = Field(default=1, ge=1, le=10)
    rag_max_sources: int = Field(default=5, ge=1, le=10)
    rag_max_context_characters: int = Field(default=6_000, ge=100, le=40_000)
    rag_max_context_tokens: int = Field(default=1_500, ge=25, le=10_000)
    citation_max_claims: int = Field(default=10, ge=1, le=30)
    citation_max_claim_characters: int = Field(default=1_000, ge=20, le=4_000)
    citation_max_excerpt_characters: int = Field(default=500, ge=50, le=2_000)
    ingestion_max_file_size_bytes: int = Field(
        default=10 * 1024 * 1024, gt=0, le=50 * 1024 * 1024
    )
    ingestion_max_request_size_bytes: int = Field(
        default=11 * 1024 * 1024, gt=0, le=55 * 1024 * 1024
    )
    ingestion_max_pdf_pages: int = Field(default=200, ge=1, le=1_000)
    ingestion_max_extracted_characters: int = Field(
        default=2_000_000, ge=1_000, le=10_000_000
    )
    ingestion_chunk_size_characters: int = Field(default=1_200, ge=100, le=10_000)
    ingestion_chunk_overlap_characters: int = Field(default=200, ge=0, le=2_000)

    @field_validator("llm_base_url")
    @classmethod
    def require_http_base_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("LLM base URL must use HTTP or HTTPS")
        return normalized

    @model_validator(mode="after")
    def reject_debug_in_production(self) -> Self:
        if self.environment == "production" and self.debug:
            raise ValueError("debug mode cannot be enabled in production")
        if self.environment == "production" and not self.llm_base_url.startswith(
            "https://"
        ):
            raise ValueError("production LLM base URL must use HTTPS")
        if self.llm_enabled and (self.llm_api_key is None or not self.llm_model):
            raise ValueError("enabled LLM requires both an API key and model")
        if (
            self.ingestion_chunk_overlap_characters
            >= self.ingestion_chunk_size_characters
        ):
            raise ValueError("ingestion chunk overlap must be smaller than chunk size")
        if self.ingestion_max_request_size_bytes < self.ingestion_max_file_size_bytes:
            raise ValueError("ingestion request limit must cover the file size limit")
        if not self.embedding_model.strip():
            raise ValueError("embedding model must not be blank")
        if self.retrieval_default_top_k > self.retrieval_max_top_k:
            raise ValueError("retrieval default top K must not exceed the maximum")
        if self.rag_retrieval_top_k > self.retrieval_max_top_k:
            raise ValueError("RAG retrieval top K must not exceed retrieval maximum")
        if self.hybrid_semantic_candidate_count > self.retrieval_max_top_k:
            raise ValueError(
                "hybrid semantic candidate count exceeds retrieval maximum"
            )
        if self.hybrid_lexical_candidate_count > self.retrieval_max_top_k:
            raise ValueError("hybrid lexical candidate count exceeds retrieval maximum")
        if self.reranking_candidate_count > self.retrieval_max_top_k:
            raise ValueError("reranking candidate count exceeds retrieval maximum")
        if self.reranking_candidate_count < self.rag_retrieval_top_k:
            raise ValueError("reranking candidate count must cover RAG retrieval top K")
        if self.rag_min_evidence_sources > self.rag_max_sources:
            raise ValueError("RAG minimum evidence sources must fit the source limit")
        if self.embedding_enabled and not self.database_url.startswith("postgresql"):
            raise ValueError(
                "enabled embeddings require a PostgreSQL pgvector database"
            )
        if self.hybrid_retrieval_enabled and not self.embedding_enabled:
            raise ValueError(
                "enabled hybrid retrieval requires enabled semantic retrieval"
            )
        if self.reranking_enabled and not self.hybrid_retrieval_enabled:
            raise ValueError("enabled reranking requires enabled hybrid retrieval")
        if self.rag_enabled and not self.embedding_enabled:
            raise ValueError("enabled RAG requires enabled semantic retrieval")
        if self.rag_enabled and not self.llm_enabled:
            raise ValueError("enabled RAG requires an enabled LLM provider")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
