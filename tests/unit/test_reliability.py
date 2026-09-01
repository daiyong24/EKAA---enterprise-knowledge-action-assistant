import asyncio

import pytest

from backend.app.llm.errors import (
    ChatUnavailableError,
    ProviderNonRetryableError,
    ProviderRateLimitError,
    ProviderRetryableError,
    ProviderTimeoutError,
)
from backend.app.llm.models import ChatPrompt, ChatResult, TokenUsage
from backend.app.llm.prompt_builder import PromptBuilder
from backend.app.llm.service import ChatService
from backend.app.rag.context import ContextBuilder
from backend.app.rag.errors import RagUnavailableError
from backend.app.rag.prompt_builder import RagPromptBuilder
from backend.app.rag.service import RagService
from backend.app.reliability.backoff import BackoffPolicy
from backend.app.reliability.circuit_breaker import CircuitBreaker
from backend.app.reliability.health import DependencyHealthService
from backend.app.reliability.provider import CircuitBreakingProvider


def _result() -> ChatResult:
    return ChatResult(
        answer="safe",
        model="model",
        usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
    )


class SequenceProvider:
    def __init__(self, outcomes: list[ChatResult | Exception]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    async def complete(self, _prompt: ChatPrompt) -> ChatResult:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def test_backoff_is_exponential_and_bounded_with_jitter() -> None:
    policy = BackoffPolicy(
        base_seconds=1, maximum_seconds=5, jitter_ratio=0.2, random_source=lambda: 1.0
    )

    assert policy.delay(0) == pytest.approx(1.2)
    assert policy.delay(1) == pytest.approx(2.4)
    assert policy.delay(4) == 5


@pytest.mark.anyio
async def test_retry_policy_handles_rate_limit_and_does_not_exceed_cap() -> None:
    provider = SequenceProvider([ProviderRateLimitError(retry_after=60), _result()])
    sleep = SleepRecorder()
    service = ChatService(
        provider,
        PromptBuilder(),
        max_retries=1,
        retry_delay_seconds=0.1,
        sleep=sleep,
        backoff_policy=BackoffPolicy(
            base_seconds=0.1, maximum_seconds=2, jitter_ratio=0, random_source=lambda: 0
        ),
    )

    await service.chat("Explain RAG.")

    assert provider.calls == 2
    assert sleep.delays == [2]


@pytest.mark.anyio
async def test_circuit_breaker_opens_and_recovers_after_successful_probe() -> None:
    now = [0.0]
    breaker = CircuitBreaker(
        failure_threshold=2,
        recovery_timeout_seconds=10,
        clock=lambda: now[0],
        failure_predicate=lambda error: isinstance(
            error, (ProviderTimeoutError, ProviderRetryableError)
        ),
    )
    failing = SequenceProvider(
        [ProviderTimeoutError("private"), ProviderRetryableError("private")]
    )
    provider = CircuitBreakingProvider(failing, breaker)

    with pytest.raises(ProviderTimeoutError):
        await provider.complete(ChatPrompt(system="s", user="u"))
    with pytest.raises(ProviderRetryableError):
        await provider.complete(ChatPrompt(system="s", user="u"))
    with pytest.raises(ProviderRetryableError):
        await provider.complete(ChatPrompt(system="s", user="u"))
    assert failing.calls == 2

    now[0] = 11
    recovered = SequenceProvider([_result()])
    recovered_provider = CircuitBreakingProvider(recovered, breaker)
    assert (
        await recovered_provider.complete(ChatPrompt(system="s", user="u")) == _result()
    )
    assert breaker.state.value == "closed"


@pytest.mark.anyio
async def test_non_retryable_error_does_not_trip_circuit() -> None:
    breaker = CircuitBreaker(failure_threshold=1, failure_predicate=lambda _: False)
    provider = CircuitBreakingProvider(
        SequenceProvider([ProviderNonRetryableError("private")]), breaker
    )

    with pytest.raises(ProviderNonRetryableError):
        await provider.complete(ChatPrompt(system="s", user="u"))
    assert breaker.state.value == "closed"


@pytest.mark.anyio
async def test_chat_deadline_safe_degradation_and_cancellation() -> None:
    class SlowProvider:
        calls = 0

        async def complete(self, _prompt: ChatPrompt) -> ChatResult:
            self.calls += 1
            await asyncio.sleep(1)
            return _result()

    provider = SlowProvider()
    service = ChatService(
        provider,
        PromptBuilder(),
        max_retries=2,
        retry_delay_seconds=0,
        deadline_seconds=0.01,
    )
    with pytest.raises(ChatUnavailableError):
        await service.chat("Explain RAG.")
    assert provider.calls == 1


@pytest.mark.anyio
async def test_rag_deadline_bounds_synchronous_retrieval_boundary() -> None:
    class SlowRetrieval:
        def search(self, *, query: str, top_k: int) -> list[object]:
            del query, top_k
            import time

            time.sleep(1)
            return []

    provider = SequenceProvider([_result()])
    service = RagService(
        retrieval_service=SlowRetrieval(),  # type: ignore[arg-type]
        provider=provider,
        prompt_builder=RagPromptBuilder(),
        context_builder=ContextBuilder(
            max_characters=1_000, max_estimated_tokens=300, max_sources=3
        ),
        retrieval_top_k=3,
        min_evidence_score=0.5,
        min_evidence_sources=1,
        max_retries=1,
        retry_delay_seconds=0,
        deadline_seconds=0.01,
    )

    with pytest.raises(RagUnavailableError):
        await service.answer("Question?")
    assert provider.calls == 0


@pytest.mark.anyio
async def test_explicit_cancellation_is_not_retried_or_recorded_as_failure() -> None:
    class SlowProvider:
        calls = 0

        async def complete(self, _prompt: ChatPrompt) -> ChatResult:
            self.calls += 1
            await asyncio.sleep(1)
            return _result()

    provider = SlowProvider()
    service = ChatService(
        provider,
        PromptBuilder(),
        max_retries=2,
        retry_delay_seconds=0,
    )
    task = asyncio.create_task(service.chat("Explain RAG."))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.calls == 1


@pytest.mark.anyio
async def test_dependency_health_is_bounded_and_content_free() -> None:
    async def healthy() -> None:
        return None

    async def failing() -> None:
        raise RuntimeError("secret provider payload")

    async def slow() -> None:
        await asyncio.sleep(1)

    service = DependencyHealthService(
        {"healthy": healthy, "failing": failing, "slow": slow}, timeout_seconds=0.01
    )
    status, dependencies = await service.check()

    assert status == "degraded"
    assert [(item.name, item.status) for item in dependencies] == [
        ("failing", "unavailable"),
        ("healthy", "ok"),
        ("slow", "timeout"),
    ]
