"""Content-minimized approval audit records."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class ApprovalAuditEvent:
    request_id: str
    actor: str
    correlation_id: str
    event: Literal[
        "created",
        "approved",
        "rejected",
        "denied",
        "expired",
        "executing",
        "executed",
        "failed",
        "unknown",
    ]
    action_hash: str
    parameter_hash: str
    timestamp: datetime
    reason_code: str | None = None

    @classmethod
    def now(cls, **kwargs: object) -> ApprovalAuditEvent:
        return cls(timestamp=datetime.now(UTC), **kwargs)  # type: ignore[arg-type]


class ApprovalAuditSink(Protocol):
    def record(self, event: ApprovalAuditEvent) -> None: ...


class InMemoryApprovalAuditSink:
    def __init__(self, *, max_events: int = 10_000) -> None:
        self._max_events = max_events
        self._events: list[ApprovalAuditEvent] = []

    def record(self, event: ApprovalAuditEvent) -> None:
        self._events.append(event)
        if len(self._events) > self._max_events:
            del self._events[: len(self._events) - self._max_events]

    def snapshot(self) -> Sequence[ApprovalAuditEvent]:
        return tuple(self._events)
