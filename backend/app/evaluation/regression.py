"""Explicit, conservative comparison gates for a recorded Stage 8 baseline."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.evaluation.models import EvaluationSummary


@dataclass(frozen=True)
class RegressionThresholds:
    """Policy values, not quality claims or production SLOs."""

    max_retrieval_metric_drop: float = 0.02
    max_human_metric_drop: float = 0.02
    max_failure_rate_increase: float = 0.01
    max_latency_p95_multiplier: float = 1.25
    max_total_tokens_multiplier: float = 1.10

    def __post_init__(self) -> None:
        values = (
            self.max_retrieval_metric_drop,
            self.max_human_metric_drop,
            self.max_failure_rate_increase,
        )
        if any(value < 0 or value > 1 for value in values):
            raise ValueError("rate thresholds must be in [0, 1]")
        if self.max_latency_p95_multiplier < 1 or self.max_total_tokens_multiplier < 1:
            raise ValueError("resource multipliers must be at least one")


@dataclass(frozen=True)
class RegressionFinding:
    metric: str
    status: str
    detail: str


@dataclass(frozen=True)
class RegressionPolicy:
    """A strict, file-backed policy; thresholds cannot be silently defaulted."""

    policy_version: str
    dataset_id: str
    dataset_version: str
    thresholds: RegressionThresholds
    sha256: str
    synthetic_corpus_sha256: str | None = None


def load_regression_policy(path: Path) -> RegressionPolicy:
    """Load a strict policy artifact and retain its exact source fingerprint."""

    try:
        raw_bytes = path.read_bytes()
        raw: Any = json.loads(raw_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("could not load regression policy") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "policy_version",
        "dataset_id",
        "dataset_version",
        "status",
        "synthetic_corpus_sha256",
        "thresholds",
        "caveat",
    }:
        raise ValueError("regression policy has unexpected or missing fields")
    if raw["schema_version"] != 1 or raw["status"] not in {
        "pending_first_reproducible_replay",
        "active",
    }:
        raise ValueError("regression policy schema or status is invalid")
    if not all(
        isinstance(raw[key], str) and raw[key].strip()
        for key in (
            "policy_version",
            "dataset_id",
            "dataset_version",
            "synthetic_corpus_sha256",
            "caveat",
        )
    ) or not isinstance(raw["thresholds"], dict):
        raise ValueError("regression policy fields are invalid")
    expected_thresholds = {
        "max_retrieval_metric_drop",
        "max_human_metric_drop",
        "max_failure_rate_increase",
        "max_latency_p95_multiplier",
        "max_total_tokens_multiplier",
    }
    if set(raw["thresholds"]) != expected_thresholds or not all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in raw["thresholds"].values()
    ):
        raise ValueError("regression policy thresholds are invalid")
    if len(raw["synthetic_corpus_sha256"]) != 64 or any(
        character not in "0123456789abcdef"
        for character in raw["synthetic_corpus_sha256"]
    ):
        raise ValueError("regression policy corpus SHA-256 is invalid")
    return RegressionPolicy(
        policy_version=raw["policy_version"],
        dataset_id=raw["dataset_id"],
        dataset_version=raw["dataset_version"],
        thresholds=RegressionThresholds(**raw["thresholds"]),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        synthetic_corpus_sha256=raw["synthetic_corpus_sha256"],
    )


def compare_to_baseline(
    *,
    baseline: EvaluationSummary,
    candidate: EvaluationSummary,
    policy: RegressionPolicy,
) -> tuple[RegressionFinding, ...]:
    """Compare like-for-like runs; unavailable human measures stay unevaluated."""

    _require_comparable(baseline=baseline, candidate=candidate, policy=policy)
    resolved_thresholds = policy.thresholds
    findings: list[RegressionFinding] = []
    for metric, old, new in (
        (
            "recall_at_k",
            baseline.retrieval.recall_at_k,
            candidate.retrieval.recall_at_k,
        ),
        (
            "hit_rate_at_k",
            baseline.retrieval.hit_rate_at_k,
            candidate.retrieval.hit_rate_at_k,
        ),
        (
            "precision_at_k",
            baseline.retrieval.precision_at_k,
            candidate.retrieval.precision_at_k,
        ),
        ("mrr", baseline.retrieval.mrr, candidate.retrieval.mrr),
    ):
        findings.append(
            _lower_is_bad(
                metric, old, new, resolved_thresholds.max_retrieval_metric_drop
            )
        )
    human_values: tuple[tuple[str, float | None, float | None], ...] = (
        ("correctness", baseline.correctness, candidate.correctness),
        ("faithfulness", baseline.faithfulness, candidate.faithfulness),
        ("citation_accuracy", baseline.citation_accuracy, candidate.citation_accuracy),
    )
    for metric, human_old, human_new in human_values:
        findings.append(
            _optional_lower_is_bad(
                metric,
                human_old,
                human_new,
                resolved_thresholds.max_human_metric_drop,
            )
        )
    findings.append(
        _higher_is_bad(
            "failure_rate",
            baseline.failure_rate,
            candidate.failure_rate,
            resolved_thresholds.max_failure_rate_increase,
        )
    )
    findings.append(
        _multiplier_is_bad(
            "latency_p95_ms",
            baseline.latency_p95_ms,
            candidate.latency_p95_ms,
            resolved_thresholds.max_latency_p95_multiplier,
        )
    )
    findings.append(
        _multiplier_is_bad(
            "total_tokens",
            float(baseline.total_tokens),
            float(candidate.total_tokens),
            resolved_thresholds.max_total_tokens_multiplier,
        )
    )
    return tuple(findings)


def _require_comparable(
    *,
    baseline: EvaluationSummary,
    candidate: EvaluationSummary,
    policy: RegressionPolicy,
) -> None:
    _require_scoring_consistency(baseline)
    _require_scoring_consistency(candidate)
    if (
        baseline.evaluation_scope != "full_dataset"
        or candidate.evaluation_scope != "full_dataset"
    ):
        raise ValueError("single-question smoke runs cannot be compared as regressions")
    if (
        baseline.dataset_id != policy.dataset_id
        or baseline.dataset_version != policy.dataset_version
        or candidate.dataset_id != policy.dataset_id
        or candidate.dataset_version != policy.dataset_version
    ):
        raise ValueError("policy does not match the evaluation dataset")
    if (
        baseline.dataset_sha256 != candidate.dataset_sha256
        or baseline.corpus_sha256 != candidate.corpus_sha256
        or baseline.top_k != candidate.top_k
    ):
        raise ValueError("baseline and candidate dataset/corpus/top_k do not match")
    if policy.synthetic_corpus_sha256 is not None and (
        baseline.corpus_sha256 != policy.synthetic_corpus_sha256
        or candidate.corpus_sha256 != policy.synthetic_corpus_sha256
    ):
        raise ValueError("evaluation corpus does not match the loaded policy")
    for field in (
        "target_id",
        "target_version",
        "model_identity",
        "configuration_sha256",
        "calibration_id",
        "policy_version",
        "policy_sha256",
    ):
        if getattr(baseline.provenance, field) != getattr(candidate.provenance, field):
            raise ValueError("baseline and candidate provenance does not match")
    if (
        baseline.provenance.policy_version != policy.policy_version
        or candidate.provenance.policy_version != policy.policy_version
        or baseline.provenance.policy_sha256 != policy.sha256
        or candidate.provenance.policy_sha256 != policy.sha256
    ):
        raise ValueError("evaluation provenance does not match the loaded policy")
    if not baseline.provenance.run_id or not candidate.provenance.run_id:
        raise ValueError("evaluation run identity is required")


def _require_scoring_consistency(summary: EvaluationSummary) -> None:
    """Reject partially recorded human scores before comparison emits findings."""

    provenance = summary.provenance
    assessment_hashes = (
        provenance.assessment_artifact_sha256,
        provenance.assessment_source_artifact_sha256,
    )
    has_assessment_provenance = all(value is not None for value in assessment_hashes)
    if (
        any(value is not None for value in assessment_hashes)
        and not has_assessment_provenance
    ):
        raise ValueError("assessment provenance must be a complete pair")
    human_values = (
        summary.correctness,
        summary.faithfulness,
        summary.citation_accuracy,
    )
    if (
        any(value is not None for value in human_values)
        and not has_assessment_provenance
    ):
        raise ValueError("human metrics require complete assessment provenance")
    if has_assessment_provenance:
        if not provenance.calibration_id:
            raise ValueError("scored summaries require a calibration identity")
        if any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in assessment_hashes
        ):
            raise ValueError("assessment provenance hashes are invalid")
    elif provenance.calibration_id is not None:
        raise ValueError("unscored summaries must not retain calibration provenance")


def _lower_is_bad(
    metric: str, old: float, new: float, allowance: float
) -> RegressionFinding:
    return RegressionFinding(
        metric, "pass" if new >= old - allowance else "fail", "evaluated"
    )


def _optional_lower_is_bad(
    metric: str, old: float | None, new: float | None, allowance: float
) -> RegressionFinding:
    if old is None or new is None:
        return RegressionFinding(metric, "not_evaluated", "human review unavailable")
    return _lower_is_bad(metric, old, new, allowance)


def _higher_is_bad(
    metric: str, old: float, new: float, allowance: float
) -> RegressionFinding:
    return RegressionFinding(
        metric, "pass" if new <= old + allowance else "fail", "evaluated"
    )


def _multiplier_is_bad(
    metric: str, old: float | None, new: float | None, multiplier: float
) -> RegressionFinding:
    if old is None or new is None:
        return RegressionFinding(metric, "not_evaluated", "measurement unavailable")
    if old == 0:
        return RegressionFinding(metric, "pass" if new == 0 else "fail", "evaluated")
    return RegressionFinding(
        metric, "pass" if new <= old * multiplier else "fail", "evaluated"
    )
