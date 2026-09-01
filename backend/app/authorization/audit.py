"""Auditable, replaceable sink for authorization decisions."""

from collections.abc import Sequence
from typing import Protocol

from backend.app.authorization.models import AuthorizationAuditEvent


class AuditSink(Protocol):
    def record(self, event: AuthorizationAuditEvent) -> None: ...


class InMemoryAuditSink:
    """Bounded local sink used by tests and local development."""

    def __init__(self, *, max_events: int = 10_000) -> None:
        self._max_events = max_events
        self._events: list[AuthorizationAuditEvent] = []

    def record(self, event: AuthorizationAuditEvent) -> None:
        self._events.append(event)
        if len(self._events) > self._max_events:
            del self._events[: len(self._events) - self._max_events]

    def snapshot(self) -> Sequence[AuthorizationAuditEvent]:
        return tuple(self._events)
