"""Deterministic Stage 5 parsing and validation of claim-level citations.

The provider may propose chunk IDs, but it never owns response citation IDs or
metadata.  This module resolves proposals only against the exact chunks selected
into the request context and copies every public citation field from that evidence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from backend.app.embeddings.models import RetrievedChunk
from backend.app.rag.models import Citation, CitationClaim, ContextWindow

_TOKEN_PATTERN = re.compile(r"[\w]+", re.UNICODE)
_SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+")
_NEGATED_CONTRACTION_PATTERN = re.compile(r"\b\w+(?:n't|n\u2019t)\b", re.IGNORECASE)
_NEGATION_MARKERS = frozenset({"not", "no", "never", "without"})
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


class CitationOutputError(ValueError):
    """The provider did not honour EKAA's constrained citation contract."""


@dataclass(frozen=True)
class ProposedClaim:
    """Untrusted provider claim and its untrusted selected-source proposals."""

    text: str
    citation_chunk_ids: tuple[str, ...]


class CitationOutputParser:
    """Parse a deliberately small provider-neutral JSON response contract."""

    def __init__(self, *, max_claims: int, max_claim_characters: int) -> None:
        self._max_claims = max_claims
        self._max_claim_characters = max_claim_characters

    def parse(self, value: str) -> tuple[ProposedClaim, ...]:
        try:
            payload: Any = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CitationOutputError(
                "provider response was not citation JSON"
            ) from exc
        if not isinstance(payload, dict) or set(payload) != {"claims"}:
            raise CitationOutputError("provider citation response shape was invalid")
        claims = payload["claims"]
        if not isinstance(claims, list) or not len(claims) <= self._max_claims:
            raise CitationOutputError("provider citation claim count was invalid")

        parsed: list[ProposedClaim] = []
        for item in claims:
            if not isinstance(item, dict) or set(item) != {
                "text",
                "citation_chunk_ids",
            }:
                raise CitationOutputError("provider citation claim shape was invalid")
            text = item["text"]
            source_ids = item["citation_chunk_ids"]
            if (
                not isinstance(text, str)
                or not text.strip()
                or len(text.strip()) > self._max_claim_characters
                or not isinstance(source_ids, list)
                or not all(
                    isinstance(source_id, str) and source_id.strip()
                    for source_id in source_ids
                )
            ):
                raise CitationOutputError("provider citation claim fields were invalid")
            parsed.append(
                ProposedClaim(
                    text=text.strip(),
                    citation_chunk_ids=tuple(
                        source_id.strip() for source_id in source_ids
                    ),
                )
            )
        return tuple(parsed)


