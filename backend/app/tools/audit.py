"""Bounded and secret-safe tool audit sink."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from backend.app.tools.models import ToolAuditEvent

_SECRET_KEY = re.compile(
    r"(secret|token|password|api[_-]?key|authorization|cookie)", re.I
)
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s]+")


def redact(value: Any, *, depth: int = 0) -> Any:
    """Return a small structural summary with secret-looking values removed."""
    if depth > 4:
        return "[redacted-depth]"
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]"
            if _SECRET_KEY.search(str(key))
            else redact(item, depth=depth + 1)
            for key, item in list(value.items())[:32]
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item, depth=depth + 1) for item in list(value)[:32]]
    if isinstance(value, str):
        return _BEARER.sub("Bearer [redacted]", value)[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


class ToolAuditSink(Protocol):
    def record(self, event: ToolAuditEvent) -> None: ...


class InMemoryToolAuditSink:
    def __init__(self, *, max_events: int = 10_000) -> None:
        if max_events < 1:
            raise ValueError("max events must be positive")
        self._max_events = max_events
        self._events: list[ToolAuditEvent] = []

    def record(self, event: ToolAuditEvent) -> None:
        self._events.append(event)
        if len(self._events) > self._max_events:
            del self._events[: len(self._events) - self._max_events]

    def snapshot(self) -> Sequence[ToolAuditEvent]:
        return tuple(self._events)
