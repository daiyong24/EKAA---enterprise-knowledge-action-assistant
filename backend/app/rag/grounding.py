"""Conservative Stage 4 output guard over the exact supplied evidence."""

from __future__ import annotations

import re
from typing import Protocol

_TOKEN_PATTERN = re.compile(r"[\w]+", re.UNICODE)
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    }
)


class GroundedOutputGuard(Protocol):
    """Decide whether a provider answer is safe to expose for Stage 4."""

    def is_supported(self, *, answer: str, context: str) -> bool: ...


class LexicalGroundedOutputGuard:
    """Reject answer terms that do not occur in the exact selected context.

    This is a deliberately conservative, provider-independent guard. It catches
    demonstrably unsupported additions such as a different duration or a named owner
    absent from the evidence. It does not prove sentence-level entailment, resolve
    negation, or understand synonyms; Stage 5 owns those claim-level concerns.
    """

    def is_supported(self, *, answer: str, context: str) -> bool:
        answer_terms = self._meaningful_terms(answer)
        context_terms = self._meaningful_terms(context)
        return bool(answer_terms) and answer_terms.issubset(context_terms)

    @staticmethod
    def _meaningful_terms(value: str) -> set[str]:
        return {
            token
            for token in (item.casefold() for item in _TOKEN_PATTERN.findall(value))
            if token not in _STOP_WORDS
        }
