"""Replaceable reranker protocol and deterministic local implementation."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from backend.app.reranking.models import RerankBatch, RerankInput, RerankScore

_TERM_PATTERN = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


class RerankerProvider(Protocol):
    """Rerank already retrieved candidates without choosing new sources."""

    model: str

    async def rerank(
        self, *, query: str, candidates: Sequence[RerankInput]
    ) -> RerankBatch: ...


class DeterministicOverlapReranker:
    """Local test/demo reranker using normalized query-token coverage.

    This provider sends no data to a third party.  Its score maps exactly to
    ``|unique_query_terms intersect candidate_terms| / |unique_query_terms|``.
    It is intentionally a deterministic baseline, not a production relevance
    model or a semantic-entailment claim.
    """

    model = "deterministic-overlap-reranker-v1"

    async def rerank(
        self, *, query: str, candidates: Sequence[RerankInput]
    ) -> RerankBatch:
        query_terms = set(_terms(query))
        denominator = len(query_terms)
        scores = tuple(
            RerankScore(
                chunk_id=candidate.chunk_id,
                score=(
                    len(query_terms.intersection(_terms(candidate.content)))
                    / denominator
                    if denominator
                    else 0.0
                ),
            )
            for candidate in candidates
        )
        return RerankBatch(model=self.model, scores=scores)


def _terms(value: str) -> tuple[str, ...]:
    return tuple(_TERM_PATTERN.findall(value.lower()))
