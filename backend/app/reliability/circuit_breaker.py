"""Small async circuit breaker for provider availability failures."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TypeVar


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """The dependency is failing fast while its circuit is open."""


T = TypeVar("T")


class CircuitBreaker:
    """Trip after consecutive transient failures and probe after a cooldown.

    Only availability failures should be passed to ``record_failure``. Malformed
    model output and permanent request errors are caller bugs/provider contract
    failures, not dependency outages, and must not trip this breaker.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        failure_predicate: Callable[[Exception], bool] | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("circuit failure threshold must be positive")
        if recovery_timeout_seconds <= 0:
            raise ValueError("circuit recovery timeout must be positive")
        self._failure_threshold = failure_threshold
        self._recovery_timeout_seconds = recovery_timeout_seconds
        self._clock = clock
        self._failure_predicate = failure_predicate or (lambda _exc: True)
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    async def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        await self._before_call()
        try:
            result = await operation()
        except asyncio.CancelledError:
            # Cancellation is caller intent, never dependency evidence.
            await self._cancel_probe_if_needed()
            raise
        except Exception as exc:
            if self._failure_predicate(exc):
                await self.record_failure()
            else:
                await self.record_success()
            raise
        else:
            await self.record_success()
            return result

    async def _before_call(self) -> None:
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if self._clock() - self._opened_at < self._recovery_timeout_seconds:
                    raise CircuitOpenError("dependency circuit is open")
                self._state = CircuitState.HALF_OPEN
            if self._state == CircuitState.HALF_OPEN:
                if self._probe_in_flight:
                    raise CircuitOpenError("dependency recovery probe is in flight")
                self._probe_in_flight = True

    async def _cancel_probe_if_needed(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._probe_in_flight = False

    async def record_failure(self) -> None:
        async with self._lock:
            if self._state == CircuitState.OPEN:
                return
            self._probe_in_flight = False
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()
                self._consecutive_failures = self._failure_threshold
                return
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()

    async def record_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.OPEN:
                return
            self._probe_in_flight = False
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
