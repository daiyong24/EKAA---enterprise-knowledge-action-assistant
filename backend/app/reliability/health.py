"""Bounded, content-free dependency health checks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from time import monotonic

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DependencyHealth:
    name: str
    status: str
    latency_ms: float | None = None


HealthCheck = Callable[[], Awaitable[None]]


class DependencyHealthService:
    """Run independent checks with one shared timeout and safe summaries."""

    def __init__(
        self, checks: Mapping[str, HealthCheck], *, timeout_seconds: float = 2.0
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("health timeout must be positive")
        self._checks = dict(checks)
        self._timeout_seconds = timeout_seconds

    async def check(self) -> tuple[str, tuple[DependencyHealth, ...]]:
        results = await asyncio.gather(
            *(self._run_one(name, check) for name, check in self._checks.items())
        )
        ordered = tuple(sorted(results, key=lambda item: item.name))
        overall = "ok" if all(item.status == "ok" for item in ordered) else "degraded"
        return overall, ordered

    async def _run_one(self, name: str, check: HealthCheck) -> DependencyHealth:
        started = monotonic()
        try:
            await asyncio.wait_for(check(), timeout=self._timeout_seconds)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning("dependency_health_timeout dependency=%s", name)
            return DependencyHealth(name=name, status="timeout")
        except Exception as exc:
            logger.warning(
                "dependency_health_failed dependency=%s error_type=%s",
                name,
                type(exc).__name__,
            )
            return DependencyHealth(name=name, status="unavailable")
        return DependencyHealth(
            name=name, status="ok", latency_ms=round((monotonic() - started) * 1000, 3)
        )
