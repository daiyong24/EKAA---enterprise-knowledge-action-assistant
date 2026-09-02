"""Small Prometheus-compatible metrics registry with bounded cardinality."""

from __future__ import annotations

import math
import re
import threading
from collections import defaultdict
from collections.abc import Mapping

_LABEL_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,31}$")
_METRIC_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,127}$")


class MetricRegistry:
    """In-process counters/histograms with an intentionally tiny label surface."""

    def __init__(self, *, max_series: int = 2_000) -> None:
        if max_series < 1:
            raise ValueError("max_series must be positive")
        self._max_series = max_series
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = (
            defaultdict(float)
        )
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = (
            defaultdict(list)
        )
        self._lock = threading.Lock()

    def inc(
        self, name: str, value: float = 1.0, *, labels: Mapping[str, str] | None = None
    ) -> None:
        self._validate(name, value)
        key = self._key(name, labels)
        with self._lock:
            if key not in self._counters and self._series_count() >= self._max_series:
                return
            self._counters[key] += value

    def observe(
        self, name: str, value: float, *, labels: Mapping[str, str] | None = None
    ) -> None:
        self._validate(name, value)
        key = self._key(name, labels)
        with self._lock:
            if key not in self._histograms and self._series_count() >= self._max_series:
                return
            values = self._histograms[key]
            values.append(value)
            if len(values) > 10_000:
                del values[: len(values) - 10_000]

    def snapshot(self) -> dict[str, dict[tuple[tuple[str, str], ...], float]]:
        with self._lock:
            counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = {
                name: {} for name, _ in self._counters
            }
            for (name, labels), value in self._counters.items():
                counters.setdefault(name, {})[labels] = value
            for (name, labels), values in self._histograms.items():
                counters.setdefault(name, {})[labels] = float(len(values))
            return counters

    def render_prometheus(self) -> str:
        """Render only safe metric names and bounded label values."""
        lines: list[str] = []
        with self._lock:
            counter_items = list(self._counters.items())
            histogram_items = list(self._histograms.items())
        for (name, labels), value in sorted(counter_items):
            lines.append(f"{name}{_render_labels(labels)} {value:g}")
        for (name, labels), values in sorted(histogram_items):
            if not values:
                continue
            lines.append(f"{name}_count{_render_labels(labels)} {len(values)}")
            lines.append(f"{name}_sum{_render_labels(labels)} {sum(values):g}")
        return "\n".join(lines) + ("\n" if lines else "")

    def _series_count(self) -> int:
        return len(self._counters) + len(self._histograms)

    @staticmethod
    def _validate(name: str, value: float) -> None:
        if not _METRIC_NAME.fullmatch(name):
            raise ValueError("metric name is invalid")
        if not math.isfinite(value) or value < 0:
            raise ValueError("metric value must be finite and non-negative")

    @staticmethod
    def _key(
        name: str, labels: Mapping[str, str] | None
    ) -> tuple[str, tuple[tuple[str, str], ...]]:
        normalized = tuple(
            sorted((str(k), str(v)[:64]) for k, v in (labels or {}).items())
        )
        if len(normalized) > 8 or any(
            not _LABEL_NAME.fullmatch(key) or any(ord(char) < 32 for char in value)
            for key, value in normalized
        ):
            raise ValueError("metric labels are invalid")
        return name, normalized


def _render_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    escaped = (
        (key, value.replace("\\", "\\\\").replace('"', '\\"')) for key, value in labels
    )
    return "{" + ",".join(f'{key}="{value}"' for key, value in escaped) + "}"
