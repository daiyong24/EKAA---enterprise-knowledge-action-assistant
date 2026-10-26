from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.db.models import Base, ChunkEmbedding, Document, DocumentChunk
from backend.app.db.session import create_session_factory
from backend.app.embeddings.cli import run_embed_pending
from backend.app.embeddings.errors import (
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    EmbeddingStorageError,
)
from backend.app.embeddings.models import EmbeddingBatch, RetrievedChunk
from backend.app.embeddings.provider import DeterministicHashEmbeddingProvider
from backend.app.embeddings.repository import EmbeddingRepository
from backend.app.embeddings.service import EmbeddingService, SemanticRetrievalService


def _database_with_chunks(count: int) -> tuple[Engine, object]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    with session_factory.begin() as session:
        document = Document(
            filename="synthetic.txt",
            mime_type="text/plain",
            checksum="a" * 64,
            status="completed",
            metadata_json={"format": "txt"},
        )
        document.chunks = [
            DocumentChunk(
                chunk_index=index,
                content=f"synthetic retrieval content {index}",
                checksum=f"{index:064x}",
                page_number=index + 1,
                section="Synthetic",
                metadata_json={"source": "test"},
            )
            for index in range(count)
        ]
        session.add(document)
    return engine, session_factory


class _PlannedProvider:
    def __init__(self, batches: Sequence[EmbeddingBatch | Exception]) -> None:
        self.model = "test-model"
        self.dimensions = 3
        self._batches = list(batches)

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        next_item = self._batches.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


def _batch(
    count: int, *, dimensions: int = 3, model: str = "test-model"
) -> EmbeddingBatch:
    return EmbeddingBatch(
        model=model,
        dimensions=dimensions,
        vectors=tuple((1.0, 0.0, 0.0) for _ in range(count)),
    )


def test_deterministic_provider_is_repeatable_and_normalized() -> None:
    provider = DeterministicHashEmbeddingProvider(
        model="deterministic-hash-v1", dimensions=16
    )

    first = provider.embed(["Network incident runbook", "Network incident runbook"])
    second = provider.embed(["Network incident runbook", "Network incident runbook"])

    assert first == second
    assert first.vectors[0] == first.vectors[1]
    assert sum(value * value for value in first.vectors[0]) == pytest.approx(1.0)


def test_deterministic_hash_v1_preserves_legacy_tokenizer_golden_vector() -> None:
    vector = (
        DeterministicHashEmbeddingProvider(model="deterministic-hash-v1", dimensions=64)
        .embed(["Stage 21 synthetic recovery target 47 minutes"])
        .vectors[0]
    )

    assert [(index, value) for index, value in enumerate(vector) if value] == [
        (58, 1.0)
    ]


def test_deterministic_hash_v2_uses_words_and_matches_stage21_fixture() -> None:
    provider = DeterministicHashEmbeddingProvider(
        model="deterministic-hash-v2", dimensions=64
    )
    query = "What is the synthetic demo recovery target?"
    chunk = (
        "This document contains fictional data created only for the EKAA local demo. "
        "The synthetic demo recovery target is 47 minutes. This value is invented "
        "for this fixture and does not describe a real organization, service, or "
        "operational target."
    )

    query_vector = provider.embed([query]).vectors[0]
    chunk_vector = provider.embed([chunk]).vectors[0]
    similarity = sum(
        left * right for left, right in zip(query_vector, chunk_vector, strict=True)
    )

    assert query_vector == provider.embed([query]).vectors[0]
    assert sum(value * value for value in query_vector) == pytest.approx(1.0)
    assert sum(value * value for value in chunk_vector) == pytest.approx(1.0)
    assert similarity == pytest.approx(0.617, abs=0.001)
    assert similarity > 0.2


def test_deterministic_provider_rejects_unsupported_algorithm_id() -> None:
    with pytest.raises(EmbeddingConfigurationError):
        DeterministicHashEmbeddingProvider(model="unknown", dimensions=64)


def test_embedding_service_commits_complete_batches_and_stops_on_later_failure() -> (
    None
):
    engine, session_factory = _database_with_chunks(3)
    provider = _PlannedProvider([_batch(2), EmbeddingProviderError()])
    service = EmbeddingService(
        session_factory=session_factory,
        provider=provider,
        model="test-model",
        dimensions=3,
        batch_size=2,
    )

    result = service.embed_pending_chunks()

    assert result.embedded_chunk_count == 2
    assert result.failed_batch_count == 1
    assert result.remaining_chunk_count == 1
    with Session(engine) as session:
        assert len(list(session.scalars(select(ChunkEmbedding)))) == 2
    engine.dispose()


def test_embedding_service_rejects_mismatch_without_persistence() -> None:
    engine, session_factory = _database_with_chunks(1)
    service = EmbeddingService(
        session_factory=session_factory,
        provider=_PlannedProvider([_batch(1, dimensions=4)]),
        model="test-model",
        dimensions=3,
        batch_size=1,
    )

    result = service.embed_pending_chunks()

    assert result.embedded_chunk_count == 0
    assert result.failed_batch_count == 1
    assert result.remaining_chunk_count == 1
    with Session(engine) as session:
        assert list(session.scalars(select(ChunkEmbedding))) == []
    engine.dispose()


