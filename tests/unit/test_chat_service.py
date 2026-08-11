import logging
from collections.abc import Sequence

import pytest

from backend.app.llm.errors import (
    ChatOutputError,
    ChatProviderRejectedError,
    ChatUnavailableError,
    ProviderNonRetryableError,
    ProviderOutputError,
    ProviderRetryableError,
    ProviderTimeoutError,
)
from backend.app.llm.models import ChatPrompt, ChatResult, TokenUsage
from backend.app.llm.prompt_builder import PromptBuilder
from backend.app.llm.service import ChatService


class SequenceProvider:
    def __init__(self, outcomes: Sequence[ChatResult | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[ChatPrompt] = []

    async def complete(self, prompt: ChatPrompt) -> ChatResult:
        self.calls.append(prompt)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _result() -> ChatResult:
    return ChatResult(
        answer="A safe answer.",
        model="test-model",
        usage=TokenUsage(input_tokens=3, output_tokens=2, total_tokens=5),
    )


def _withheld_result() -> ChatResult:
    return ChatResult(
        answer=(
            "EKAA completed the Stage 1 conceptual request, but it does not "
            "expose ungrounded model content. Grounded answers will be available "
            "after authorized retrieval is implemented."
        ),
        model="test-model",
        usage=TokenUsage(input_tokens=3, output_tokens=2, total_tokens=5),
    )


@pytest.mark.anyio
async def test_success_builds_a_non_retrieval_prompt() -> None:
    provider = SequenceProvider([_result()])
    service = ChatService(
        provider, PromptBuilder(), max_retries=0, retry_delay_seconds=0
    )

    result = await service.chat("Explain Retrieval-Augmented Generation.")

    assert result == _withheld_result()
    assert provider.calls[0].user == "Explain Retrieval-Augmented Generation."
    assert (
        "do not have access to an enterprise knowledge base" in provider.calls[0].system
    )


@pytest.mark.anyio
async def test_enterprise_question_returns_policy_refusal_before_provider() -> None:
    provider = SequenceProvider([_result()])
    service = ChatService(
        provider, PromptBuilder(), max_retries=0, retry_delay_seconds=0
    )

    result = await service.chat("Tell me our incident response policy.")

    assert "cannot answer organization-specific questions" in result.answer
    assert result.model == "ekaa-stage-1-policy"
    assert result.usage == TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)
    assert provider.calls == []


@pytest.mark.anyio
async def test_provider_output_is_never_exposed_before_grounding() -> None:
    provider = SequenceProvider(
        [
            ChatResult(
                answer="The CEO is a fabricated enterprise claim.",
                model="test-model",
                usage=TokenUsage(input_tokens=3, output_tokens=2, total_tokens=5),
            )
        ]
    )
    service = ChatService(
        provider, PromptBuilder(), max_retries=0, retry_delay_seconds=0
    )

    result = await service.chat("Explain RAG.")

    assert result == _withheld_result()
    assert "fabricated enterprise claim" not in result.answer
    assert provider.calls


@pytest.mark.anyio
async def test_timeout_retries_then_succeeds_with_bounded_delays() -> None:
    provider = SequenceProvider(
        [
            ProviderTimeoutError("timeout"),
            ProviderRetryableError("temporary"),
            _result(),
        ]
    )
    sleep = SleepRecorder()
    service = ChatService(
        provider,
        PromptBuilder(),
        max_retries=2,
        retry_delay_seconds=0.25,
        sleep=sleep,
    )

    assert await service.chat("Explain RAG.") == _withheld_result()
    assert len(provider.calls) == 3
    assert sleep.delays == [0.25, 0.5]


@pytest.mark.anyio
async def test_retry_bound_returns_safe_unavailable_error() -> None:
    provider = SequenceProvider(
        [ProviderRetryableError("one"), ProviderRetryableError("two")]
    )
    sleep = SleepRecorder()
    service = ChatService(
        provider,
        PromptBuilder(),
        max_retries=1,
        retry_delay_seconds=0,
        sleep=sleep,
    )

    with pytest.raises(ChatUnavailableError):
        await service.chat("Explain RAG.")

    assert len(provider.calls) == 2
    assert sleep.delays == [0]


@pytest.mark.anyio
async def test_non_retryable_error_is_not_retried() -> None:
    provider = SequenceProvider([ProviderNonRetryableError("invalid"), _result()])
    service = ChatService(
        provider, PromptBuilder(), max_retries=2, retry_delay_seconds=0
    )

    with pytest.raises(ChatProviderRejectedError):
        await service.chat("Explain RAG.")

    assert len(provider.calls) == 1


@pytest.mark.anyio
async def test_invalid_provider_output_is_not_retried() -> None:
    provider = SequenceProvider([ProviderOutputError("malformed response"), _result()])
    service = ChatService(
        provider, PromptBuilder(), max_retries=2, retry_delay_seconds=0
    )

    with pytest.raises(ChatOutputError):
        await service.chat("Explain RAG.")

    assert len(provider.calls) == 1


@pytest.mark.anyio
async def test_retry_logging_does_not_include_provider_error_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "super-secret-provider-detail"
    provider = SequenceProvider([ProviderRetryableError(secret), _result()])
    service = ChatService(
        provider,
        PromptBuilder(),
        max_retries=1,
        retry_delay_seconds=0,
        sleep=SleepRecorder(),
    )

    with caplog.at_level(logging.WARNING):
        await service.chat("Explain RAG.")

    assert secret not in caplog.text
