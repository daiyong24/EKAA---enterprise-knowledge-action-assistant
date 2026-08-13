"""Provider-independent reranking values.

The values intentionally contain only the query and already retrieved public/demo
chunk data required for final context selection.  They are not public API models.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RerankInput:
    """One bounded candidate supplied to a reranker."""

    chunk_id: str
    content: str


@dataclass(frozen=True)
class RerankScore:
    """One provider-proposed normalized ordering score."""

    chunk_id: str
    score: float


@dataclass(frozen=True)
class RerankBatch:
    """Complete response for one ordered candidate set."""

    model: str
    scores: tuple[RerankScore, ...]
