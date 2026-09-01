"""Bounded retry delays with optional deterministic randomness for tests."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

RandomSource = Callable[[], float]


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """Exponential backoff with a bounded multiplicative jitter window.

    ``attempt`` is zero based and represents the delay before the next attempt.
    Jitter is symmetric, so the effective delay is bounded by ``[1-j, 1+j]``
    times the exponential delay and never exceeds ``maximum_seconds``.
    """

    base_seconds: float = 0.25
    maximum_seconds: float = 5.0
    jitter_ratio: float = 0.2
    random_source: RandomSource = random.random

    def __post_init__(self) -> None:
        if self.base_seconds < 0:
            raise ValueError("backoff base must not be negative")
        if self.maximum_seconds < self.base_seconds:
            raise ValueError("backoff maximum must cover the base")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("backoff jitter must be between zero and one")

    def delay(self, attempt: int) -> float:
        if attempt < 0:
            raise ValueError("backoff attempt must not be negative")
        exponential = self.base_seconds * (2.0**attempt)
        exponential = min(self.maximum_seconds, exponential)
        if exponential == 0 or self.jitter_ratio == 0:
            return exponential
        # random_source is injected so fault-injection tests can assert exact bounds.
        random_value = min(1.0, max(0.0, float(self.random_source())))
        factor = 1 - self.jitter_ratio + (2 * self.jitter_ratio * random_value)
        return min(self.maximum_seconds, exponential * factor)
