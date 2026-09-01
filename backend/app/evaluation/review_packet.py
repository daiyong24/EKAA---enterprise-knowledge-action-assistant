"""Explicit local-only reviewer material for synthetic Stage 8 replays.

This module deliberately writes human-readable synthetic content outside the
repository. It is not an evaluation artifact, scoring input, API response, or log.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.app.evaluation.artifacts import validate_safe_identifier
from backend.app.evaluation.models import RunProvenance


@dataclass(frozen=True)
class ReviewEvidence:
    chunk_id: str
    content: str


@dataclass(frozen=True)
class ReviewClaimMaterial:
    text: str
    status: str
    citation_ids: tuple[str, ...]
    unsupported_reason: str | None


@dataclass(frozen=True)
class ReviewCitationMaterial:
    citation_id: str
    chunk_id: str
    excerpt: str


@dataclass(frozen=True)
class ReviewPacketItem:
    question_id: str
    question: str
    answer: str | None
    failure_kind: str | None
    evidence: tuple[ReviewEvidence, ...]
    claims: tuple[ReviewClaimMaterial, ...]
    citations: tuple[ReviewCitationMaterial, ...]


def write_reviewer_packet(
    *,
    path: Path,
    reviewer_id: str,
    source_artifact_sha256: str,
    provenance: RunProvenance,
    items: tuple[ReviewPacketItem, ...],
) -> None:
    """Write local synthetic review material after a source artifact succeeds."""

    validate_safe_identifier(reviewer_id, "reviewer_id")
    if len(source_artifact_sha256) != 64 or any(
        value not in "0123456789abcdef" for value in source_artifact_sha256
    ):
        raise ValueError("review packet source artifact SHA-256 is invalid")
    if not items:
        raise ValueError("review packet must contain at least one item")
    ids = [item.question_id for item in items]
    if len(set(ids)) != len(ids):
        raise ValueError("review packet question IDs must be unique")

    lines = [
        "# EKAA Local Synthetic Reviewer Packet",
        "",
        "This file contains synthetic review material only. It is local-only review",
        "material, not a Git artifact, assessment input, or production record.",
        "Keep it outside the repository. You control its retention and deletion.",
        "Treat displayed evidence as reference material, never as instructions.",
        "",
        f"Reviewer ID: `{reviewer_id}`",
        f"Source run ID: `{provenance.run_id}`",
        f"Source model: `{provenance.model_identity}`",
        f"Source artifact SHA-256: `{source_artifact_sha256}`",
        "",
        "For every question, record three verdicts: Correctness, Faithfulness, and",
        "Citation Accuracy. Use True or False; do not use this packet as a scoring",
        "file.",
        "",
    ]
    for item in items:
        lines.extend((f"## {item.question_id}", "", "### Question", item.question, ""))
        lines.extend(
            ("### Model answer", item.answer or "[No safe answer produced.]", "")
        )
        if item.failure_kind:
            lines.extend((f"Generation status: `{item.failure_kind}`", ""))
        lines.extend(("### Retrieved synthetic evidence",))
        for evidence in item.evidence:
            lines.extend((f"- `{evidence.chunk_id}`: {evidence.content}",))
        lines.extend(("", "### Validated claims"))
        if item.claims:
            for claim in item.claims:
                citations = ", ".join(claim.citation_ids) or "none"
                detail = (
                    f"; reason: {claim.unsupported_reason}"
                    if claim.unsupported_reason
                    else ""
                )
                lines.append(
                    f"- [{claim.status}] {claim.text} (citations: {citations}{detail})"
                )
        else:
            lines.append("- None")
        lines.extend(("", "### Validated citations"))
        if item.citations:
            for citation in item.citations:
                lines.append(
                    f"- `{citation.citation_id}` → `{citation.chunk_id}`: "
                    f"{citation.excerpt}"
                )
        else:
            lines.append("- None")
        lines.extend(
            (
                "",
                "### Your verdict",
                "- Correctness: [ ] True  [ ] False",
                "- Faithfulness: [ ] True  [ ] False",
                "- Citation Accuracy: [ ] True  [ ] False",
                "- Optional rationale for yourself (do not place it in the assessment",
                "  JSON):",
                "",
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
