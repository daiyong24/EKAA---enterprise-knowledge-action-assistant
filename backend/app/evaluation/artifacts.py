"""Strict, content-free Stage 8 generation artifact and offline-score handling."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

from backend.app.evaluation.corpus import APPROVED_LOCAL_OLLAMA_CORPUS_SHA256
from backend.app.evaluation.dataset import dataset_sha256
from backend.app.evaluation.metrics import percentile_nearest_rank
from backend.app.evaluation.models import (
    AssessmentBundle,
    AssessmentSource,
    EvaluationRecord,
    EvaluationSummary,
    GoldenDataset,
    RetrievalMetrics,
    RunProvenance,
)
from backend.app.evaluation.regression import RegressionPolicy

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ELIGIBLE_GENERATION_TARGETS = frozenset(
    {
        ("synthetic-corpus-ollama-generation", "1.2.0"),
        ("synthetic-corpus-ollama-generation", "1.3.0"),
        ("synthetic-corpus-ollama-generation", "1.3.1"),
        ("synthetic-corpus-ollama-generation", "1.3.2"),
        ("synthetic-corpus-ollama-generation", "1.4.0"),
    }
)
_SUMMARY_FIELDS = {
    "dataset_id",
    "dataset_version",
    "dataset_sha256",
    "corpus_sha256",
    "top_k",
    "evaluation_scope",
    "provenance",
    "retrieval",
    "correctness",
    "faithfulness",
    "citation_accuracy",
    "unsupported_claim_rate",
    "latency_p50_ms",
    "latency_p95_ms",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "failures",
    "failure_rate",
    "estimated_cost_usd",
    "cost_unavailable_reason",
    "records",
}
_PROVENANCE_FIELDS = {
    "target_id",
    "target_version",
    "model_identity",
    "configuration_sha256",
    "run_id",
    "calibration_id",
    "policy_version",
    "policy_sha256",
    "assessment_artifact_sha256",
    "assessment_source_artifact_sha256",
}
_RETRIEVAL_FIELDS = {
    "evaluated_questions",
    "missing_results",
    "recall_at_k",
    "hit_rate_at_k",
    "precision_at_k",
    "mrr",
}
_RECORD_FIELDS = {field.name for field in fields(EvaluationRecord)}


@dataclass(frozen=True)
class GenerationSourceArtifact:
    """A validated raw artifact that can be scored exactly once per review."""

    summary: EvaluationSummary
    sha256: str


def write_artifacts(
    *, summary: EvaluationSummary, json_path: Path, csv_path: Path
) -> None:
    """Write fully constructed content-free artifacts after all caller validation."""

    _ensure_distinct_paths((json_path, csv_path))
    payload = {
        "artifact_schema_version": 2,
        "artifact_kind": (
            "stage8_generation_source"
            if _is_unscored_generation_source(summary)
            else "stage8_evaluation_result"
        ),
        "summary": asdict(summary),
    }
    json_text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    record_fields = [field.name for field in fields(EvaluationRecord)]
    # csv.DictWriter has no supported in-memory target other than a text stream.
    import io

    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=record_fields)
    writer.writeheader()
    writer.writerows(asdict(record) for record in summary.records)
    csv_text = stream.getvalue()
    json_path.write_text(json_text, encoding="utf-8")
    csv_path.write_text(csv_text, encoding="utf-8", newline="")


def _is_unscored_generation_source(summary: EvaluationSummary) -> bool:
    """Return whether this summary can be the source of a future human review."""

    provenance = summary.provenance
    if summary.evaluation_scope == "full_dataset" and _is_eligible_generation_target(
        provenance
    ):
        # Source-artifact readers require these exact bounded identities.  Enforce
        # the same contract before this writer can label the result as scoreable.
        validate_safe_identifier(provenance.model_identity, "model_identity")
        validate_safe_identifier(provenance.run_id, "run_id")
    return (
        summary.evaluation_scope == "full_dataset"
        and _is_eligible_generation_target(provenance)
        and isinstance(provenance.model_identity, str)
        and bool(provenance.model_identity)
        and summary.corpus_sha256 == APPROVED_LOCAL_OLLAMA_CORPUS_SHA256
        and summary.correctness is None
        and summary.faithfulness is None
        and summary.citation_accuracy is None
        and provenance.calibration_id is None
        and provenance.assessment_artifact_sha256 is None
        and provenance.assessment_source_artifact_sha256 is None
    )


def load_generation_source_artifact(
    path: Path, *, dataset: GoldenDataset, dataset_path: Path, policy: RegressionPolicy
) -> GenerationSourceArtifact:
    """Read, hash, and verify one eligible raw source file exactly once."""

    try:
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("could not load generation source artifact") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "artifact_schema_version",
        "artifact_kind",
        "summary",
    }:
        raise ValueError("generation source artifact has unexpected or missing fields")
    if raw["artifact_schema_version"] != 2 or isinstance(
        raw["artifact_schema_version"], bool
    ):
        raise ValueError("generation source artifact schema is invalid")
    if raw["artifact_kind"] != "stage8_generation_source":
        raise ValueError("generation source artifact kind is invalid")
    summary = _summary_from_raw(raw["summary"], dataset=dataset)
    if summary.dataset_sha256 != dataset_sha256(dataset_path):
        raise ValueError("generation source artifact dataset SHA-256 does not match")
    _require_eligible_source(summary, policy=policy)
    _recompute_and_compare(summary, dataset=dataset)
    return GenerationSourceArtifact(summary, hashlib.sha256(raw_bytes).hexdigest())


def apply_assessment_bundle(
    *,
    source: GenerationSourceArtifact,
    bundle: AssessmentBundle,
    dataset: GoldenDataset,
) -> EvaluationSummary:
    """Bind a v2 review bundle before calculating human rates."""

    _require_source_binding(source=source, binding=bundle.source)
    expected_ids = {question.question_id for question in dataset.questions}
    assessment_ids = {assessment.question_id for assessment in bundle.assessments}
    if assessment_ids != expected_ids or len(assessment_ids) != len(bundle.assessments):
        raise ValueError(
            "assessment coverage must match every reviewed question exactly once"
        )
    ordered = tuple(
        {assessment.question_id: assessment for assessment in bundle.assessments}[
            question.question_id
        ]
        for question in dataset.questions
    )
    human_rates = tuple(
        _complete_human_rate(ordered, field)
        for field in ("correctness", "faithfulness", "citation_accuracy")
    )
    return replace(
        source.summary,
        correctness=human_rates[0],
        faithfulness=human_rates[1],
        citation_accuracy=human_rates[2],
        provenance=replace(
            source.summary.provenance,
            calibration_id=bundle.calibration_set_id,
            assessment_artifact_sha256=bundle.artifact_sha256,
            assessment_source_artifact_sha256=source.sha256,
        ),
    )


def _summary_from_raw(raw: object, *, dataset: GoldenDataset) -> EvaluationSummary:
    if not isinstance(raw, dict) or set(raw) != _SUMMARY_FIELDS:
        raise ValueError("generation source summary has unexpected or missing fields")
    dataset_id = _identifier(raw["dataset_id"], "dataset_id")
    dataset_version = _identifier(raw["dataset_version"], "dataset_version")
    if dataset_id != dataset.dataset_id or dataset_version != dataset.dataset_version:
        raise ValueError("generation source artifact dataset identity does not match")
    corpus_sha256 = _sha(raw["corpus_sha256"], "corpus_sha256")
    if raw["evaluation_scope"] != "full_dataset":
        raise ValueError("only full-dataset artifacts may be human-scored")
    top_k = _positive_int(raw["top_k"], "top_k")
    return EvaluationSummary(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_sha256=_sha(raw["dataset_sha256"], "dataset_sha256"),
        corpus_sha256=corpus_sha256,
        top_k=top_k,
        evaluation_scope="full_dataset",
        provenance=_provenance(raw["provenance"]),
        retrieval=_retrieval(raw["retrieval"]),
        correctness=_nullable_rate(raw["correctness"], "correctness"),
        faithfulness=_nullable_rate(raw["faithfulness"], "faithfulness"),
        citation_accuracy=_nullable_rate(raw["citation_accuracy"], "citation_accuracy"),
        unsupported_claim_rate=_nullable_rate(
            raw["unsupported_claim_rate"], "unsupported_claim_rate"
        ),
        latency_p50_ms=_nullable_number(raw["latency_p50_ms"], "latency_p50_ms"),
        latency_p95_ms=_nullable_number(raw["latency_p95_ms"], "latency_p95_ms"),
        input_tokens=_nonnegative_int(raw["input_tokens"], "input_tokens"),
        output_tokens=_nonnegative_int(raw["output_tokens"], "output_tokens"),
        total_tokens=_nonnegative_int(raw["total_tokens"], "total_tokens"),
        failures=_nonnegative_int(raw["failures"], "failures"),
        failure_rate=_rate(raw["failure_rate"], "failure_rate"),
        estimated_cost_usd=_nullable_number(
            raw["estimated_cost_usd"], "estimated_cost_usd"
        ),
        cost_unavailable_reason=_cost_reason(
            raw["cost_unavailable_reason"], "cost_unavailable_reason"
        ),
        records=_records(raw["records"], dataset=dataset, top_k=top_k),
    )


def _provenance(raw: object) -> RunProvenance:
    if not isinstance(raw, dict) or set(raw) != _PROVENANCE_FIELDS:
        raise ValueError(
            "generation source provenance has unexpected or missing fields"
        )
    return RunProvenance(
        target_id=_identifier(raw["target_id"], "target_id"),
        target_version=_identifier(raw["target_version"], "target_version"),
        model_identity=_identifier(raw["model_identity"], "model_identity"),
        configuration_sha256=_sha(raw["configuration_sha256"], "configuration_sha256"),
        run_id=_identifier(raw["run_id"], "run_id"),
        calibration_id=_nullable_identifier(raw["calibration_id"], "calibration_id"),
        policy_version=_identifier(raw["policy_version"], "policy_version"),
        policy_sha256=_sha(raw["policy_sha256"], "policy_sha256"),
        assessment_artifact_sha256=_nullable_sha(
            raw["assessment_artifact_sha256"], "assessment_artifact_sha256"
        ),
        assessment_source_artifact_sha256=_nullable_sha(
            raw["assessment_source_artifact_sha256"],
            "assessment_source_artifact_sha256",
        ),
    )


def _retrieval(raw: object) -> RetrievalMetrics:
    if not isinstance(raw, dict) or set(raw) != _RETRIEVAL_FIELDS:
        raise ValueError("generation source retrieval has unexpected or missing fields")
    return RetrievalMetrics(
        evaluated_questions=_nonnegative_int(
            raw["evaluated_questions"], "evaluated_questions"
        ),
        missing_results=_nonnegative_int(raw["missing_results"], "missing_results"),
        recall_at_k=_rate(raw["recall_at_k"], "recall_at_k"),
        hit_rate_at_k=_rate(raw["hit_rate_at_k"], "hit_rate_at_k"),
        precision_at_k=_rate(raw["precision_at_k"], "precision_at_k"),
        mrr=_rate(raw["mrr"], "mrr"),
    )


def _records(
    raw: object, *, dataset: GoldenDataset, top_k: int
) -> tuple[EvaluationRecord, ...]:
    if not isinstance(raw, list) or len(raw) != len(dataset.questions):
        raise ValueError("generation source record count does not match the dataset")
    expected_counts = {
        question.question_id: len(question.expected_chunk_ids)
        for question in dataset.questions
    }
    records: list[EvaluationRecord] = []
    for value in raw:
        if not isinstance(value, dict) or set(value) != _RECORD_FIELDS:
            raise ValueError(
                "generation source record has unexpected or missing fields"
            )
        question_id = _identifier(value["question_id"], "record question_id")
        if question_id not in expected_counts:
            raise ValueError("generation source record question is outside the dataset")
        if (
            _positive_int(value["expected_chunk_count"], "expected_chunk_count")
            != expected_counts[question_id]
        ):
            raise ValueError("generation source expected chunk count is forged")
        retrieved = _nullable_nonnegative_int(
            value["retrieved_chunk_count"], "retrieved_chunk_count"
        )
        matched = _nullable_nonnegative_int(
            value["matched_expected_chunk_count"], "matched_expected_chunk_count"
        )
        first_rank = _nullable_positive_int(
            value["first_expected_rank"], "first_expected_rank"
        )
        if retrieved is None:
            if matched is not None or first_rank is not None:
                raise ValueError("generation source retrieval record is inconsistent")
        elif (
            retrieved > top_k
            or matched is None
            or matched > retrieved
            or matched > expected_counts[question_id]
        ):
            raise ValueError("generation source retrieval record is invalid")
        if (
            retrieved is not None
            and matched is not None
            and (
                (matched == 0 and first_rank is not None)
                or (matched > 0 and first_rank is None)
            )
        ):
            raise ValueError("generation source first expected rank is inconsistent")
        if first_rank is not None and (retrieved is None or first_rank > retrieved):
            raise ValueError("generation source first expected rank is invalid")
        input_tokens = _nullable_nonnegative_int(value["input_tokens"], "input_tokens")
        output_tokens = _nullable_nonnegative_int(
            value["output_tokens"], "output_tokens"
        )
        total_tokens = _nullable_nonnegative_int(value["total_tokens"], "total_tokens")
        if (input_tokens is None) != (output_tokens is None) or (
            input_tokens is None
        ) != (total_tokens is None):
            raise ValueError("generation source token record is inconsistent")
        if (
            total_tokens is not None
            and input_tokens is not None
            and output_tokens is not None
            and total_tokens != input_tokens + output_tokens
        ):
            raise ValueError("generation source token totals are inconsistent")
        claims = _nullable_nonnegative_int(value["claim_count"], "claim_count")
        unsupported = _nullable_nonnegative_int(
            value["unsupported_claim_count"], "unsupported_claim_count"
        )
        if (claims is None) != (unsupported is None) or (
            claims is not None and unsupported is not None and unsupported > claims
        ):
            raise ValueError("generation source claim record is inconsistent")
        records.append(
            EvaluationRecord(
                question_id=question_id,
                expected_chunk_count=expected_counts[question_id],
                retrieved_chunk_count=retrieved,
                matched_expected_chunk_count=matched,
                first_expected_rank=first_rank,
                latency_ms=_nullable_number(value["latency_ms"], "latency_ms"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                claim_count=claims,
                unsupported_claim_count=unsupported,
                failure_kind=_nullable_identifier(
                    value["failure_kind"], "failure_kind"
                ),
            )
        )
    if len({record.question_id for record in records}) != len(records):
        raise ValueError("generation source record question IDs must be unique")
    return tuple(records)


def _require_eligible_source(
    summary: EvaluationSummary, *, policy: RegressionPolicy
) -> None:
    provenance = summary.provenance
    if (
        not _is_eligible_generation_target(provenance)
        or not provenance.model_identity
        or summary.corpus_sha256 != APPROVED_LOCAL_OLLAMA_CORPUS_SHA256
        or policy.synthetic_corpus_sha256 != summary.corpus_sha256
        or provenance.policy_version != policy.policy_version
        or provenance.policy_sha256 != policy.sha256
        or summary.correctness is not None
        or summary.faithfulness is not None
        or summary.citation_accuracy is not None
        or provenance.calibration_id is not None
        or provenance.assessment_artifact_sha256 is not None
        or provenance.assessment_source_artifact_sha256 is not None
    ):
        raise ValueError(
            "generation source artifact is not an eligible unscored local replay"
        )


def _is_eligible_generation_target(provenance: RunProvenance) -> bool:
    """Allow only reviewed synthetic local-generation target identities."""

    return (provenance.target_id, provenance.target_version) in (
        _ELIGIBLE_GENERATION_TARGETS
    )


def _recompute_and_compare(
    summary: EvaluationSummary, *, dataset: GoldenDataset
) -> None:
    records = summary.records
    if len(records) != len(dataset.questions):
        raise ValueError("generation source coverage is incomplete")
    missing = sum(record.retrieved_chunk_count is None for record in records)
    recalls = [
        (record.matched_expected_chunk_count or 0) / record.expected_chunk_count
        for record in records
    ]
    hits = [
        1.0 if (record.matched_expected_chunk_count or 0) else 0.0 for record in records
    ]
    precisions = [
        (record.matched_expected_chunk_count or 0) / summary.top_k for record in records
    ]
    mrrs = [
        0.0 if record.first_expected_rank is None else 1.0 / record.first_expected_rank
        for record in records
    ]
    declared = summary.retrieval
    computed_retrieval = (
        len(records),
        missing,
        sum(recalls) / len(records),
        sum(hits) / len(records),
        sum(precisions) / len(records),
        sum(mrrs) / len(records),
    )
    if (
        declared.evaluated_questions != computed_retrieval[0]
        or declared.missing_results != computed_retrieval[1]
        or not all(
            _same_number(left, right)
            for left, right in zip(
                (
                    declared.recall_at_k,
                    declared.hit_rate_at_k,
                    declared.precision_at_k,
                    declared.mrr,
                ),
                computed_retrieval[2:],
                strict=True,
            )
        )
    ):
        raise ValueError("generation source retrieval aggregates do not match records")
    failures = sum(record.failure_kind is not None for record in records)
    usage = tuple(
        sum(getattr(record, field) or 0 for record in records)
        for field in ("input_tokens", "output_tokens", "total_tokens")
    )
    known_claims = [
        record
        for record in records
        if record.claim_count is not None and record.unsupported_claim_count is not None
    ]
    claims = sum(record.claim_count or 0 for record in known_claims)
    unsupported = sum(record.unsupported_claim_count or 0 for record in known_claims)
    unsupported_rate = None if claims == 0 else unsupported / claims
    latencies = [
        record.latency_ms for record in records if record.latency_ms is not None
    ]
    if (
        summary.failures != failures
        or not _same_number(summary.failure_rate, failures / len(records))
        or (summary.input_tokens, summary.output_tokens, summary.total_tokens) != usage
        or not _same_optional_number(summary.unsupported_claim_rate, unsupported_rate)
        or not _same_optional_number(
            summary.latency_p50_ms, percentile_nearest_rank(latencies, 50)
        )
        or not _same_optional_number(
            summary.latency_p95_ms, percentile_nearest_rank(latencies, 95)
        )
    ):
        raise ValueError("generation source aggregates do not match records")


def _require_source_binding(
    *, source: GenerationSourceArtifact, binding: AssessmentSource
) -> None:
    summary = source.summary
    provenance = summary.provenance
    values = (
        (binding.source_artifact_sha256, source.sha256),
        (binding.source_run_id, provenance.run_id),
        (binding.dataset_id, summary.dataset_id),
        (binding.dataset_version, summary.dataset_version),
        (binding.dataset_sha256, summary.dataset_sha256),
        (binding.corpus_sha256, summary.corpus_sha256),
        (binding.top_k, summary.top_k),
        (binding.target_id, provenance.target_id),
        (binding.target_version, provenance.target_version),
        (binding.model_identity, provenance.model_identity),
        (binding.configuration_sha256, provenance.configuration_sha256),
        (binding.policy_version, provenance.policy_version),
        (binding.policy_sha256, provenance.policy_sha256),
    )
    if any(left != right for left, right in values):
        raise ValueError(
            "assessment source binding does not match the validated artifact"
        )


def _complete_human_rate(assessments: tuple[Any, ...], field: str) -> float | None:
    values = tuple(getattr(assessment, field) for assessment in assessments)
    return None if any(value is None for value in values) else sum(values) / len(values)


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a bounded safe identifier")
    return value


def validate_safe_identifier(value: object, field: str) -> str:
    """Validate a content-free provenance identifier for producers and readers."""

    return _identifier(value, field)


def _nullable_identifier(value: object, field: str) -> str | None:
    return None if value is None else _identifier(value, field)


def _cost_reason(value: object, field: str) -> str | None:
    if value is None:
        return None
    if value not in {
        "no versioned provider pricing schedule supplied",
        "pricing is unavailable for one or more observed models",
    }:
        raise ValueError(f"{field} is not an approved content-free status")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lower-case SHA-256")
    return value


def _nullable_sha(value: object, field: str) -> str | None:
    return None if value is None else _sha(value, field)


def _positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _nullable_nonnegative_int(value: object, field: str) -> int | None:
    return None if value is None else _nonnegative_int(value, field)


def _nullable_positive_int(value: object, field: str) -> int | None:
    return None if value is None else _positive_int(value, field)


def _number(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field} must be a finite non-negative number")
    return float(value)


def _rate(value: object, field: str) -> float:
    result = _number(value, field)
    if result > 1:
        raise ValueError(f"{field} must be in [0, 1]")
    return result


def _nullable_rate(value: object, field: str) -> float | None:
    return None if value is None else _rate(value, field)


def _nullable_number(value: object, field: str) -> float | None:
    return None if value is None else _number(value, field)


def _same_number(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)


def _same_optional_number(left: float | None, right: float | None) -> bool:
    return (left is None and right is None) or (
        left is not None and right is not None and _same_number(left, right)
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _ensure_distinct_paths(paths: tuple[Path, ...]) -> None:
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if _paths_refer_to_same_file(left, right):
                raise ValueError("artifact paths must be distinct")


def _paths_refer_to_same_file(left: Path, right: Path) -> bool:
    """Detect equal, resolved-link, and existing hard-link artifact paths."""

    if left.resolve() == right.resolve():
        return True
    if not left.exists() or not right.exists():
        return False
    try:
        return left.samefile(right)
    except OSError as exc:
        raise ValueError("could not verify artifact paths are distinct") from exc
