"""Typed, provider-neutral data contracts for RAG evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.app.llm.models import TokenUsage

ReviewStatus = Literal["reviewed"]
EvaluationScope = Literal["full_dataset", "single_question_smoke"]


@dataclass(frozen=True)
class GoldenQuestion:
    """A reviewed synthetic/public question and its expected evidence chunks."""

    question_id: str
    question: str
    expected_chunk_ids: tuple[str, ...]
    provenance: Literal["synthetic", "public"]
    review_status: ReviewStatus
    review_note: str


@dataclass(frozen=True)
class GoldenDataset:
    """A versioned immutable-in-practice set of reviewed evaluation questions."""

    schema_version: int
    dataset_id: str
    dataset_version: str
    source_policy: Literal["synthetic_or_public_only"]
    questions: tuple[GoldenQuestion, ...]


@dataclass(frozen=True)
class HumanAssessment:
    """A calibrated human judgement; ``None`` means deliberately unreviewed."""

    question_id: str
    correctness: bool | None
    faithfulness: bool | None
    citation_accuracy: bool | None
    reviewer_id: str


@dataclass(frozen=True)
class AssessmentSource:
    """Content-free identity of the generation artifact a review evaluated."""

    source_artifact_sha256: str
    source_run_id: str
    dataset_id: str
    dataset_version: str
    dataset_sha256: str
    corpus_sha256: str
    top_k: int
    target_id: str
    target_version: str
    model_identity: str
    configuration_sha256: str
    policy_version: str
    policy_sha256: str


@dataclass(frozen=True)
class AssessmentBundle:
    """Strict v2 human-review envelope and its raw-byte fingerprint."""

    calibration_set_id: str
    source: AssessmentSource
    assessments: tuple[HumanAssessment, ...]
    artifact_sha256: str


@dataclass(frozen=True)
class EvaluationObservation:
    """Safe, content-free facts captured for one evaluation question.

    ``retrieved_chunk_ids`` are the ranked server-owned retrieval result. Generation
    fields are independent observations and never alter retrieval metrics.
    """

    question_id: str
    retrieved_chunk_ids: tuple[str, ...] | None
    model: str | None
    latency_ms: float | None
    usage: TokenUsage | None
    claim_count: int | None
    unsupported_claim_count: int | None
    failure_kind: str | None


@dataclass(frozen=True)
class EvaluationRecord:
    """One replayable result. It intentionally excludes prompt and document text."""

    question_id: str
    expected_chunk_count: int
    retrieved_chunk_count: int | None
    matched_expected_chunk_count: int | None
    first_expected_rank: int | None
    latency_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    claim_count: int | None
    unsupported_claim_count: int | None
    failure_kind: str | None


@dataclass(frozen=True)
class RetrievalMetrics:
    evaluated_questions: int
    missing_results: int
    recall_at_k: float
    hit_rate_at_k: float
    precision_at_k: float
    mrr: float


@dataclass(frozen=True)
class EvaluationSummary:
    """Aggregate values with explicit ``None`` for unavailable measurements."""

    dataset_id: str
    dataset_version: str
    dataset_sha256: str
    corpus_sha256: str | None
    top_k: int
    evaluation_scope: EvaluationScope
    provenance: RunProvenance
    retrieval: RetrievalMetrics
    correctness: float | None
    faithfulness: float | None
    citation_accuracy: float | None
    unsupported_claim_rate: float | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    failures: int
    failure_rate: float
    estimated_cost_usd: float | None
    cost_unavailable_reason: str | None
    records: tuple[EvaluationRecord, ...]


@dataclass(frozen=True)
class RunProvenance:
    """Content-free identity required to replay and compare evaluation runs."""

    target_id: str
    target_version: str
    model_identity: str | None
    configuration_sha256: str
    run_id: str
    calibration_id: str | None
    policy_version: str
    policy_sha256: str
    assessment_artifact_sha256: str | None = None
    assessment_source_artifact_sha256: str | None = None
