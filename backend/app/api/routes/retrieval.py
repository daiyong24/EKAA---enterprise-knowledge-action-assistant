"""HTTP transport for read-only Stage 6 hybrid retrieval; it does not generate."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.app.authorization.errors import (
    AuthorizationUnavailableError,
    IdentityError,
    InvalidIdentityError,
)
from backend.app.authorization.http import principal_context
from backend.app.authorization.service import AuthorizedRetrievalService
from backend.app.core.exceptions import ApplicationError
from backend.app.embeddings.errors import (
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    EmbeddingStorageError,
)
from backend.app.embeddings.hybrid import HybridRetrievalService
from backend.app.embeddings.models import HybridQueryDiagnostics, RetrievedChunk
from backend.app.embeddings.service import SemanticRetrievalService

router = APIRouter(tags=["retrieval"])


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2_000)
    top_k: int | None = Field(default=None, ge=1, le=50)
    include_diagnostics: bool = False


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


class RetrievalDiagnostics(BaseModel):
    """Counts only: never echo query text, document content, or policy decisions."""

    model_config = ConfigDict(frozen=True)

    semantic_candidate_count: int = Field(ge=0, le=50)
    lexical_candidate_count: int = Field(ge=0, le=50)
    merged_candidate_count: int = Field(ge=0, le=100)
    exact_identifier_candidate_count: int = Field(ge=0, le=50)


class RetrievalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    sources: list[RetrievalSource]
    diagnostics: RetrievalDiagnostics | None = None


def _to_source(item: RetrievedChunk) -> RetrievalSource:
    return RetrievalSource(**item.__dict__)


def _to_diagnostics(item: HybridQueryDiagnostics) -> RetrievalDiagnostics:
    return RetrievalDiagnostics(**item.__dict__)


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
    service: SemanticRetrievalService | HybridRetrievalService = (
        request.app.state.retrieval_service
    )
    try:
        top_k = body.top_k or settings.retrieval_default_top_k
        if body.include_diagnostics and (
            isinstance(service, HybridRetrievalService)
            or (
                isinstance(service, AuthorizedRetrievalService)
                and service.supports_diagnostics
            )
        ):
            with principal_context(request):
                hybrid_result = service.search_with_diagnostics(
                    query=body.query, top_k=top_k
                )
            results = list(hybrid_result.sources)
            diagnostics = _to_diagnostics(hybrid_result.diagnostics)
        else:
            with principal_context(request):
                results = service.search(query=body.query, top_k=top_k)
            diagnostics = None
    except ValueError as exc:
        raise ApplicationError(
            code="invalid_retrieval_request",
            message="The retrieval request is invalid.",
            status_code=422,
        ) from exc
    except InvalidIdentityError as exc:
        raise ApplicationError(
            code="invalid_identity",
            message="Identity verification failed.",
            status_code=401,
        ) from exc
    except IdentityError as exc:
        raise ApplicationError(
            code="identity_unavailable",
            message="Identity verification is temporarily unavailable.",
            status_code=503,
        ) from exc
    except AuthorizationUnavailableError as exc:
        raise ApplicationError(
            code="authorization_unavailable",
            message="Authorization is temporarily unavailable.",
            status_code=503,
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
        model=settings.embedding_model,
        sources=[_to_source(item) for item in results],
        diagnostics=diagnostics,
    )
