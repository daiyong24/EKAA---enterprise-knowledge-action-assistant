"""Provider-independent values passed between embedding application boundaries."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingBatch:
    """A complete ordered provider response for an ordered input batch."""

    model: str
    dimensions: int
    vectors: tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class EmbeddingInput:
    """Minimum data needed to embed an already-approved normalized chunk."""

    chunk_id: str
    content: str
    content_checksum: str


@dataclass(frozen=True)
class EmbeddingRunResult:
    """Bounded batch-run status; failed batches never persist partial vectors."""

    embedded_chunk_count: int
    failed_batch_count: int
    remaining_chunk_count: int


@dataclass(frozen=True)
class RetrievedChunk:
    """Traceable semantic-retrieval result; source text remains untrusted data."""

    document_id: str
    document_name: str
    chunk_id: str
    chunk_index: int
    content: str
    score: float
    page_number: int | None
    section: str | None
    # RRF is a ranking value, not an evidence-confidence scale.  It remains
    # request-local retrieval metadata so callers can inspect fusion behavior
    # without accidentally using it for the Stage 4 evidence threshold.
    fusion_score: float | None = None


@dataclass(frozen=True)
class HybridQueryDiagnostics:
    """Small, request-local counts for an opt-in retrieval diagnostic response.

    Raw queries, document content, and protected-data decisions must never be put in
    diagnostics.  Stage 9 will supply the authorization filter before either branch
    runs.
    """

    semantic_candidate_count: int
    lexical_candidate_count: int
    merged_candidate_count: int
    exact_identifier_candidate_count: int


@dataclass(frozen=True)
class HybridSearchResult:
    """Fused sources plus safe, bounded branch counts."""

    sources: tuple[RetrievedChunk, ...]
    diagnostics: HybridQueryDiagnostics
