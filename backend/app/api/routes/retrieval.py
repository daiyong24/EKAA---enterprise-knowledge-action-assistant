"""HTTP transport for Stage 3 semantic retrieval; it does not generate answers."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.exceptions import ApplicationError
from backend.app.embeddings.errors import (
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    EmbeddingStorageError,
)
from backend.app.embeddings.models import RetrievedChunk
from backend.app.embeddings.service import SemanticRetrievalService

router = APIRouter(tags=["retrieval"])


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2_000)
    top_k: int | None = Field(default=None, ge=1, le=50)


class RetrievalSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    document_name: str
    chunk_id: str
    chunk_index: int = Field(ge=0)
    content: str
    score: float
    page_number: int | None
    section: str | None


class RetrievalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    sources: list[RetrievalSource]


def _to_source(item: RetrievedChunk) -> RetrievalSource:
    return RetrievalSource(**item.__dict__)


@router.post("/retrieval/search", response_model=RetrievalResponse)
async def search(request: Request, body: RetrievalRequest) -> RetrievalResponse:
    """Return pgvector-ranked synthetic/public chunks, never an LLM answer."""
    settings = request.app.state.settings
    if not settings.embedding_enabled:
        raise ApplicationError(
            code="retrieval_not_available",
            message="Semantic retrieval is not available.",
            status_code=503,
        )
    service: SemanticRetrievalService = request.app.state.retrieval_service
    try:
        results = service.search(
            query=body.query,
            top_k=body.top_k or settings.retrieval_default_top_k,
        )
    except ValueError as exc:
        raise ApplicationError(
            code="invalid_retrieval_request",
            message="The retrieval request is invalid.",
            status_code=422,
        ) from exc
    except (
        EmbeddingConfigurationError,
        EmbeddingProviderError,
        EmbeddingStorageError,
    ) as exc:
        raise ApplicationError(
            code="semantic_retrieval_unavailable",
            message="Semantic retrieval is temporarily unavailable.",
            status_code=503,
        ) from exc
    return RetrievalResponse(
        model=settings.embedding_model, sources=[_to_source(item) for item in results]
    )
