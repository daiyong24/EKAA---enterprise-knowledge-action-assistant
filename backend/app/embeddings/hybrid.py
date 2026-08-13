"""Deterministic Stage 6 semantic and lexical candidate fusion."""

from __future__ import annotations

from collections.abc import Sequence

from backend.app.embeddings.models import (
    HybridQueryDiagnostics,
    HybridSearchResult,
    RetrievedChunk,
)
from backend.app.embeddings.service import (
    LexicalRetrievalService,
    SemanticRetrievalService,
)


class HybridRetrievalService:
    """Fuse independent retrieval candidates without adding a reranker.

    Reciprocal rank fusion (RRF) is deliberately rank-based so semantic cosine
    similarity and PostgreSQL full-text rank do not need to share a score scale.
    For each candidate ``c`` the score is::

        sum(1 / (rrf_rank_constant + rank_branch(c)))

    where ranks start at one.  A chunk returned by both branches receives both
    contributions. Equal fused scores are ordered by chunk ID, making the public
    ordering deterministic across calls and mocks. RRF is deliberately never used
    as the RAG evidence-confidence score: its magnitude depends on the configured
    rank constant and would make Stage 4's existing threshold meaningless.
    """

    def __init__(
        self,
        *,
        semantic_service: SemanticRetrievalService,
        lexical_service: LexicalRetrievalService,
        semantic_candidate_count: int,
        lexical_candidate_count: int,
        rrf_rank_constant: int,
        max_top_k: int,
    ) -> None:
        self._semantic_service = semantic_service
        self._lexical_service = lexical_service
        self._semantic_candidate_count = semantic_candidate_count
        self._lexical_candidate_count = lexical_candidate_count
        self._rrf_rank_constant = rrf_rank_constant
        self._max_top_k = max_top_k

    def search(
        self,
        *,
        query: str,
        top_k: int,
        allowed_document_ids: Sequence[str] | None = None,
    ) -> list[RetrievedChunk]:
        return list(
            self.search_with_diagnostics(
                query=query,
                top_k=top_k,
                allowed_document_ids=allowed_document_ids,
            ).sources
        )

    def search_with_diagnostics(
        self,
        *,
        query: str,
        top_k: int,
        allowed_document_ids: Sequence[str] | None = None,
    ) -> HybridSearchResult:
        if top_k < 1 or top_k > self._max_top_k:
            raise ValueError("top_k is outside the configured limit")
        semantic = self._semantic_service.search(
            query=query,
            top_k=self._semantic_candidate_count,
            allowed_document_ids=allowed_document_ids,
        )
        lexical, exact_identifier_count = self._lexical_service.search_with_metadata(
            query=query,
            top_k=self._lexical_candidate_count,
            allowed_document_ids=allowed_document_ids,
        )
        fused = self._fuse(semantic=semantic, lexical=lexical)
        return HybridSearchResult(
            sources=tuple(fused[:top_k]),
            diagnostics=HybridQueryDiagnostics(
                semantic_candidate_count=len(semantic),
                lexical_candidate_count=len(lexical),
                merged_candidate_count=len(fused),
                exact_identifier_candidate_count=exact_identifier_count,
            ),
        )

    def _fuse(
        self,
        *,
        semantic: Sequence[RetrievedChunk],
        lexical: Sequence[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """Merge duplicate IDs while retaining evidence confidence for RAG.

        ``score`` remains the strongest branch confidence for the chunk (semantic
        cosine score or lexical candidate score). This keeps the established RAG
        threshold meaningful. ``fusion_score`` is the RRF ordering value only.
        The two scales are intentionally not compared or added.
        """
        fusion_scores: dict[str, float] = {}
        evidence_scores: dict[str, float] = {}
        sources: dict[str, RetrievedChunk] = {}
        for branch in (semantic, lexical):
            for rank, item in enumerate(branch, start=1):
                sources.setdefault(item.chunk_id, item)
                evidence_scores[item.chunk_id] = max(
                    evidence_scores.get(item.chunk_id, item.score), item.score
                )
                fusion_scores[item.chunk_id] = fusion_scores.get(
                    item.chunk_id, 0.0
                ) + 1.0 / (self._rrf_rank_constant + rank)
        return [
            RetrievedChunk(
                document_id=item.document_id,
                document_name=item.document_name,
                chunk_id=item.chunk_id,
                chunk_index=item.chunk_index,
                content=item.content,
                score=evidence_scores[item.chunk_id],
                page_number=item.page_number,
                section=item.section,
                fusion_score=fusion_scores[item.chunk_id],
            )
            for item in sorted(
                sources.values(),
                key=lambda item: (-fusion_scores[item.chunk_id], item.chunk_id),
            )
        ]