class CitationValidator:
    """Validate source existence and lexical-entailment-oriented excerpts.

    This is a deterministic baseline, not a semantic judge. A claim is supported
    only when every proposed chunk is selected for this request and contains one
    sentence containing the full meaningful claim-token sequence in order. This
    conservatively rejects many valid paraphrases but prevents returning an
    unsupported factual claim as if it had a citation.
    """

    def __init__(self, *, max_excerpt_characters: int) -> None:
        self._max_excerpt_characters = max_excerpt_characters

    def validate(
        self,
        claims: tuple[ProposedClaim, ...],
        *,
        context: ContextWindow,
    ) -> tuple[tuple[CitationClaim, ...], tuple[Citation, ...]]:
        selected_by_id = {chunk.chunk_id: chunk for chunk in context.selected_chunks}
        validated_claims: list[CitationClaim] = []
        citations: list[Citation] = []
        citation_number = 1

        for proposed in claims:
            unique_ids = tuple(dict.fromkeys(proposed.citation_chunk_ids))
            if not unique_ids:
                validated_claims.append(
                    CitationClaim(
                        text=proposed.text,
                        status="unsupported",
                        citation_ids=(),
                        unsupported_reason="missing_citation",
                    )
                )
                continue
            chunks = [selected_by_id.get(chunk_id) for chunk_id in unique_ids]
            if any(chunk is None for chunk in chunks):
                validated_claims.append(
                    CitationClaim(
                        text=proposed.text,
                        status="unsupported",
                        citation_ids=(),
                        unsupported_reason="citation_not_selected",
                    )
                )
                continue
            excerpts = [
                self._excerpt_for_claim(proposed.text, chunk)
                for chunk in chunks
                if chunk is not None
            ]
            if any(excerpt is None for excerpt in excerpts):
                validated_claims.append(
                    CitationClaim(
                        text=proposed.text,
                        status="unsupported",
                        citation_ids=(),
                        unsupported_reason="citation_does_not_support_claim",
                    )
                )
                continue

            claim_citation_ids: list[str] = []
            for chunk, excerpt in zip(chunks, excerpts, strict=True):
                assert chunk is not None
                assert excerpt is not None
                citation_id = f"citation-{citation_number}"
                citation_number += 1
                claim_citation_ids.append(citation_id)
                citations.append(self._citation(citation_id, chunk, excerpt))
            validated_claims.append(
                CitationClaim(
                    text=proposed.text,
                    status="supported",
                    citation_ids=tuple(claim_citation_ids),
                    unsupported_reason=None,
                )
            )
        return tuple(validated_claims), tuple(citations)

    def _excerpt_for_claim(self, claim: str, chunk: RetrievedChunk) -> str | None:
        claim_tokens = self._meaningful_tokens(claim)
        if not claim_tokens:
            return None
        for sentence in _SENTENCE_PATTERN.split(chunk.content):
            normalized = sentence.strip()
            sentence_tokens = self._meaningful_tokens(normalized)
            if self._is_ordered_subsequence(
                claim_tokens, sentence_tokens
            ) and self._negation_polarity_is_preserved(claim_tokens, sentence_tokens):
                return self._bounded_excerpt(normalized)
        return None

    def _bounded_excerpt(self, sentence: str) -> str | None:
        if len(sentence) <= self._max_excerpt_characters:
            return sentence
        # A truncated citation could hide or alter its support, so do not expose it.
        return None

    @staticmethod
    def _citation(citation_id: str, chunk: RetrievedChunk, excerpt: str) -> Citation:
        return Citation(
            citation_id=citation_id,
            document_id=chunk.document_id,
            document_name=chunk.document_name,
            chunk_id=chunk.chunk_id,
            chunk_index=chunk.chunk_index,
            excerpt=excerpt,
            page_number=chunk.page_number,
            section=chunk.section,
        )

    @staticmethod
    def _meaningful_tokens(value: str) -> tuple[str, ...]:
        normalized = _NEGATED_CONTRACTION_PATTERN.sub(
            lambda match: f"{match.group(0)[:-3]} not", value.casefold()
        ).replace("cannot", "can not")
        return tuple(
            token
            for token in _TOKEN_PATTERN.findall(normalized)
            if token not in _STOP_WORDS
        )

    @staticmethod
    def _is_ordered_subsequence(
        claim_tokens: tuple[str, ...], sentence_tokens: tuple[str, ...]
    ) -> bool:
        """Preserve claim order and repeated terms when testing support."""
        remaining_tokens = iter(sentence_tokens)
        return all(
            any(token == candidate for candidate in remaining_tokens)
            for token in claim_tokens
        )

    @staticmethod
    def _negation_polarity_is_preserved(
        claim_tokens: tuple[str, ...], sentence_tokens: tuple[str, ...]
    ) -> bool:
        """Reject a positive claim when its supporting sentence is explicitly negative.

        This intentionally treats every sentence-level negation marker as relevant.
        It is conservative, but avoids turning a directly negated source statement
        into a cited positive answer without claiming full semantic understanding.
        """
        sentence_is_negative = bool(_NEGATION_MARKERS.intersection(sentence_tokens))
        claim_is_negative = bool(_NEGATION_MARKERS.intersection(claim_tokens))
        return not sentence_is_negative or claim_is_negative
