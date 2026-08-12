"""Provider-independent, retrieval-derived Stage 4 values."""

from dataclasses import dataclass
from typing import Literal

from backend.app.embeddings.models import RetrievedChunk
from backend.app.llm.models import TokenUsage


@dataclass(frozen=True)
class RagSource:
    """Minimum traceability selected by the server from retrieved evidence."""

    document_id: str
    document_name: str
    chunk_id: str
    chunk_index: int
    page_number: int | None
    section: str | None


@dataclass(frozen=True)
class ContextWindow:
    """The exact bounded evidence sent to a generation provider."""

    text: str
    sources: tuple[RagSource, ...]
    character_count: int
    estimated_token_count: int
    selected_chunks: tuple[RetrievedChunk, ...]


@dataclass(frozen=True)
class Citation:
    """A server-built, claim-level reference into selected retrieved evidence."""

    citation_id: str
    document_id: str
    document_name: str
    chunk_id: str
    chunk_index: int
    excerpt: str
    page_number: int | None
    section: str | None


@dataclass(frozen=True)
class CitationClaim:
    """One provider-proposed claim after deterministic citation validation."""

    text: str
    status: Literal["supported", "unsupported"]
    citation_ids: tuple[str, ...]
    unsupported_reason: str | None


@dataclass(frozen=True)
class RagAnswerResult:
    """A generated answer with server-owned, validated claim citations."""

    answer: str
    model: str
    usage: TokenUsage
    claims: tuple[CitationClaim, ...]
    citations: tuple[Citation, ...]
