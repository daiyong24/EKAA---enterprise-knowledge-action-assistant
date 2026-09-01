"""Provider decorators for circuit breaking at the replaceable boundary."""

from __future__ import annotations

from backend.app.llm.errors import (
    ProviderRetryableError,
    ProviderTimeoutError,
)
from backend.app.llm.models import ChatPrompt, ChatResult
from backend.app.llm.provider import LLMProvider
from backend.app.reliability.circuit_breaker import CircuitBreaker, CircuitOpenError


class ProviderCircuitOpenError(ProviderRetryableError):
    """A provider request was rejected locally by an open circuit."""


class CircuitBreakingProvider:
    """Keep outage protection outside vendor-specific provider adapters."""

    def __init__(self, provider: LLMProvider, breaker: CircuitBreaker) -> None:
        self._provider = provider
        self._breaker = breaker

    async def complete(self, prompt: ChatPrompt) -> ChatResult:
        try:
            return await self._breaker.call(lambda: self._provider.complete(prompt))
        except CircuitOpenError as exc:
            raise ProviderCircuitOpenError("provider circuit is open") from exc


def is_provider_availability_failure(error: Exception) -> bool:
    return isinstance(error, (ProviderTimeoutError, ProviderRetryableError))
