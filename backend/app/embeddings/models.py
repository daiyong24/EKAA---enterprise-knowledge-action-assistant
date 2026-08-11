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
