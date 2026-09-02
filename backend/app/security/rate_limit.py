"""Bounded process-local abuse control seam; production storage is replaceable."""

from __future__ import annotations

import math
import threading
import time

from starlette.types import ASGIApp, Receive, Scope, Send


class InMemoryRateLimiter:
    def __init__(self, *, capacity: int = 60, refill_per_second: float = 1.0) -> None:
        if (
            type(capacity) is not int
            or capacity < 1
            or isinstance(refill_per_second, bool)
            or not isinstance(refill_per_second, (int, float))
            or not math.isfinite(refill_per_second)
            or refill_per_second <= 0
        ):
            raise ValueError("invalid rate limiter configuration")
        self._capacity = float(capacity)
        self._refill = refill_per_second
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, *, cost: float = 1.0) -> bool:
        if (
            not key
            or len(key) > 128
            or isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or not math.isfinite(cost)
            or cost <= 0
            or cost > self._capacity
        ):
            return False
        now = time.monotonic()
        with self._lock:
            tokens, updated = self._buckets.get(key, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - updated) * self._refill)
            if tokens < cost:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - cost, now)
            if len(self._buckets) > 10_000:
                self._buckets.pop(next(iter(self._buckets)))
            return True


class RateLimitMiddleware:
    """Apply a bounded process-local request limit keyed by client network peer."""

    def __init__(self, app: ASGIApp, *, limiter: InMemoryRateLimiter) -> None:
        self.app = app
        self._limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        key = str(client[0]) if client else "anonymous"
        if self._limiter.allow(key):
            await self.app(scope, receive, send)
            return
        payload = b'{"error":{"code":"rate_limited","message":"Too many requests."}}'
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                    (b"retry-after", b"1"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})
