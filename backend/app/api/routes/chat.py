from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.exceptions import ApplicationError
from backend.app.llm.errors import (
    ChatOutputError,
    ChatProviderRejectedError,
    ChatUnavailableError,
)
from backend.app.llm.models import ChatResult, TokenUsage
from backend.app.llm.service import ChatService

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    """Stage 1 accepts one stateless, untrusted user message."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=8_000)


class ChatUsageResponse(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class ChatResponse(BaseModel):
    answer: str
    model: str
    usage: ChatUsageResponse


def _to_response(result: ChatResult) -> ChatResponse:
    usage: TokenUsage = result.usage
    return ChatResponse(
        answer=result.answer,
        model=result.model,
        usage=ChatUsageResponse(**usage.model_dump()),
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, payload: ChatRequest) -> ChatResponse:
    """Produce a stateless non-retrieval response through the LLM boundary."""
    service: ChatService = request.app.state.chat_service
    try:
        result = await service.chat(payload.message)
    except ChatUnavailableError as exc:
        raise ApplicationError(
            code="llm_unavailable",
            message="The language model is currently unavailable.",
            status_code=503,
        ) from exc
    except ChatProviderRejectedError as exc:
        raise ApplicationError(
            code="llm_request_rejected",
            message="The language model could not process this request.",
            status_code=502,
        ) from exc
    except ChatOutputError as exc:
        raise ApplicationError(
            code="llm_invalid_response",
            message="The language model returned an invalid response.",
            status_code=502,
        ) from exc
    return _to_response(result)
