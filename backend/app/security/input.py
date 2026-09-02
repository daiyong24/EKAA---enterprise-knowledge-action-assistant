"""Conservative hostile-input checks; detection never grants authority."""

from __future__ import annotations

import re
from dataclasses import dataclass

_INJECTION_PATTERNS = (
    (
        "instruction_override",
        re.compile(
            r"(?i)ignore\s+(?:(?:all|any)\s+)?(?:previous|prior|these|the)\s+instructions"
        ),
    ),
    (
        "prompt_exfiltration",
        re.compile(
            r"(?i)(?:reveal|print|show|leak)\s+(?:the\s+)?(?:system|developer)\s+prompt"
        ),
    ),
    (
        "tool_authority_escalation",
        re.compile(
            r"(?i)(?:call|invoke|run|execute)\s+(?:the\s+)?tool\s+without\s+approval"
        ),
    ),
    (
        "delimiter_spoofing",
        re.compile(r"(?i)<\/?(?:system|developer|tool)>|\[/?SYSTEM\]"),
    ),
)


@dataclass(frozen=True, slots=True)
class InjectionFinding:
    category: str
    matched: bool


def scan_untrusted_text(
    value: str, *, max_characters: int = 8_000
) -> tuple[InjectionFinding, ...]:
    if not isinstance(value, str) or not value or len(value) > max_characters:
        raise ValueError("untrusted text is invalid or too large")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise ValueError("untrusted text contains control characters")
    return tuple(
        InjectionFinding(category, bool(pattern.search(value)))
        for category, pattern in _INJECTION_PATTERNS
    )
