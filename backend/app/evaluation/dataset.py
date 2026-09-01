"""Strict loader and fingerprinting for versioned golden datasets."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from backend.app.evaluation.models import (
    AssessmentBundle,
    AssessmentSource,
    GoldenDataset,
    GoldenQuestion,
    HumanAssessment,
)

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_ASSESSMENTS = 1_000


class DatasetValidationError(ValueError):
    """A golden dataset or human assessment is malformed or unsafe to run."""


def load_dataset(path: Path) -> GoldenDataset:
    """Load one schema-v1 synthetic/public-only dataset with reviewed questions."""

    raw = _load_json_object(path)
    _require_exact_keys(
        raw,
        {
            "schema_version",
            "dataset_id",
            "dataset_version",
            "source_policy",
            "questions",
        },
    )
    if raw["schema_version"] != 1:
        raise DatasetValidationError("unsupported dataset schema version")
    if raw["source_policy"] != "synthetic_or_public_only":
        raise DatasetValidationError("dataset source policy is not permitted")
    dataset_id = _non_empty_string(raw["dataset_id"], "dataset_id")
    dataset_version = _non_empty_string(raw["dataset_version"], "dataset_version")
    questions_raw = raw["questions"]
    if not isinstance(questions_raw, list) or not questions_raw:
        raise DatasetValidationError("questions must be a non-empty list")
    questions = tuple(_question(item) for item in questions_raw)
    ids = [question.question_id for question in questions]
    if len(set(ids)) != len(ids):
        raise DatasetValidationError("question IDs must be unique")
    return GoldenDataset(
        schema_version=1,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        source_policy="synthetic_or_public_only",
        questions=questions,
    )


def dataset_sha256(path: Path) -> str:
    """Return a source-file fingerprint so a result can identify its exact corpus."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_assessments(path: Path) -> AssessmentBundle:
    """Load the strict v2, content-free assessment envelope once from raw bytes."""

    try:
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise DatasetValidationError("could not load assessment JSON") from exc
    if not isinstance(raw, dict):
        raise DatasetValidationError("assessment JSON must be an object")
    _require_exact_keys(
        raw, {"schema_version", "calibration_set_id", "source", "assessments"}
    )
    if raw["schema_version"] != 2 or isinstance(raw["schema_version"], bool):
        raise DatasetValidationError("invalid assessment schema")
    calibration_set_id = _safe_identifier(
        raw["calibration_set_id"], "calibration_set_id"
    )
    source = _assessment_source(raw["source"])
    if (
        not isinstance(raw["assessments"], list)
        or not raw["assessments"]
        or len(raw["assessments"]) > _MAX_ASSESSMENTS
    ):
        raise DatasetValidationError("assessments must be a non-empty list")
    assessments: list[HumanAssessment] = []
    for item in raw["assessments"]:
        if not isinstance(item, dict):
            raise DatasetValidationError("assessment must be an object")
        _require_exact_keys(
            item,
            {
                "question_id",
                "correctness",
                "faithfulness",
                "citation_accuracy",
                "reviewer_id",
            },
        )
        values = (item["correctness"], item["faithfulness"], item["citation_accuracy"])
        if any(value is not None and not isinstance(value, bool) for value in values):
            raise DatasetValidationError(
                "assessment judgements must be booleans or null"
            )
        assessments.append(
            HumanAssessment(
                question_id=_safe_identifier(item["question_id"], "question_id"),
                correctness=item["correctness"],
                faithfulness=item["faithfulness"],
                citation_accuracy=item["citation_accuracy"],
                reviewer_id=_safe_identifier(item["reviewer_id"], "reviewer_id"),
            )
        )
    ids = [assessment.question_id for assessment in assessments]
    if len(set(ids)) != len(ids):
        raise DatasetValidationError("assessment question IDs must be unique")
    return AssessmentBundle(
        calibration_set_id=calibration_set_id,
        source=source,
        assessments=tuple(assessments),
        artifact_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


def _assessment_source(value: object) -> AssessmentSource:
    if not isinstance(value, dict):
        raise DatasetValidationError("assessment source must be an object")
    expected = {
        "source_artifact_sha256",
        "source_run_id",
        "dataset_id",
        "dataset_version",
        "dataset_sha256",
        "corpus_sha256",
        "top_k",
        "target_id",
        "target_version",
        "model_identity",
        "configuration_sha256",
        "policy_version",
        "policy_sha256",
    }
    _require_exact_keys(value, expected)
    if (
        not isinstance(value["top_k"], int)
        or isinstance(value["top_k"], bool)
        or value["top_k"] < 1
    ):
        raise DatasetValidationError(
            "assessment source top_k must be a positive integer"
        )
    sha_fields = {
        "source_artifact_sha256",
        "dataset_sha256",
        "corpus_sha256",
        "configuration_sha256",
        "policy_sha256",
    }
    for field in sha_fields:
        if not isinstance(value[field], str) or not _SHA256.fullmatch(value[field]):
            raise DatasetValidationError(f"assessment source {field} must be a SHA-256")
    return AssessmentSource(
        source_artifact_sha256=value["source_artifact_sha256"],
        source_run_id=_safe_identifier(value["source_run_id"], "source_run_id"),
        dataset_id=_safe_identifier(value["dataset_id"], "dataset_id"),
        dataset_version=_safe_identifier(value["dataset_version"], "dataset_version"),
        dataset_sha256=value["dataset_sha256"],
        corpus_sha256=value["corpus_sha256"],
        top_k=value["top_k"],
        target_id=_safe_identifier(value["target_id"], "target_id"),
        target_version=_safe_identifier(value["target_version"], "target_version"),
        model_identity=_safe_identifier(value["model_identity"], "model_identity"),
        configuration_sha256=value["configuration_sha256"],
        policy_version=_safe_identifier(value["policy_version"], "policy_version"),
        policy_sha256=value["policy_sha256"],
    )


def _question(item: object) -> GoldenQuestion:
    if not isinstance(item, dict):
        raise DatasetValidationError("question must be an object")
    _require_exact_keys(
        item,
        {
            "question_id",
            "question",
            "expected_chunk_ids",
            "provenance",
            "review_status",
            "review_note",
        },
    )
    expected = item["expected_chunk_ids"]
    if (
        not isinstance(expected, list)
        or not expected
        or not all(isinstance(value, str) and value.strip() for value in expected)
        or len(set(expected)) != len(expected)
    ):
        raise DatasetValidationError(
            "expected_chunk_ids must be a unique non-empty list"
        )
    if item["provenance"] not in {"synthetic", "public"}:
        raise DatasetValidationError("question provenance is not permitted")
    if item["review_status"] != "reviewed":
        raise DatasetValidationError("every golden question must be reviewed")
    return GoldenQuestion(
        question_id=_non_empty_string(item["question_id"], "question_id"),
        question=_non_empty_string(item["question"], "question"),
        expected_chunk_ids=tuple(expected),
        provenance=item["provenance"],
        review_status="reviewed",
        review_note=_non_empty_string(item["review_note"], "review_note"),
    )


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetValidationError("could not load evaluation JSON") from exc
    if not isinstance(value, dict):
        raise DatasetValidationError("evaluation JSON must be an object")
    return value


def _require_exact_keys(value: dict[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise DatasetValidationError("evaluation JSON has unexpected or missing fields")


def _non_empty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DatasetValidationError(f"{field} must be a non-empty string")
    return value


def _safe_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        raise DatasetValidationError(f"{field} must be a bounded safe identifier")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
