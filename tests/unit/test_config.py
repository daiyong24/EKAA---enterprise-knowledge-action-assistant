import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings


def test_settings_have_safe_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.debug is False
    assert settings.api_prefix == ""


def test_api_prefix_rejects_trailing_slash() -> None:
    with pytest.raises(ValidationError):
        Settings(api_prefix="/api/", _env_file=None)


def test_production_rejects_debug_mode() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", debug=True, _env_file=None)


def test_enabled_llm_requires_explicit_model_and_key() -> None:
    with pytest.raises(ValidationError):
        Settings(llm_enabled=True, _env_file=None)


def test_production_rejects_insecure_llm_url() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            llm_base_url="http://localhost:1234",
            _env_file=None,
        )


def test_request_limit_must_cover_file_limit() -> None:
    with pytest.raises(ValidationError):
        Settings(
            ingestion_max_file_size_bytes=2_000,
            ingestion_max_request_size_bytes=1_999,
            _env_file=None,
        )


def test_enabled_embeddings_require_postgresql_pgvector_path() -> None:
    with pytest.raises(ValidationError):
        Settings(
            embedding_enabled=True, database_url="sqlite:///./ekaa.db", _env_file=None
        )


def test_enabled_rag_requires_enabled_retrieval_and_llm() -> None:
    with pytest.raises(ValidationError):
        Settings(rag_enabled=True, _env_file=None)

    settings = Settings(
        rag_enabled=True,
        embedding_enabled=True,
        database_url="postgresql+psycopg://test:test@localhost/test",
        llm_enabled=True,
        llm_api_key="not-a-real-secret",
        llm_model="test-model",
        _env_file=None,
    )

    assert settings.rag_enabled is True


def test_rag_bounds_must_fit_retrieval_and_context_limits() -> None:
    with pytest.raises(ValidationError):
        Settings(rag_retrieval_top_k=21, retrieval_max_top_k=20, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(rag_min_evidence_sources=2, rag_max_sources=1, _env_file=None)


def test_citation_limits_have_safe_bounded_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.citation_max_claims == 10
    assert settings.citation_max_excerpt_characters == 500

    with pytest.raises(ValidationError):
        Settings(citation_max_claim_characters=19, _env_file=None)


def test_hybrid_retrieval_requires_semantic_retrieval_and_bounded_candidates() -> None:
    with pytest.raises(ValidationError):
        Settings(hybrid_retrieval_enabled=True, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(
            hybrid_semantic_candidate_count=21,
            retrieval_max_top_k=20,
            _env_file=None,
        )

    settings = Settings(
        hybrid_retrieval_enabled=True,
        embedding_enabled=True,
        database_url="postgresql+psycopg://test:test@localhost/test",
        _env_file=None,
    )
    assert settings.hybrid_rrf_rank_constant == 60


def test_reranking_requires_hybrid_retrieval_and_bounded_candidate_context() -> None:
    with pytest.raises(ValidationError):
        Settings(reranking_enabled=True, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(
            reranking_candidate_count=4,
            rag_retrieval_top_k=5,
            _env_file=None,
        )
    with pytest.raises(ValidationError):
        Settings(
            reranking_candidate_count=21,
            retrieval_max_top_k=20,
            _env_file=None,
        )

    settings = Settings(
        reranking_enabled=True,
        hybrid_retrieval_enabled=True,
        embedding_enabled=True,
        database_url="postgresql+psycopg://test:test@localhost/test",
        _env_file=None,
    )
    assert settings.reranking_timeout_seconds == 1.0
