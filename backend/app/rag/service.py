"""Application orchestration for bounded retrieval-augmented generation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from backend.app.embeddings.models import RetrievedChunk
from backend.app.llm.errors import (
    ProviderConfigurationError,
    ProviderNonRetryableError,
    ProviderOutputError,
    ProviderRateLimitError,
    ProviderRetryableError,
    ProviderTimeoutError,
)
from backend.app.llm.models import ChatPrompt, ChatResult, TokenUsage
from backend.app.llm.provider import LLMProvider
from backend.app.rag.citations import (
    CitationOutputError,
    CitationOutputParser,
    CitationValidator,
)
from backend.app.rag.context import ContextBuilder
from backend.app.rag.errors import (
    RagOutputError,
    RagProviderRejectedError,
    RagUnavailableError,
)
from backend.app.rag.models import Citation, CitationClaim, RagAnswerResult
from backend.app.rag.prompt_builder import RagPromptBuilder
from backend.app.reliability.backoff import BackoffPolicy
from backend.app.reliability.provider import ProviderCircuitOpenError
from backend.app.reranking.service import RerankingService

logger = logging.getLogger(__name__)
Sleep = Callable[[float], Awaitable[None]]
INSUFFICIENT_EVIDENCE_ANSWER = (
    "Insufficient information is available in the knowledge base."
)
INSUFFICIENT_EVIDENCE_MODEL = "ekaa-rag-evidence-policy"


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
        reranking_service: RerankingService | None = None,
        citation_output_parser: CitationOutputParser | None = None,
        citation_validator: CitationValidator | None = None,
        retrieval_top_k: int,
        rerank_candidate_count: int | None = None,
        min_evidence_score: float,
        min_evidence_sources: int,
        max_retries: int,
        retry_delay_seconds: float,
        sleep: Sleep = asyncio.sleep,
        backoff_policy: BackoffPolicy | None = None,
        deadline_seconds: float | None = None,
    ) -> None:
        self._retrieval_service = retrieval_service
        self._provider = provider
        self._prompt_builder = prompt_builder
        self._context_builder = context_builder
        self._reranking_service = reranking_service
        self._citation_output_parser = citation_output_parser or CitationOutputParser(
            max_claims=10, max_claim_characters=1_000
        )
        self._citation_validator = citation_validator or CitationValidator(
            max_excerpt_characters=500
        )
        self._retrieval_top_k = retrieval_top_k
        self._rerank_candidate_count = rerank_candidate_count or retrieval_top_k
        self._min_evidence_score = min_evidence_score
        self._min_evidence_sources = min_evidence_sources
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._sleep = sleep
        self._backoff_policy = backoff_policy
        self._deadline_seconds = deadline_seconds

    async def answer(self, question: str) -> RagAnswerResult:
        if self._deadline_seconds is None:
            return await self._answer(question)
        try:
            async with asyncio.timeout(self._deadline_seconds):
                return await self._answer(question)
        except TimeoutError as exc:
            raise RagUnavailableError("request deadline exceeded") from exc

    async def _answer(self, question: str) -> RagAnswerResult:
        # Retrieval is a synchronous persistence boundary. Running it in a worker
        # lets the request deadline cancel the awaiting request without leaking
        # database/provider details into the public error contract.
        retrieved = await asyncio.to_thread(
            self._retrieval_service.search,
            query=question,
            top_k=self._rerank_candidate_count,
        )
        qualified = self._sufficient_chunks(retrieved)
        if self._reranking_service is not None:
            qualified = await self._reranking_service.rerank(
                query=question, candidates=qualified
            )
        context = self._context_builder.build(qualified[: self._retrieval_top_k])
        if len(context.sources) < self._min_evidence_sources:
            return self._insufficient_evidence_result()
        prompt = self._prompt_builder.build(question=question, context=context)
        result = await self._complete_with_retries(prompt)
        try:
            proposed_claims = self._citation_output_parser.parse(result.answer)
        except CitationOutputError as exc:
            raise RagOutputError(
                "provider returned invalid citation output", usage=result.usage
            ) from exc
        if not proposed_claims:
            return self._insufficient_evidence_result(usage=result.usage)
        claims, citations = self._citation_validator.validate(
            proposed_claims, context=context
        )
        if not all(claim.status == "supported" for claim in claims):
            logger.warning("rag_generation_rejected_unsupported_claim")
            return self._unsupported_claims_result(
                claims=claims, citations=citations, usage=result.usage
            )
        return RagAnswerResult(
            answer="\n".join(claim.text for claim in claims),
            model=result.model,
            usage=result.usage,
            claims=claims,
            citations=citations,
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
            claims=(),
            citations=(),
        )

    @staticmethod
    def _unsupported_claims_result(
        *,
        claims: tuple[CitationClaim, ...],
        citations: tuple[Citation, ...],
        usage: TokenUsage,
    ) -> RagAnswerResult:
        """Do not present a partly unsupported generated answer as factual output."""
        return RagAnswerResult(
            answer=INSUFFICIENT_EVIDENCE_ANSWER,
            model=INSUFFICIENT_EVIDENCE_MODEL,
            usage=usage,
            claims=claims,
            citations=citations,
        )

    async def _complete_with_retries(self, prompt: ChatPrompt) -> ChatResult:
        for attempt in range(self._max_retries + 1):
            try:
                return await self._provider.complete(prompt)
            except ProviderOutputError as exc:
                raise RagOutputError(
                    "provider returned invalid output", usage=exc.usage
                ) from exc
            except ProviderNonRetryableError as exc:
                raise RagProviderRejectedError("provider rejected request") from exc
            except ProviderConfigurationError as exc:
                raise RagUnavailableError("provider is not configured") from exc
            except ProviderCircuitOpenError as exc:
                raise RagUnavailableError("provider circuit is open") from exc
            except (ProviderTimeoutError, ProviderRetryableError) as exc:
                if attempt == self._max_retries:
                    raise RagUnavailableError("provider remained unavailable") from exc
                logger.warning(
                    "rag_generation_retry attempt=%s error_type=%s",
                    attempt + 1,
                    type(exc).__name__,
                )
                await self._sleep(self._retry_delay(exc, attempt))
        raise AssertionError("retry loop must return or raise")

    def _retry_delay(self, error: Exception, attempt: int) -> float:
        if isinstance(error, ProviderRateLimitError) and error.retry_after is not None:
            if self._backoff_policy is not None:
                return min(error.retry_after, self._backoff_policy.maximum_seconds)
            return min(error.retry_after, self._retry_delay_seconds * (attempt + 1))
        if self._backoff_policy is not None:
            return self._backoff_policy.delay(attempt)
        return self._retry_delay_seconds * (attempt + 1)
