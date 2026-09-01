"""Replay runner and safe artifact writer for versioned RAG evaluation."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from backend.app.evaluation.dataset import dataset_sha256
from backend.app.evaluation.metrics import (
    percentile_nearest_rank,
    retrieval_metrics,
    to_record,
    unsupported_claim_rate,
)
from backend.app.evaluation.models import (
    EvaluationObservation,
    EvaluationScope,
    EvaluationSummary,
    GoldenDataset,
    GoldenQuestion,
    RunProvenance,
)
from backend.app.evaluation.regression import RegressionPolicy
from backend.app.llm.models import TokenUsage
from backend.app.rag.models import RagAnswerResult

Clock = Callable[[], float]
_DEFAULT_TEST_PROVENANCE = RunProvenance(
    target_id="static-metric-plumbing-fixture",
    target_version="1.0.0",
    model_identity=None,
    configuration_sha256="0" * 64,
    run_id="library-test-run",
    calibration_id=None,
    policy_version="test-policy",
    policy_sha256="test-policy-sha",
)


class EvaluationTarget(Protocol):
    """A testable boundary which may be backed by retrieval/RAG or a local fixture."""

    provenance: RunProvenance

    async def evaluate(self, question: GoldenQuestion) -> EvaluationObservation: ...


class RagAnswerService(Protocol):
    """Existing RAG seam used only by an explicitly injected evaluation target."""

    async def answer(self, question: str) -> RagAnswerResult: ...


class RagGenerationEvaluationTarget:
    """Adapt generation measurements while keeping retrieval observations separate.

    This is intentionally not wired to the CLI. A caller must explicitly inject an
    approved service/test fake. It deliberately returns no retrieval IDs: final
    citations are not a candidate-ranking result and must never score Recall/MRR.
    """

    def __init__(
        self,
        service: RagAnswerService,
        provenance: RunProvenance = _DEFAULT_TEST_PROVENANCE,
    ) -> None:
        self._service = service
        self.provenance = provenance

    async def evaluate(self, question: GoldenQuestion) -> EvaluationObservation:
        result = await self._service.answer(question.question)
        return EvaluationObservation(
            question_id=question.question_id,
            retrieved_chunk_ids=None,
            model=result.model,
            latency_ms=None,
            usage=result.usage,
            claim_count=len(result.claims),
            unsupported_claim_count=sum(
                claim.status == "unsupported" for claim in result.claims
            ),
            failure_kind=None,
        )


class StaticFixtureTarget:
    """Local-only target for deterministic fixture replay; never calls a provider/DB."""

    def __init__(
        self,
        observations: Mapping[str, EvaluationObservation],
        provenance: RunProvenance = _DEFAULT_TEST_PROVENANCE,
    ) -> None:
        self._observations = dict(observations)
        self.provenance = provenance

    async def evaluate(self, question: GoldenQuestion) -> EvaluationObservation:
        try:
            return self._observations[question.question_id]
        except KeyError as exc:
            raise KeyError("fixture has no result for question") from exc


class EvaluationRunner:
    """Runs each versioned question once, in file order, with explicit failure rows."""

    def __init__(
        self, *, target: EvaluationTarget, clock: Clock = time.perf_counter
    ) -> None:
        self._target = target
        self._clock = clock

    async def run(
        self,
        *,
        dataset: GoldenDataset,
        dataset_path: Path,
        policy: RegressionPolicy,
        top_k: int,
        evaluation_scope: EvaluationScope = "full_dataset",
        corpus_sha256: str | None = None,
        cost_per_1k_tokens_usd: Mapping[str, tuple[float, float]] | None = None,
    ) -> EvaluationSummary:
        """Produce a reproducible aggregate, never making absent measures look good."""

        if top_k < 1:
            raise ValueError("top_k must be positive")
        if evaluation_scope not in {"full_dataset", "single_question_smoke"}:
            raise ValueError("evaluation scope is invalid")
        # Live evaluation output is deliberately unassessed. Human scores are applied
        # only by the offline, source-bound scoring command.
        if (
            dataset.dataset_id != policy.dataset_id
            or dataset.dataset_version != policy.dataset_version
            or self._target.provenance.policy_version != policy.policy_version
            or self._target.provenance.policy_sha256 != policy.sha256
        ):
            raise ValueError("dataset, target, and loaded policy must match")
        observations = tuple(
            [await self._evaluate_one(question) for question in dataset.questions]
        )
        records = tuple(
            to_record(
                observation=observation,
                expected_chunk_ids=question.expected_chunk_ids,
                top_k=top_k,
            )
            for observation, question in zip(
                observations, dataset.questions, strict=True
            )
        )
        retrieval = retrieval_metrics(
            observations=observations,
            expected_chunk_ids=tuple(
                question.expected_chunk_ids for question in dataset.questions
            ),
            top_k=top_k,
        )
        cost, reason = _estimate_cost(observations, cost_per_1k_tokens_usd)
        latencies = [
            observation.latency_ms
            for observation in observations
            if observation.latency_ms is not None
        ]
        total_usage = _usage_total(observations)
        failures = sum(
            observation.failure_kind is not None for observation in observations
        )
        return EvaluationSummary(
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.dataset_version,
            dataset_sha256=dataset_sha256(dataset_path),
            corpus_sha256=corpus_sha256,
            top_k=top_k,
            evaluation_scope=evaluation_scope,
            provenance=self._target.provenance,
            retrieval=retrieval,
            correctness=None,
            faithfulness=None,
            citation_accuracy=None,
            unsupported_claim_rate=unsupported_claim_rate(observations),
            latency_p50_ms=percentile_nearest_rank(latencies, 50),
            latency_p95_ms=percentile_nearest_rank(latencies, 95),
            input_tokens=total_usage.input_tokens,
            output_tokens=total_usage.output_tokens,
            total_tokens=total_usage.total_tokens,
            failures=failures,
            failure_rate=failures / len(observations) if observations else 0.0,
            estimated_cost_usd=cost,
            cost_unavailable_reason=reason,
            records=records,
        )

    async def _evaluate_one(self, question: GoldenQuestion) -> EvaluationObservation:
        start = self._clock()
        try:
            observation = await self._target.evaluate(question)
        except Exception as exc:  # Target detail is not stored in an artifact.
            return EvaluationObservation(
                question_id=question.question_id,
                retrieved_chunk_ids=None,
                model=None,
                latency_ms=(self._clock() - start) * 1_000,
                usage=None,
                claim_count=None,
                unsupported_claim_count=None,
                failure_kind=type(exc).__name__,
            )
        if observation.question_id != question.question_id:
            return EvaluationObservation(
                question_id=question.question_id,
                retrieved_chunk_ids=None,
                model=None,
                latency_ms=(self._clock() - start) * 1_000,
                usage=None,
                claim_count=None,
                unsupported_claim_count=None,
                failure_kind="question_id_mismatch",
            )
        return EvaluationObservation(
            question_id=observation.question_id,
            retrieved_chunk_ids=observation.retrieved_chunk_ids,
            model=observation.model,
            latency_ms=(self._clock() - start) * 1_000,
            usage=observation.usage,
            claim_count=observation.claim_count,
            unsupported_claim_count=observation.unsupported_claim_count,
            failure_kind=observation.failure_kind,
        )


def observation_from_fixture(value: Mapping[str, object]) -> EvaluationObservation:
    """Validate content-free fixture output supplied to the controlled CLI."""

    expected = {
        "question_id",
        "retrieved_chunk_ids",
        "model",
        "usage",
        "claim_count",
        "unsupported_claim_count",
        "failure_kind",
    }
    if set(value) != expected:
        raise ValueError("fixture observation has unexpected or missing fields")
    question_id = value["question_id"]
    if not isinstance(question_id, str) or not question_id:
        raise ValueError("fixture question_id is invalid")
    identifiers = value["retrieved_chunk_ids"]
    if identifiers is not None and (
        not isinstance(identifiers, list)
        or not all(
            isinstance(identifier, str) and identifier for identifier in identifiers
        )
    ):
        raise ValueError("fixture retrieved_chunk_ids is invalid")
    usage_raw = value["usage"]
    usage = None
    if usage_raw is not None:
        if (
            not isinstance(usage_raw, dict)
            or set(usage_raw)
            != {
                "input_tokens",
                "output_tokens",
                "total_tokens",
            }
            or not all(
                isinstance(token, int) and token >= 0 for token in usage_raw.values()
            )
        ):
            raise ValueError("fixture usage is invalid")
        usage = TokenUsage(
            input_tokens=usage_raw["input_tokens"],
            output_tokens=usage_raw["output_tokens"],
            total_tokens=usage_raw["total_tokens"],
        )
    claim_count = value["claim_count"]
    unsupported = value["unsupported_claim_count"]
    if (
        claim_count is not None
        and (not isinstance(claim_count, int) or claim_count < 0)
    ) or (
        unsupported is not None
        and (not isinstance(unsupported, int) or unsupported < 0)
    ):
        raise ValueError("fixture claim counts are invalid")
    if (claim_count is None) != (unsupported is None) or (
        isinstance(claim_count, int)
        and isinstance(unsupported, int)
        and unsupported > claim_count
    ):
        raise ValueError("fixture claim counts are inconsistent")
    model = value["model"]
    failure_kind = value["failure_kind"]
    if model is not None and not isinstance(model, str):
        raise ValueError("fixture model is invalid")
    if failure_kind is not None and not isinstance(failure_kind, str):
        raise ValueError("fixture failure_kind is invalid")
    return EvaluationObservation(
        question_id=question_id,
        retrieved_chunk_ids=tuple(identifiers)
        if isinstance(identifiers, list)
        else None,
        model=model,
        latency_ms=None,
        usage=usage,
        claim_count=claim_count,
        unsupported_claim_count=unsupported,
        failure_kind=failure_kind,
    )


def _usage_total(observations: tuple[EvaluationObservation, ...]) -> TokenUsage:
    return TokenUsage(
        input_tokens=sum(
            observation.usage.input_tokens
            for observation in observations
            if observation.usage is not None
        ),
        output_tokens=sum(
            observation.usage.output_tokens
            for observation in observations
            if observation.usage is not None
        ),
        total_tokens=sum(
            observation.usage.total_tokens
            for observation in observations
            if observation.usage is not None
        ),
    )


def _estimate_cost(
    observations: tuple[EvaluationObservation, ...],
    schedule: Mapping[str, tuple[float, float]] | None,
) -> tuple[float | None, str | None]:
    if not schedule:
        return None, "no versioned provider pricing schedule supplied"
    total = 0.0
    for observation in observations:
        if observation.usage is None or observation.model is None:
            continue
        rates = schedule.get(observation.model)
        if rates is None:
            return None, "pricing is unavailable for one or more observed models"
        input_rate, output_rate = rates
        total += (
            observation.usage.input_tokens * input_rate
            + observation.usage.output_tokens * output_rate
        ) / 1_000
    return total, None
