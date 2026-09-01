"""Deterministic metric formulas for Stage 8 evaluation artifacts."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from backend.app.evaluation.models import (
    EvaluationObservation,
    EvaluationRecord,
    HumanAssessment,
    RetrievalMetrics,
)


def retrieval_metrics(
    *,
    observations: Sequence[EvaluationObservation],
    expected_chunk_ids: Sequence[tuple[str, ...]],
    top_k: int,
) -> RetrievalMetrics:
    """Calculate macro recall, hit rate, precision, and MRR at a fixed K.

    Missing retrieval output is a zero-relevance result, not a dropped denominator.
    Precision@K uses K as its denominator, so returning fewer than K results cannot
    inflate a score. Duplicate returned IDs contribute only at their first rank.
    """

    if top_k < 1:
        raise ValueError("top_k must be positive")
    if len(observations) != len(expected_chunk_ids):
        raise ValueError("observations and expected IDs must align")
    if not observations:
        return RetrievalMetrics(0, 0, 0.0, 0.0, 0.0, 0.0)

    recalls: list[float] = []
    hits: list[float] = []
    precisions: list[float] = []
    reciprocal_ranks: list[float] = []
    missing_results = 0
    for observation, expected in zip(observations, expected_chunk_ids, strict=True):
        expected_set = set(expected)
        if not expected_set:
            raise ValueError("a golden question must expect at least one chunk")
        actual = observation.retrieved_chunk_ids
        if actual is None:
            missing_results += 1
            ranked: tuple[str, ...] = ()
        else:
            ranked = _first_unique(actual)[:top_k]
        relevant = [chunk_id for chunk_id in ranked if chunk_id in expected_set]
        recalls.append(len(set(relevant)) / len(expected_set))
        hits.append(1.0 if relevant else 0.0)
        precisions.append(len(set(relevant)) / top_k)
        first_rank = next(
            (
                index
                for index, chunk_id in enumerate(ranked, start=1)
                if chunk_id in expected_set
            ),
            None,
        )
        reciprocal_ranks.append(0.0 if first_rank is None else 1.0 / first_rank)
    return RetrievalMetrics(
        evaluated_questions=len(observations),
        missing_results=missing_results,
        recall_at_k=_mean(recalls),
        hit_rate_at_k=_mean(hits),
        precision_at_k=_mean(precisions),
        mrr=_mean(reciprocal_ranks),
    )


def human_review_rate(
    assessments: Iterable[HumanAssessment], field: str
) -> float | None:
    """Return a rate only when every reviewed row has a judgement for the metric."""

    if field not in {"correctness", "faithfulness", "citation_accuracy"}:
        raise ValueError("unknown human review field")
    assessment_values = tuple(assessments)
    values = tuple(
        _assessment_value(assessment, field) for assessment in assessment_values
    )
    if not values or any(value is None for value in values):
        return None
    return sum(value is True for value in values) / len(values)


def unsupported_claim_rate(
    observations: Iterable[EvaluationObservation],
) -> float | None:
    """Return unsupported claims / all emitted claims, or unavailable when no claims."""

    known = [
        observation
        for observation in observations
        if observation.claim_count is not None
        and observation.unsupported_claim_count is not None
    ]
    claims = sum(observation.claim_count or 0 for observation in known)
    if claims == 0:
        return None
    unsupported = sum(observation.unsupported_claim_count or 0 for observation in known)
    return unsupported / claims


def percentile_nearest_rank(values: Iterable[float], percentile: int) -> float | None:
    """Use the documented nearest-rank percentile over non-negative finite values."""

    cleaned = sorted(value for value in values if math.isfinite(value) and value >= 0)
    if not cleaned:
        return None
    if percentile < 1 or percentile > 100:
        raise ValueError("percentile must be in [1, 100]")
    rank = math.ceil(percentile / 100 * len(cleaned))
    return cleaned[rank - 1]


def to_record(
    *,
    observation: EvaluationObservation,
    expected_chunk_ids: tuple[str, ...],
    top_k: int,
) -> EvaluationRecord:
    """Reduce an observation to an artifact-safe record without user/content text."""

    actual = (
        _first_unique(observation.retrieved_chunk_ids)[:top_k]
        if observation.retrieved_chunk_ids is not None
        else None
    )
    matched = (
        len(set(actual).intersection(expected_chunk_ids))
        if actual is not None
        else None
    )
    first_expected_rank = (
        next(
            (
                index
                for index, chunk_id in enumerate(actual, start=1)
                if chunk_id in expected_chunk_ids
            ),
            None,
        )
        if actual is not None
        else None
    )
    usage = observation.usage
    return EvaluationRecord(
        question_id=observation.question_id,
        expected_chunk_count=len(expected_chunk_ids),
        retrieved_chunk_count=len(actual) if actual is not None else None,
        matched_expected_chunk_count=matched,
        first_expected_rank=first_expected_rank,
        latency_ms=observation.latency_ms,
        input_tokens=usage.input_tokens if usage is not None else None,
        output_tokens=usage.output_tokens if usage is not None else None,
        total_tokens=usage.total_tokens if usage is not None else None,
        claim_count=observation.claim_count,
        unsupported_claim_count=observation.unsupported_claim_count,
        failure_kind=observation.failure_kind,
    )


def _first_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _assessment_value(assessment: HumanAssessment, field: str) -> bool | None:
    if field == "correctness":
        return assessment.correctness
    if field == "faithfulness":
        return assessment.faithfulness
    return assessment.citation_accuracy
