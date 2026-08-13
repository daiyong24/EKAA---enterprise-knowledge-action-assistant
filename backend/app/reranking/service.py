"""Safe, bounded reranking orchestration for final RAG context selection."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import replace
from math import isfinite

from backend.app.embeddings.models import RetrievedChunk
from backend.app.reranking.models import RerankBatch, RerankInput
from backend.app.reranking.provider import RerankerProvider

logger = logging.getLogger(__name__)


class RerankingService:
    """Order candidates with a provider, falling back safely to retrieval order.

    Reranking may only reorder candidates that have already passed the existing
    retrieval evidence threshold.  A timeout, provider failure, or malformed
    response returns the original stable retrieval order; it never turns an
    unavailable reranker into a failed knowledge request or a new source.
    """

    def __init__(self, *, provider: RerankerProvider, timeout_seconds: float) -> None:
        self._provider = provider
        self._timeout_seconds = timeout_seconds

    async def rerank(
        self, *, query: str, candidates: Sequence[RetrievedChunk]
    ) -> list[RetrievedChunk]:
        if not candidates:
            return []
        inputs = tuple(
            RerankInput(chunk_id=candidate.chunk_id, content=candidate.content)
            for candidate in candidates
        )
        try:
            batch = await asyncio.wait_for(
                self._provider.rerank(query=query, candidates=inputs),
                timeout=self._timeout_seconds,
            )
            score_by_id = self._validated_scores(batch=batch, inputs=inputs)
        except TimeoutError:
            logger.warning("reranker_fallback reason=timeout")
            return list(candidates)
        except Exception as exc:
            logger.warning(
                "reranker_fallback reason=provider_or_output_error error_type=%s",
                type(exc).__name__,
            )
            return list(candidates)

        original_rank = {
            candidate.chunk_id: rank for rank, candidate in enumerate(candidates)
        }
        return sorted(
            (
                replace(candidate, rerank_score=score_by_id[candidate.chunk_id])
                for candidate in candidates
            ),
            key=lambda candidate: (
                -score_by_id[candidate.chunk_id],
                original_rank[candidate.chunk_id],
                candidate.chunk_id,
            ),
        )

    def _validated_scores(
        self, *, batch: RerankBatch, inputs: Sequence[RerankInput]
    ) -> dict[str, float]:
        if batch.model != self._provider.model:
            raise ValueError("reranker model mismatch")
        expected_ids = [item.chunk_id for item in inputs]
        returned_ids = [item.chunk_id for item in batch.scores]
        if (
            len(set(expected_ids)) != len(expected_ids)
            or len(returned_ids) != len(expected_ids)
            or set(returned_ids) != set(expected_ids)
            or len(set(returned_ids)) != len(returned_ids)
        ):
            raise ValueError("reranker response does not map exactly to candidates")
        if any(
            not isinstance(item.score, (int, float))
            or isinstance(item.score, bool)
            or not isfinite(item.score)
            or item.score < 0
            or item.score > 1
            for item in batch.scores
        ):
            raise ValueError("reranker score is outside the normalized range")
        return {item.chunk_id: item.score for item in batch.scores}
