"""HTTP transport for Stage 4 evidence-bounded answers."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.exceptions import ApplicationError
from backend.app.embeddings.errors import (
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    EmbeddingStorageError,
)
from backend.app.rag.errors import (
    RagOutputError,
    RagProviderRejectedError,
    RagUnavailableError,
)
from backend.app.rag.models import Citation, CitationClaim, RagAnswerResult
from backend.app.rag.service import RagService

router = APIRouter(tags=["rag"])


class RagRequest(BaseModel):
    """One bounded, stateless, untrusted knowledge question."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=2_000)


class RagUsageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class CitationResponse(BaseModel):
    """A server-built reference into a source selected for this exact request."""

    model_config = ConfigDict(frozen=True)

    citation_id: str
    document_id: str
    document_name: str
    chunk_id: str
    chunk_index: int = Field(ge=0)
    excerpt: str = Field(min_length=1)
    page_number: int | None
    section: str | None


class CitationClaimResponse(BaseModel):
    """UI-ready factual claim status; unsupported text is never an answer."""

    model_config = ConfigDict(frozen=True)

    text: str
    status: Literal["supported", "unsupported"]
    citation_ids: list[str]
    unsupported_reason: str | None


class RagResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    answer: str
    model: str
    usage: RagUsageResponse
    claims: list[CitationClaimResponse]
    citations: list[CitationResponse]


def _to_citation_response(citation: Citation) -> CitationResponse:
    return CitationResponse(**citation.__dict__)


def _to_claim_response(claim: CitationClaim) -> CitationClaimResponse:
    return CitationClaimResponse(**claim.__dict__)


def _to_response(result: RagAnswerResult) -> RagResponse:
    return RagResponse(
        answer=result.answer,
        model=result.model,
        usage=RagUsageResponse(**result.usage.model_dump()),
        claims=[_to_claim_response(claim) for claim in result.claims],
        citations=[_to_citation_response(citation) for citation in result.citations],
    )


@router.post("/rag/answer", response_model=RagResponse)
async def answer(request: Request, body: RagRequest) -> RagResponse:
    """Generate only from bounded retrieval context and server-owned sources."""
    settings = request.app.state.settings
    if not settings.rag_enabled:
        raise ApplicationError(
            code="rag_not_available",
            message="Evidence-bounded answers are not available.",
            status_code=503,
        )
    service: RagService = request.app.state.rag_service
    try:
        result = await service.answer(body.question)
    except (
        EmbeddingConfigurationError,
        EmbeddingProviderError,
        EmbeddingStorageError,
    ) as exc:
        raise ApplicationError(
            code="rag_retrieval_unavailable",
            message="Evidence retrieval is temporarily unavailable.",
            status_code=503,
        ) from exc
    except RagUnavailableError as exc:
        raise ApplicationError(
            code="rag_generation_unavailable",
            message="Evidence-bounded answer generation is temporarily unavailable.",
            status_code=503,
        ) from exc
    except RagProviderRejectedError as exc:
        raise ApplicationError(
            code="rag_generation_rejected",
            message="The language model could not process this request.",
            status_code=502,
        ) from exc
    except RagOutputError as exc:
        raise ApplicationError(
            code="rag_invalid_generation",
            message="The language model returned an invalid response.",
            status_code=502,
        ) from exc
    return _to_response(result)
