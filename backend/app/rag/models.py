"""Provider-independent, retrieval-derived Stage 4 values."""

from dataclasses import dataclass

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


@dataclass(frozen=True)
class RagAnswerResult:
    """A generated answer accompanied only by server-derived sources."""

    answer: str
    model: str
    usage: TokenUsage
    sources: tuple[RagSource, ...]
