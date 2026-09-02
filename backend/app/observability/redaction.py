"""Bounded telemetry redaction; telemetry must never contain prompts or secrets."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_SENSITIVE = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|prompt|"
    r"content|document|chunk|embedding|provider[_-]?payload)",
    re.I,
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_KEY = re.compile(r"\b(?:sk|key|token)[-_][A-Za-z0-9_-]{8,}\b", re.I)
_MAX_DEPTH = 4
_MAX_ITEMS = 32
_MAX_STRING = 256
_SAFE_SCALAR_KEYS = frozenset(
    {
        "component",
        "route",
        "http.method",
        "http.route",
        "llm.operation",
        "rag.operation",
        "operation",
        "outcome",
        "status",
    }
)


def redact_telemetry(value: Any, *, _depth: int = 0) -> Any:
    """Return a safe structural value suitable for logs, spans, and labels.

    Values with sensitive field names are discarded entirely.  Free text is not
    exported: it is reduced to a bounded length and known credential patterns
    are replaced.  This is deliberately conservative because retrieved text is
    untrusted and may contain secrets even when the key name looks harmless.
    """
    if _depth > _MAX_DEPTH:
        return "[redacted-depth]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:_MAX_ITEMS]:
            key_text = str(key)[:128]
            if _SENSITIVE.search(key_text):
                result[key_text] = "[redacted]"
            elif key_text in _SAFE_SCALAR_KEYS:
                result[key_text] = (
                    _clean_safe_string(item)
                    if isinstance(item, str)
                    else redact_telemetry(item, _depth=_depth + 1)
                )
            elif isinstance(item, Mapping):
                # Preserve structure for safe nested fields, but never preserve
                # arbitrary free-form values at the telemetry boundary.
                result[key_text] = redact_telemetry(item, _depth=_depth + 1)
            else:
                result[key_text] = "[redacted]"
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [
            redact_telemetry(item, _depth=_depth + 1)
            for item in list(value)[:_MAX_ITEMS]
        ]
    if isinstance(value, str):
        return "[redacted]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


def _clean_safe_string(value: str) -> str:
    safe = _BEARER.sub("Bearer [redacted]", value)
    safe = _KEY.sub("[redacted-credential]", safe)
    return safe[:_MAX_STRING]


def redacted_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Redact and normalize span attributes while excluding free-form payloads."""
    value = redact_telemetry(attributes)
    return value if isinstance(value, dict) else {}
