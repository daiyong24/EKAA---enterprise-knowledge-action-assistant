"""Application orchestration for bounded retrieval-augmented generation."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Protocol

from backend.app.embeddings.models import RetrievedChunk
from backend.app.llm.errors import (
    ProviderConfigurationError,
    ProviderNonRetryableError,
    ProviderOutputError,
    ProviderRetryableError,
    ProviderTimeoutError,
)
from backend.app.llm.models import ChatPrompt, ChatResult, TokenUsage
from backend.app.llm.provider import LLMProvider
from backend.app.rag.context import ContextBuilder
from backend.app.rag.errors import (
    RagOutputError,
    RagProviderRejectedError,
    RagUnavailableError,
)
from backend.app.rag.grounding import GroundedOutputGuard, LexicalGroundedOutputGuard
from backend.app.rag.models import RagAnswerResult
from backend.app.rag.prompt_builder import RagPromptBuilder

logger = logging.getLogger(__name__)
Sleep = Callable[[float], Awaitable[None]]
INSUFFICIENT_EVIDENCE_ANSWER = (
    "Insufficient information is available in the knowledge base."
)
INSUFFICIENT_EVIDENCE_MODEL = "ekaa-rag-evidence-policy"
_MODEL_SOURCE_LABEL = re.compile(
    r"\[(?:source|citation)\s*:[^\]]+\]|(?:^|\n)\s*(?:source|citation)\s*:\s*\S+",
    flags=re.IGNORECASE,
)


class RetrievalService(Protocol):
    """Read-only semantic retrieval seam used by the RAG orchestration layer."""

    def search(self, *, query: str, top_k: int) -> list[RetrievedChunk]: ...


class RagService:
    """Retrieve evidence first, then generate only with the selected context."""

    def __init__(
        self,
        *,
        retrieval_service: RetrievalService,
        provider: LLMProvider,
        prompt_builder: RagPromptBuilder,
        context_builder: ContextBuilder,
        grounded_output_guard: GroundedOutputGuard | None = None,
        retrieval_top_k: int,
        min_evidence_score: float,
        min_evidence_sources: int,
        max_retries: int,
        retry_delay_seconds: float,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._retrieval_service = retrieval_service
        self._provider = provider
        self._prompt_builder = prompt_builder
        self._context_builder = context_builder
        self._grounded_output_guard = (
            grounded_output_guard or LexicalGroundedOutputGuard()
        )
        self._retrieval_top_k = retrieval_top_k
        self._min_evidence_score = min_evidence_score
        self._min_evidence_sources = min_evidence_sources
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._sleep = sleep

    async def answer(self, question: str) -> RagAnswerResult:
        retrieved = self._retrieval_service.search(
            query=question, top_k=self._retrieval_top_k
        )
        context = self._context_builder.build(self._sufficient_chunks(retrieved))
        if len(context.sources) < self._min_evidence_sources:
            return self._insufficient_evidence_result()
        prompt = self._prompt_builder.build(question=question, context=context)
        result = await self._complete_with_retries(prompt)
        self._reject_model_owned_source_labels(result.answer)
        if not self._grounded_output_guard.is_supported(
            answer=result.answer, context=context.text
        ):
            logger.warning("rag_generation_rejected_unsupported_output")
            return self._insufficient_evidence_result(usage=result.usage)
        return RagAnswerResult(
            answer=result.answer,
            model=result.model,
            usage=result.usage,
            sources=context.sources,
        )

    def _sufficient_chunks(self, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        return [chunk for chunk in chunks if chunk.score >= self._min_evidence_score]

    @staticmethod
    def _insufficient_evidence_result(
        usage: TokenUsage | None = None,
    ) -> RagAnswerResult:
        return RagAnswerResult(
            answer=INSUFFICIENT_EVIDENCE_ANSWER,
            model=INSUFFICIENT_EVIDENCE_MODEL,
            usage=usage or TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0),
            sources=(),
        )

    @staticmethod
    def _reject_model_owned_source_labels(answer: str) -> None:
        """Keep source references server-owned until Stage 5 validation exists."""
        if _MODEL_SOURCE_LABEL.search(answer):
            logger.warning("rag_generation_rejected_model_source_label")
            raise RagOutputError("provider emitted a model-owned source label")

    async def _complete_with_retries(self, prompt: ChatPrompt) -> ChatResult:
        for attempt in range(self._max_retries + 1):
            try:
                return await self._provider.complete(prompt)
            except ProviderOutputError as exc:
                raise RagOutputError("provider returned invalid output") from exc
            except ProviderNonRetryableError as exc:
                raise RagProviderRejectedError("provider rejected request") from exc
            except ProviderConfigurationError as exc:
                raise RagUnavailableError("provider is not configured") from exc
            except (ProviderTimeoutError, ProviderRetryableError) as exc:
                if attempt == self._max_retries:
                    raise RagUnavailableError("provider remained unavailable") from exc
                logger.warning(
                    "rag_generation_retry attempt=%s error_type=%s",
                    attempt + 1,
                    type(exc).__name__,
                )
                await self._sleep(self._retry_delay_seconds * (attempt + 1))
        raise AssertionError("retry loop must return or raise")
