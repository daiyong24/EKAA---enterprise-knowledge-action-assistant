"""OpenTelemetry-compatible tracing seam without a hard dependency on OTel."""

from __future__ import annotations

import contextvars
import logging
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from backend.app.observability.redaction import redacted_attributes

logger = logging.getLogger(__name__)
_correlation: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ekaa_correlation_id", default=None
)
_trace: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ekaa_trace_id", default=None
)
_span: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ekaa_span_id", default=None
)


def current_correlation_id() -> str | None:
    return _correlation.get()


def current_trace_id() -> str | None:
    return _trace.get()


@dataclass(frozen=True, slots=True)
class SpanRecord:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    start_time: datetime
    duration_ms: float
    status: str
    attributes: dict[str, object]


class TraceExporter(Protocol):
    def export(self, span: SpanRecord) -> None: ...


class InMemoryTraceExporter:
    def __init__(self, *, max_spans: int = 10_000) -> None:
        self._max_spans = max_spans
        self._spans: list[SpanRecord] = []

    def export(self, span: SpanRecord) -> None:
        self._spans.append(span)
        if len(self._spans) > self._max_spans:
            del self._spans[: len(self._spans) - self._max_spans]

    def snapshot(self) -> tuple[SpanRecord, ...]:
        return tuple(self._spans)


class Tracer:
    def __init__(self, exporter: TraceExporter, *, sample_rate: float = 1.0) -> None:
        if not 0 <= sample_rate <= 1:
            raise ValueError("sample rate must be between 0 and 1")
        self._exporter = exporter
        self._sample_rate = sample_rate

    @contextmanager
    def span(
        self,
        name: str,
        *,
        correlation_id: str | None = None,
        attributes: Mapping[str, object] | None = None,
        force_sample: bool = False,
    ) -> Iterator[str]:
        parent = _span.get()
        trace_id = _trace.get() or uuid.uuid4().hex
        span_id = uuid.uuid4().hex[:16]
        started_at = datetime.now(UTC)
        sampled = (
            force_sample
            or self._sample_rate >= 1
            or (
                self._sample_rate > 0
                and int(trace_id[:8], 16) / 0xFFFFFFFF < self._sample_rate
            )
        )
        tokens = (
            _trace.set(trace_id),
            _correlation.set(correlation_id or _correlation.get()),
        )
        span_token = _span.set(span_id)
        started = time.perf_counter()
        status = "ok"
        try:
            yield span_id
        except BaseException:
            status = "error"
            raise
        finally:
            if sampled:
                record = SpanRecord(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent,
                    name=name[:128],
                    start_time=started_at,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    status=status,
                    attributes=redacted_attributes(attributes or {}),
                )
                try:
                    self._exporter.export(record)
                except Exception:  # exporter failure must not affect the request
                    logger.warning(
                        "telemetry_export_failed exporter=%s",
                        type(self._exporter).__name__,
                    )
            _trace.reset(tokens[0])
            _correlation.reset(tokens[1])
            _span.reset(span_token)
