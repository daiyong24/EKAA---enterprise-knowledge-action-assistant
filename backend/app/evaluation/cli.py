"""Controlled local Stage 8 fixture runner; it has no public HTTP endpoint."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.app.core.config import Settings
from backend.app.evaluation.artifacts import (
    apply_assessment_bundle,
    load_generation_source_artifact,
    validate_safe_identifier,
    write_artifacts,
)
from backend.app.evaluation.corpus import (
    SyntheticCorpusRetrievalTarget,
    load_synthetic_corpus,
    validate_approved_local_ollama_corpus,
    validate_dataset_coverage,
)
from backend.app.evaluation.dataset import load_assessments, load_dataset
from backend.app.evaluation.local_generation import (
    LocalOllamaReadinessRecord,
    build_local_ollama_readiness_preflight,
    build_synthetic_ollama_generation_target,
)
from backend.app.evaluation.models import (
    EvaluationObservation,
    EvaluationScope,
    GoldenDataset,
    RunProvenance,
)
from backend.app.evaluation.regression import load_regression_policy
from backend.app.evaluation.review_packet import write_reviewer_packet
from backend.app.evaluation.runner import (
    EvaluationRunner,
    EvaluationTarget,
    StaticFixtureTarget,
    observation_from_fixture,
)

_LOCAL_OLLAMA_CORPUS = Path("docs/evaluation/synthetic_corpus_v1.json").resolve()


class _LocalOllamaReadinessError(RuntimeError):
    """Fail closed after a content-free local readiness outcome."""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a local, reproducible EKAA Stage 8 evaluation fixture."
    )
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument(
        "--local-ollama-preflight",
        action="store_true",
        help=(
            "Run exactly one content-free readiness call for the approved full "
            "synthetic local Ollama target. This mode never runs evaluation or "
            "writes artifacts."
        ),
    )
    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument(
        "--fixture",
        type=Path,
        help=(
            "Content-free local observations; this command never calls a provider "
            "or DB."
        ),
    )
    target_group.add_argument(
        "--synthetic-corpus",
        type=Path,
        help="Run the deterministic local corpus retrieval replay; no DB/provider.",
    )
    target_group.add_argument(
        "--synthetic-ollama-corpus",
        type=Path,
        help=(
            "Run the synthetic-only local Ollama generation replay. Requires "
            "--allow-local-ollama and never opens a DB."
        ),
    )
    parser.add_argument(
        "--allow-local-ollama",
        action="store_true",
        help=(
            "Explicitly permit provider calls to the configured loopback Ollama "
            "endpoint for this synthetic-only run."
        ),
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("docs/evaluation/baseline_policy_v1.json"),
    )
    parser.add_argument("--run-id")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-csv", type=Path)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--assessments", type=Path)
    parser.add_argument(
        "--assessment-source-artifact",
        type=Path,
        help="Validated, unscored full local-generation artifact for offline scoring.",
    )
    parser.add_argument(
        "--review-packet",
        type=Path,
        help=(
            "Write local synthetic reviewer material for this exact full Ollama "
            "replay. "
            "The path must be outside the repository."
        ),
    )
    parser.add_argument(
        "--reviewer-id",
        help="Bounded pseudonymous reviewer ID required with --review-packet.",
    )
    parser.add_argument(
        "--question-id",
        help=(
            "Run one reviewed question as a local Ollama smoke test. Requires "
            "--synthetic-ollama-corpus and --allow-local-ollama."
        ),
    )
    arguments = parser.parse_args()
    try:
        _validate_arguments(parser, arguments)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        if arguments.local_ollama_preflight:
            asyncio.run(_run_local_ollama_preflight(arguments))
            return 0
        summary = asyncio.run(_run(arguments))
    except _LocalOllamaReadinessError:
        parser.error("local readiness check failed")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    write_artifacts(
        summary=summary,
        json_path=arguments.output_json,
        csv_path=arguments.output_csv,
    )
    if arguments.review_packet is not None:
        target = getattr(arguments, "_review_target", None)
        if target is None or len(target.review_items) != len(summary.records):
            raise RuntimeError("review packet material was not captured")
        source_sha256 = hashlib.sha256(arguments.output_json.read_bytes()).hexdigest()
        write_reviewer_packet(
            path=arguments.review_packet,
            reviewer_id=arguments.reviewer_id,
            source_artifact_sha256=source_sha256,
            provenance=summary.provenance,
            items=target.review_items,
        )
    print(
        f"stage8_evaluation dataset={summary.dataset_id}@{summary.dataset_version} "
        f"questions={summary.retrieval.evaluated_questions} "
        f"recall_at_{summary.top_k}={summary.retrieval.recall_at_k:.4f}"
    )
    return 0


def _validate_arguments(
    parser: argparse.ArgumentParser, arguments: argparse.Namespace
) -> None:
    """Reject unsafe or mixed execution modes before settings or outputs exist."""

    preflight = arguments.local_ollama_preflight
    offline = (
        arguments.assessments is not None
        or arguments.assessment_source_artifact is not None
    )
    inputs = [arguments.dataset, arguments.policy]
    if preflight:
        if (
            arguments.output_json is not None
            or arguments.output_csv is not None
            or arguments.assessments is not None
            or arguments.assessment_source_artifact is not None
            or arguments.fixture is not None
            or arguments.synthetic_corpus is not None
            or arguments.synthetic_ollama_corpus is None
            or not arguments.allow_local_ollama
            or arguments.question_id is not None
            or arguments.review_packet is not None
            or arguments.reviewer_id is not None
        ):
            parser.error(
                "--local-ollama-preflight requires only the full approved local "
                "Ollama target and forbids outputs, scoring, smoke, and review "
                "material"
            )
        if not arguments.run_id:
            parser.error("--local-ollama-preflight requires --run-id")
        validate_safe_identifier(arguments.run_id, "run_id")
        inputs.append(arguments.synthetic_ollama_corpus)
    elif offline:
        if (
            arguments.assessments is None
            or arguments.assessment_source_artifact is None
        ):
            parser.error(
                "offline scoring requires both --assessments and "
                "--assessment-source-artifact"
            )
        if (
            any(
                value is not None
                for value in (
                    arguments.fixture,
                    arguments.synthetic_corpus,
                    arguments.synthetic_ollama_corpus,
                    arguments.question_id,
                    arguments.run_id,
                    arguments.review_packet,
                    arguments.reviewer_id,
                )
            )
            or arguments.allow_local_ollama
        ):
            parser.error(
                "offline scoring cannot be combined with a live target or --run-id"
            )
        inputs.extend((arguments.assessments, arguments.assessment_source_artifact))
    else:
        targets = (
            arguments.fixture,
            arguments.synthetic_corpus,
            arguments.synthetic_ollama_corpus,
        )
        if sum(value is not None for value in targets) != 1:
            parser.error("a live replay requires exactly one target")
        if not arguments.run_id:
            parser.error("a live replay requires --run-id")
        validate_safe_identifier(arguments.run_id, "run_id")
        inputs.extend(value for value in targets if value is not None)
    if not preflight and (
        arguments.output_json is None or arguments.output_csv is None
    ):
        parser.error("evaluation output requires both --output-json and --output-csv")
    if (arguments.review_packet is None) != (arguments.reviewer_id is None):
        parser.error("--review-packet and --reviewer-id must be provided together")
    if arguments.review_packet is not None:
        if (
            arguments.synthetic_ollama_corpus is None
            or not arguments.allow_local_ollama
            or arguments.question_id is not None
        ):
            parser.error(
                "--review-packet requires a full synthetic local Ollama replay"
            )
        validate_safe_identifier(arguments.reviewer_id, "reviewer_id")
        if arguments.review_packet.suffix.lower() != ".md":
            parser.error("--review-packet must be a Markdown file")
        if _is_within_workspace(arguments.review_packet):
            parser.error("--review-packet must be outside the repository")
    if (
        arguments.synthetic_ollama_corpus is not None
        and not arguments.allow_local_ollama
    ):
        parser.error("--synthetic-ollama-corpus requires --allow-local-ollama")
    if arguments.synthetic_ollama_corpus is None and arguments.allow_local_ollama:
        parser.error(
            "--allow-local-ollama is valid only with --synthetic-ollama-corpus"
        )
    if arguments.question_id is not None:
        if not arguments.question_id:
            parser.error("--question-id must not be empty")
        if (
            arguments.synthetic_ollama_corpus is None
            or not arguments.allow_local_ollama
        ):
            parser.error(
                "--question-id requires --synthetic-ollama-corpus "
                "and --allow-local-ollama"
            )
    if (
        arguments.synthetic_ollama_corpus is not None
        and arguments.synthetic_ollama_corpus.resolve() != _LOCAL_OLLAMA_CORPUS
    ):
        parser.error(
            "--synthetic-ollama-corpus accepts only the versioned Stage 8 fixture"
        )
    _validate_distinct_paths(
        tuple(
            path
            for path in (
                *inputs,
                None if preflight else arguments.output_json,
                None if preflight else arguments.output_csv,
                arguments.review_packet,
            )
            if path is not None
        )
    )


async def _run(arguments: argparse.Namespace) -> Any:
    dataset = load_dataset(arguments.dataset)
    policy = load_regression_policy(arguments.policy)
    if getattr(arguments, "assessments", None) is not None:
        source_path = getattr(arguments, "assessment_source_artifact", None)
        if source_path is None:
            raise ValueError("offline scoring requires an assessment source artifact")
        bundle = load_assessments(arguments.assessments)
        source = load_generation_source_artifact(
            source_path,
            dataset=dataset,
            dataset_path=arguments.dataset,
            policy=policy,
        )
        return apply_assessment_bundle(source=source, bundle=bundle, dataset=dataset)
    target: EvaluationTarget
    corpus_sha256: str | None
    evaluation_scope: EvaluationScope = "full_dataset"
    if (
        arguments.synthetic_corpus is not None
        or arguments.synthetic_ollama_corpus is not None
    ):
        corpus_path = arguments.synthetic_corpus or arguments.synthetic_ollama_corpus
        assert corpus_path is not None
        corpus = load_synthetic_corpus(corpus_path)
        validate_dataset_coverage(dataset, corpus)
        if arguments.synthetic_ollama_corpus is not None:
            validate_approved_local_ollama_corpus(
                corpus, policy_corpus_sha256=policy.synthetic_corpus_sha256
            )
            if arguments.question_id is not None:
                dataset = _single_question_dataset(dataset, arguments.question_id)
                evaluation_scope = "single_question_smoke"
            target = build_synthetic_ollama_generation_target(
                corpus=corpus,
                settings=Settings(),
                policy_version=policy.policy_version,
                policy_sha256=policy.sha256,
                run_id=arguments.run_id,
                top_k=arguments.top_k,
                policy_corpus_sha256=policy.synthetic_corpus_sha256,
                readiness_required=arguments.question_id is None,
                capture_review_material=getattr(arguments, "review_packet", None)
                is not None,
            )
            if arguments.question_id is None:
                readiness = await target.prepare_for_full_run()
                _print_local_ollama_readiness(readiness)
                if readiness.outcome != "ready":
                    raise _LocalOllamaReadinessError()
            if getattr(arguments, "review_packet", None) is not None:
                arguments._review_target = target
        else:
            target = SyntheticCorpusRetrievalTarget(
                corpus=corpus,
                policy_version=policy.policy_version,
                policy_sha256=policy.sha256,
                run_id=arguments.run_id,
            )
        corpus_sha256 = corpus.sha256
    else:
        fixture = _load_fixture(arguments.fixture)
        observations = tuple(map(observation_from_fixture, fixture))
        _validate_fixture_ids(dataset, observations)
        target = StaticFixtureTarget(
            {value.question_id: value for value in observations},
            RunProvenance(
                target_id="static-metric-plumbing-fixture",
                target_version="1.0.0",
                model_identity=None,
                configuration_sha256=hashlib.sha256(
                    arguments.fixture.read_bytes()
                ).hexdigest(),
                run_id=arguments.run_id,
                calibration_id=None,
                policy_version=policy.policy_version,
                policy_sha256=policy.sha256,
            ),
        )
        corpus_sha256 = None
    return await EvaluationRunner(target=target).run(
        dataset=dataset,
        dataset_path=arguments.dataset,
        policy=policy,
        top_k=arguments.top_k,
        evaluation_scope=evaluation_scope,
        corpus_sha256=corpus_sha256,
    )


async def _run_local_ollama_preflight(arguments: argparse.Namespace) -> None:
    """Run one approved readiness call without retrieval, runner, or artifacts."""

    dataset = load_dataset(arguments.dataset)
    policy = load_regression_policy(arguments.policy)
    corpus_path = arguments.synthetic_ollama_corpus
    assert corpus_path is not None
    corpus = load_synthetic_corpus(corpus_path)
    validate_dataset_coverage(dataset, corpus)
    validate_approved_local_ollama_corpus(
        corpus, policy_corpus_sha256=policy.synthetic_corpus_sha256
    )
    preflight = build_local_ollama_readiness_preflight(
        settings=Settings(),
        run_id=arguments.run_id,
        top_k=arguments.top_k,
    )
    readiness = await preflight.prepare()
    _print_local_ollama_readiness(readiness)
    if readiness.outcome != "ready":
        raise _LocalOllamaReadinessError()


def _print_local_ollama_readiness(record: LocalOllamaReadinessRecord) -> None:
    """Emit the only approved content-free readiness record."""

    print(record.render())


def _load_fixture(path: Path) -> list[dict[str, object]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"schema_version", "observations"}:
        raise ValueError("fixture must contain only schema_version and observations")
    observations = value["observations"]
    if value["schema_version"] != 1 or not isinstance(observations, list):
        raise ValueError("fixture schema is invalid")
    if not all(isinstance(observation, dict) for observation in observations):
        raise ValueError("fixture observations must be objects")
    return observations


def _validate_fixture_ids(
    dataset: GoldenDataset,
    observations: tuple[EvaluationObservation, ...],
) -> None:
    fixture_ids = [observation.question_id for observation in observations]
    if len(set(fixture_ids)) != len(fixture_ids):
        raise ValueError("fixture question IDs must be unique")
    dataset_ids = {question.question_id for question in dataset.questions}
    if set(fixture_ids) != dataset_ids:
        raise ValueError("fixture IDs must exactly match the dataset")


def _single_question_dataset(dataset: GoldenDataset, question_id: str) -> GoldenDataset:
    """Select one reviewed question only after full dataset/corpus validation."""

    if not question_id:
        raise ValueError("question ID must not be empty")
    questions = tuple(
        question
        for question in dataset.questions
        if question.question_id == question_id
    )
    if not questions:
        raise ValueError("question ID is not present in the reviewed dataset")
    return GoldenDataset(
        schema_version=dataset.schema_version,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        source_policy=dataset.source_policy,
        questions=questions,
    )


def _validate_distinct_paths(paths: tuple[Path, ...]) -> None:
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if _paths_refer_to_same_file(left, right):
                raise ValueError(
                    "all evaluation input and output paths must be distinct"
                )


def _paths_refer_to_same_file(left: Path, right: Path) -> bool:
    """Detect equal, resolved-link, and existing hard-link CLI paths."""

    if left.resolve() == right.resolve():
        return True
    if not left.exists() or not right.exists():
        return False
    try:
        return left.samefile(right)
    except OSError as exc:
        raise ValueError("could not verify evaluation paths are distinct") from exc


def _is_within_workspace(path: Path) -> bool:
    """Keep content-bearing review material out of the repository tree."""

    try:
        path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
