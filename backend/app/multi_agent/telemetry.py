"""Replaceable, content-minimized telemetry boundary for the candidate route."""

from __future__ import annotations

import math
import re
import threading
from contextlib import suppress
from enum import StrEnum
from queue import Empty, Full, Queue
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")


class TelemetryEventName(StrEnum):
    ROUTE_SELECTED = "route_selected"
    WORKER_STARTED = "worker_started"
    WORKER_COMPLETED = "worker_completed"
    HANDOFF_CREATED = "handoff_created"
    FAILURE = "failure"
    CONFLICT = "conflict"
    FALLBACK = "fallback"


class TelemetryOutcome(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    FALLBACK = "fallback"


class RouteTelemetryEvent(BaseModel):
    """Safe metric/span-shaped event; no prompts, claims, or provider payloads."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event: TelemetryEventName
    outcome: TelemetryOutcome
    task_id: str = Field(min_length=1, max_length=128)
    correlation_id: str = Field(min_length=1, max_length=128)
    worker_id: str | None = Field(default=None, max_length=128)
    duration_ms: float = Field(default=0, ge=0, le=3_600_000)
    handoffs: int = Field(default=0, ge=0, le=16)
    reason_code: str | None = Field(default=None, max_length=64)

    @field_validator("task_id", "correlation_id", "worker_id", "reason_code")
    @classmethod
    def safe_ids(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_ID.fullmatch(value):
            raise ValueError("telemetry identifier is invalid")
        return value

    @field_validator("duration_ms")
    @classmethod
    def finite_duration(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("telemetry duration must be finite")
        return value


class TelemetrySink(Protocol):
    def record(self, event: RouteTelemetryEvent) -> None: ...


class InMemoryTelemetrySink:
    """Test/reference sink; production adapters must be injected explicitly."""

    def __init__(self) -> None:
        self.events: list[RouteTelemetryEvent] = []
        self._lock = threading.Lock()

    def record(self, event: RouteTelemetryEvent) -> None:
        with self._lock:
            if len(self.events) < 1024:
                self.events.append(event)


class BoundedTelemetrySink:
    """Non-blocking bounded adapter for potentially slow exporters."""

    def __init__(self, downstream: TelemetrySink, *, max_queue: int = 128) -> None:
        if max_queue < 1 or max_queue > 10_000:
            raise ValueError("telemetry queue size is invalid")
        self._downstream = downstream
        self._queue: Queue[RouteTelemetryEvent | None] = Queue(maxsize=max_queue)
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def record(self, event: RouteTelemetryEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except Full:
            # Telemetry is diagnostic; dropping an event must not block or alter
            # the safety boundary.
            return

    def flush(self, timeout: float = 1.0) -> None:
        self._queue.join()
        self._thread.join(timeout=max(0.0, timeout))

    def _drain(self) -> None:
        while True:
            try:
                event = self._queue.get()
            except Empty:
                continue
            try:
                if event is None:
                    return
                with suppress(Exception):
                    self._downstream.record(event)
            finally:
                self._queue.task_done()
