"""Batch embedding and semantic retrieval application services."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.embeddings.errors import (
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    EmbeddingStorageError,
)
from backend.app.embeddings.models import (
    EmbeddingBatch,
    EmbeddingInput,
    EmbeddingRunResult,
    RetrievedChunk,
)
from backend.app.embeddings.provider import EmbeddingProvider
from backend.app.embeddings.repository import (
    EmbeddingRepository,
    LexicalRetrievalRepository,
)

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]


def validate_embedding_batch(
    batch: EmbeddingBatch,
    *,
    expected_model: str,
    expected_dimensions: int,
    expected_count: int,
) -> None:
    """Reject malformed or mismatched provider output before any database write."""
    if batch.model != expected_model or batch.dimensions != expected_dimensions:
        raise EmbeddingConfigurationError
    if len(batch.vectors) != expected_count:
        raise EmbeddingProviderError
    if any(len(vector) != expected_dimensions for vector in batch.vectors):
        raise EmbeddingProviderError


class EmbeddingService:
    """Embed pending chunks one atomic batch at a time.

    Successfully committed earlier batches remain useful if a later provider batch
    fails. The failing batch is not written, and the returned status makes retrying
    a controlled maintenance run safe.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        provider: EmbeddingProvider,
        model: str,
        dimensions: int,
        batch_size: int,
        repository: EmbeddingRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size
        self._repository = repository or EmbeddingRepository()
        if provider.model != model or provider.dimensions != dimensions:
            raise EmbeddingConfigurationError

    def embed_pending_chunks(self) -> EmbeddingRunResult:
        embedded_count = 0
        failed_batches = 0
        while True:
            session = self._session_factory()
            try:
                inputs = self._repository.list_pending_chunks(
                    session,
                    model=self._model,
                    dimensions=self._dimensions,
                    limit=self._batch_size,
                )
            except SQLAlchemyError as exc:
                logger.error(
                    "embedding_pending_query_failed error_type=%s", type(exc).__name__
                )
                raise EmbeddingStorageError from exc
            finally:
                session.close()
            if not inputs:
                return EmbeddingRunResult(
                    embedded_chunk_count=embedded_count,
                    failed_batch_count=failed_batches,
                    remaining_chunk_count=0,
                )
            try:
                batch = self._provider.embed([item.content for item in inputs])
                validate_embedding_batch(
                    batch,
                    expected_model=self._model,
                    expected_dimensions=self._dimensions,
                    expected_count=len(inputs),
                )
                inserted_count = self._persist_batch(inputs=inputs, batch=batch)
            except EmbeddingStorageError:
                raise
            except (EmbeddingConfigurationError, EmbeddingProviderError) as exc:
                logger.warning(
                    "embedding_batch_failed batch_size=%s error_type=%s",
                    len(inputs),
                    type(exc).__name__,
                )
                failed_batches += 1
                return self._run_result_after_failure(embedded_count, failed_batches)
            except Exception as exc:
                logger.error(
                    "embedding_provider_unexpected_failure batch_size=%s error_type=%s",
                    len(inputs),
                    type(exc).__name__,
                )
                failed_batches += 1
                return self._run_result_after_failure(embedded_count, failed_batches)
            embedded_count += inserted_count

    def _persist_batch(
        self, *, inputs: Sequence[EmbeddingInput], batch: EmbeddingBatch
    ) -> int:
        session = self._session_factory()
        try:
            with session.begin():
                inserted_count = self._repository.add_embeddings(
                    session,
                    inputs=inputs,
                    vectors=batch.vectors,
                    model=self._model,
                    dimensions=self._dimensions,
                )
                session.flush()
                return inserted_count
        except SQLAlchemyError as exc:
            session.rollback()
            logger.error(
                "embedding_batch_persistence_failed error_type=%s", type(exc).__name__
            )
            raise EmbeddingStorageError from exc
        finally:
            session.close()

    def _run_result_after_failure(
        self, embedded_count: int, failed_batches: int
    ) -> EmbeddingRunResult:
        session = self._session_factory()
        try:
            remaining = self._repository.count_pending_chunks(
                session, model=self._model, dimensions=self._dimensions
            )
        except SQLAlchemyError as exc:
            logger.error(
                "embedding_remaining_query_failed error_type=%s", type(exc).__name__
            )
            raise EmbeddingStorageError from exc
        finally:
            session.close()
        return EmbeddingRunResult(
            embedded_chunk_count=embedded_count,
            failed_batch_count=failed_batches,
            remaining_chunk_count=remaining,
        )


