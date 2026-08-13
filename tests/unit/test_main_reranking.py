from backend.app.core.config import Settings
from backend.app.main import create_app


def _settings(*, reranking_enabled: bool) -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+psycopg://test:test@localhost/test",
        embedding_enabled=True,
        hybrid_retrieval_enabled=True,
        reranking_enabled=reranking_enabled,
        _env_file=None,
    )


def test_application_only_wires_rerank_candidate_expansion_when_enabled() -> None:
    disabled = create_app(_settings(reranking_enabled=False)).state.rag_service
    enabled = create_app(_settings(reranking_enabled=True)).state.rag_service

    assert disabled._reranking_service is None  # type: ignore[attr-defined]
    assert disabled._rerank_candidate_count == 5  # type: ignore[attr-defined]
    assert enabled._reranking_service is not None  # type: ignore[attr-defined]
    assert enabled._rerank_candidate_count == 12  # type: ignore[attr-defined]