def test_embedding_batch_storage_failure_rolls_back_the_whole_batch() -> None:
    engine, session_factory = _database_with_chunks(2)
    service = EmbeddingService(
        session_factory=session_factory,
        provider=_PlannedProvider([_batch(2)]),
        model="test-model",
        dimensions=3,
        batch_size=2,
    )

    def fail_before_flush(*_: object) -> None:
        raise SQLAlchemyError("synthetic internal storage detail")

    event.listen(Session, "before_flush", fail_before_flush)
    try:
        with pytest.raises(EmbeddingStorageError):
            service.embed_pending_chunks()
    finally:
        event.remove(Session, "before_flush", fail_before_flush)

    with Session(engine) as session:
        assert list(session.scalars(select(ChunkEmbedding))) == []
    engine.dispose()


def test_embedding_service_rejects_provider_metadata_at_construction() -> None:
    _, session_factory = _database_with_chunks(0)
    provider = DeterministicHashEmbeddingProvider(
        model="deterministic-hash-v2", dimensions=3
    )

    with pytest.raises(EmbeddingConfigurationError):
        EmbeddingService(
            session_factory=session_factory,
            provider=provider,
            model="expected",
            dimensions=3,
            batch_size=1,
        )


def test_maintenance_command_is_config_gated_and_reports_only_counts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Service:
        def embed_pending_chunks(self) -> object:
            from backend.app.embeddings.models import EmbeddingRunResult

            return EmbeddingRunResult(
                embedded_chunk_count=2, failed_batch_count=0, remaining_chunk_count=0
            )

    disabled = Settings(_env_file=None)
    assert run_embed_pending(disabled, Service()) == 2  # type: ignore[arg-type]
    enabled = Settings(
        embedding_enabled=True,
        database_url="postgresql+psycopg://test:test@localhost/test",
        _env_file=None,
    )
    assert run_embed_pending(enabled, Service()) == 0  # type: ignore[arg-type]
    assert "embedded_chunk_count" in capsys.readouterr().out


class _RankingRepository:
    def search(self, _: Session, **__: object) -> list[RetrievedChunk]:
        return [
            RetrievedChunk("doc-b", "b.txt", "chunk-z", 2, "second", 0.4, 3, None),
            RetrievedChunk("doc-a", "a.txt", "chunk-b", 1, "tie b", 0.8, None, "One"),
            RetrievedChunk("doc-a", "a.txt", "chunk-a", 0, "tie a", 0.8, 1, "One"),
        ]


def test_retrieval_orders_scores_deterministically_and_preserves_metadata() -> None:
    _, session_factory = _database_with_chunks(0)
    service = SemanticRetrievalService(
        session_factory=session_factory,
        provider=_PlannedProvider([_batch(1)]),
        model="test-model",
        dimensions=3,
        max_top_k=5,
        repository=_RankingRepository(),  # type: ignore[arg-type]
    )

    results = service.search(query="synthetic query", top_k=3)

    assert [result.chunk_id for result in results] == ["chunk-a", "chunk-b", "chunk-z"]
    assert results[0].document_id == "doc-a"
    assert results[0].page_number == 1
    assert results[0].section == "One"


def test_retrieval_service_defense_in_depth_drops_rows_outside_authorized_ids() -> None:
    _, session_factory = _database_with_chunks(0)
    service = SemanticRetrievalService(
        session_factory=session_factory,
        provider=_PlannedProvider([_batch(1)]),
        model="test-model",
        dimensions=3,
        max_top_k=5,
        repository=_RankingRepository(),  # type: ignore[arg-type]
    )
    results = service.search(
        query="synthetic query", top_k=3, allowed_document_ids=["doc-a"]
    )
    assert results
    assert {result.document_id for result in results} == {"doc-a"}


def test_retrieval_returns_no_results_and_enforces_top_k() -> None:
    class EmptyRepository:
        def search(self, _: Session, **__: object) -> list[RetrievedChunk]:
            return []

    _, session_factory = _database_with_chunks(0)
    service = SemanticRetrievalService(
        session_factory=session_factory,
        provider=_PlannedProvider([_batch(1)]),
        model="test-model",
        dimensions=3,
        max_top_k=2,
        repository=EmptyRepository(),  # type: ignore[arg-type]
    )

    assert service.search(query="missing", top_k=1) == []
    with pytest.raises(ValueError):
        service.search(query="too many", top_k=3)


def test_pgvector_query_is_model_scoped_and_permission_filter_ready() -> None:
    statement = EmbeddingRepository().search_statement(
        query_vector=[1.0, 0.0, 0.0],
        model="test-model",
        dimensions=3,
        top_k=4,
        allowed_document_ids=["authorized-document"],
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "chunk_embeddings.embedding <=>" in sql
    assert "chunk_embeddings.embedding_model" in sql
    assert "chunk_embeddings.embedding_dimensions" in sql
    assert "documents.id IN" in sql