class SemanticRetrievalService:
    """Embed one query and retrieve pgvector-ranked, traceable public chunks."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        provider: EmbeddingProvider,
        model: str,
        dimensions: int,
        max_top_k: int,
        repository: EmbeddingRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._model = model
        self._dimensions = dimensions
        self._max_top_k = max_top_k
        self._repository = repository or EmbeddingRepository()
        if provider.model != model or provider.dimensions != dimensions:
            raise EmbeddingConfigurationError

    def search(
        self,
        *,
        query: str,
        top_k: int,
        allowed_document_ids: Sequence[str] | None = None,
    ) -> list[RetrievedChunk]:
        if top_k < 1 or top_k > self._max_top_k:
            raise ValueError("top_k is outside the configured limit")
        try:
            batch = self._provider.embed([query])
            validate_embedding_batch(
                batch,
                expected_model=self._model,
                expected_dimensions=self._dimensions,
                expected_count=1,
            )
        except (EmbeddingConfigurationError, EmbeddingProviderError):
            logger.warning("retrieval_query_embedding_failed")
            raise
        except Exception as exc:
            logger.error(
                "retrieval_query_embedding_unexpected_failure error_type=%s",
                type(exc).__name__,
            )
            raise EmbeddingProviderError from exc
        session = self._session_factory()
        try:
            results = self._repository.search(
                session,
                query_vector=batch.vectors[0],
                model=self._model,
                dimensions=self._dimensions,
                top_k=top_k,
                allowed_document_ids=allowed_document_ids,
            )
        except SQLAlchemyError as exc:
            logger.error(
                "semantic_retrieval_query_failed error_type=%s", type(exc).__name__
            )
            raise EmbeddingStorageError from exc
        finally:
            session.close()
        # Database ordering uses distance then ID; retaining this stable order at the
        # service boundary also protects callers supplied by a mocked repository.
        if allowed_document_ids is not None:
            permitted = frozenset(allowed_document_ids)
            results = [item for item in results if item.document_id in permitted]
        return sorted(results, key=lambda item: (-item.score, item.chunk_id))


_EXACT_IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]*\d+[A-Za-z0-9_-]*|[A-Za-z]+[-_][A-Za-z0-9_-]+"
)


def extract_exact_identifiers(query: str, *, maximum: int = 5) -> tuple[str, ...]:
    """Return bounded, normalized identifier-like tokens from an untrusted query."""
    identifiers: list[str] = []
    for match in _EXACT_IDENTIFIER_PATTERN.finditer(query):
        token = match.group(0).lower()
        if token not in identifiers:
            identifiers.append(token)
        if len(identifiers) == maximum:
            break
    return tuple(identifiers)


class LexicalRetrievalService:
    """Read-only PostgreSQL full-text retrieval with exact-ID candidate handling."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        max_top_k: int,
        repository: LexicalRetrievalRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._max_top_k = max_top_k
        self._repository = repository or LexicalRetrievalRepository()

    def search(
        self,
        *,
        query: str,
        top_k: int,
        allowed_document_ids: Sequence[str] | None = None,
    ) -> list[RetrievedChunk]:
        results, _ = self.search_with_metadata(
            query=query, top_k=top_k, allowed_document_ids=allowed_document_ids
        )
        return results

    def search_with_metadata(
        self,
        *,
        query: str,
        top_k: int,
        allowed_document_ids: Sequence[str] | None = None,
    ) -> tuple[list[RetrievedChunk], int]:
        if top_k < 1 or top_k > self._max_top_k:
            raise ValueError("top_k is outside the configured limit")
        session = self._session_factory()
        try:
            results, exact_count = self._repository.search(
                session,
                query=query,
                exact_identifiers=extract_exact_identifiers(query),
                top_k=top_k,
                allowed_document_ids=allowed_document_ids,
            )
        except SQLAlchemyError as exc:
            logger.error(
                "lexical_retrieval_query_failed error_type=%s", type(exc).__name__
            )
            raise EmbeddingStorageError from exc
        finally:
            session.close()
        if allowed_document_ids is not None:
            permitted = frozenset(allowed_document_ids)
            results = [item for item in results if item.document_id in permitted]
        return (
            sorted(results, key=lambda item: (-item.score, item.chunk_id)),
            exact_count,
        )
