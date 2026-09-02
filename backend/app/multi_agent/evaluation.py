"""Stage 19 comparison harness against the Stage 14 evaluation contract."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from backend.app.agent_evaluation.models import BatchEvaluation, Scenario
from backend.app.agent_evaluation.scoring import scenario_suite_fingerprint


def _validated_batch(value: BatchEvaluation) -> BatchEvaluation:
    """Revalidate model-constructed artifacts at every trust boundary."""
    try:
        return BatchEvaluation.model_validate(value.model_dump())
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise ValueError("evaluation artifact is invalid") from exc


def _validated_manifest(
    scenarios: Sequence[Scenario],
) -> tuple[tuple[Scenario, ...], str, frozenset[str]]:
    """Normalize an authoritative scenario manifest and derive its fingerprint."""
    try:
        normalized = tuple(
            Scenario.model_validate(scenario.model_dump()) for scenario in scenarios
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise ValueError("authoritative scenario manifest is invalid") from exc
    ids = [scenario.scenario_id for scenario in normalized]
    if not normalized or len(set(ids)) != len(ids):
        raise ValueError("authoritative scenario manifest must be unique and non-empty")
    return normalized, scenario_suite_fingerprint(normalized), frozenset(ids)


def _assert_manifest_binding(
    artifact: BatchEvaluation,
    *,
    fingerprint: str,
    scenario_ids: frozenset[str],
) -> None:
    """Reject caller-overwritten provenance at the authoritative trust boundary."""
    if artifact.dataset_fingerprint != fingerprint:
        raise ValueError("evaluation artifact fingerprint does not match manifest")
    if {result.scenario_id for result in artifact.results} != scenario_ids:
        raise ValueError("evaluation artifact scenarios do not match manifest")


class StatisticalEvidence(BaseModel):
    """Auditable powered-analysis artifact required before candidate adoption."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    analysis_id: str = Field(min_length=1, max_length=128)
    method: Literal["paired_permutation", "paired_bootstrap"]
    replicate_ids: tuple[str, ...] = Field(min_length=3, max_length=100)
    evaluation_version: str = Field(min_length=1, max_length=64)
    corpus_version: str = Field(min_length=1, max_length=64)
    dataset_fingerprint: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    power_target: float = Field(ge=0.8, le=1)
    achieved_power: float = Field(ge=0, le=1)
    p_value: float = Field(ge=0, le=1)
    observed_success_delta: float
    authoritative: bool
    independently_verified: bool

    @field_validator(
        "power_target", "achieved_power", "p_value", "observed_success_delta"
    )
    @classmethod
    def finite_metrics(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("statistical metric must be finite")
        return value

    @model_validator(mode="after")
    def complete_and_powered(self) -> StatisticalEvidence:
        if len(set(self.replicate_ids)) != len(self.replicate_ids):
            raise ValueError("statistical replicate IDs must be unique")
        if self.achieved_power < self.power_target:
            raise ValueError("statistical evidence is underpowered")
        if self.p_value > 0.05:
            raise ValueError("statistical evidence is not significant")
        return self


class ReplicatePair(BaseModel):
    """One complete, matched baseline/candidate evaluation artifact pair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    replicate_id: str = Field(min_length=1, max_length=128)
    baseline: BatchEvaluation
    candidate: BatchEvaluation

    @model_validator(mode="after")
    def matched(self) -> ReplicatePair:
        _validated_batch(self.baseline)
        _validated_batch(self.candidate)
        if self.baseline.scenario_count != self.candidate.scenario_count:
            raise ValueError("replicate suites must have equal size")
        baseline_ids = {item.scenario_id for item in self.baseline.results}
        candidate_ids = {item.scenario_id for item in self.candidate.results}
        if baseline_ids != candidate_ids:
            raise ValueError("replicate scenario IDs must match exactly")
        if (
            self.baseline.evaluation_version is None
            or self.candidate.evaluation_version is None
            or self.baseline.evaluation_version != self.candidate.evaluation_version
            or self.baseline.corpus_version is None
            or self.candidate.corpus_version is None
            or self.baseline.corpus_version != self.candidate.corpus_version
            or self.baseline.dataset_fingerprint is None
            or self.candidate.dataset_fingerprint is None
            or self.baseline.dataset_fingerprint != self.candidate.dataset_fingerprint
        ):
            raise ValueError("replicate provenance bindings are required")
        return self

    def validate_against_manifest(self, scenarios: Sequence[Scenario]) -> ReplicatePair:
        """Validate this pair against scenario content supplied by a trusted caller."""
        _, fingerprint, scenario_ids = _validated_manifest(scenarios)
        _assert_manifest_binding(
            _validated_batch(self.baseline),
            fingerprint=fingerprint,
            scenario_ids=scenario_ids,
        )
        _assert_manifest_binding(
            _validated_batch(self.candidate),
            fingerprint=fingerprint,
            scenario_ids=scenario_ids,
        )
        return self


class ReplicateBundle(BaseModel):
    """Verifiable evidence: IDs and complete artifacts, not a caller count."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle_id: str = Field(min_length=1, max_length=128)
    baseline: BatchEvaluation
    candidate: BatchEvaluation
    replicates: tuple[ReplicatePair, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def complete_and_consistent(self) -> ReplicateBundle:
        top_baseline = _validated_batch(self.baseline)
        top_candidate = _validated_batch(self.candidate)
        ids = [item.replicate_id for item in self.replicates]
        if len(set(ids)) != len(ids):
            raise ValueError("replicate IDs must be unique")
        baseline_run_ids = [item.baseline.run_id for item in self.replicates]
        candidate_run_ids = [item.candidate.run_id for item in self.replicates]
        if len(set(baseline_run_ids)) != len(baseline_run_ids) or len(
            set(candidate_run_ids)
        ) != len(candidate_run_ids):
            raise ValueError("replicate artifact run IDs must be unique")
        first = self.replicates[0]
        first_ids = {result.scenario_id for result in first.baseline.results}
        top_baseline_ids = {result.scenario_id for result in top_baseline.results}
        top_candidate_ids = {result.scenario_id for result in top_candidate.results}
        if (
            first.baseline.run_id != top_baseline.run_id
            or first.candidate.run_id != top_candidate.run_id
        ):
            raise ValueError("top-level artifact IDs must bind to first replicate")
        if first_ids != top_baseline_ids or first_ids != top_candidate_ids:
            raise ValueError("top-level scenario IDs must match replicates")
        for side, top in (
            (first.baseline, top_baseline),
            (first.candidate, top_candidate),
        ):
            for field in (
                "evaluation_version",
                "corpus_version",
                "dataset_fingerprint",
            ):
                if getattr(side, field) != getattr(top, field):
                    raise ValueError("top-level provenance must bind all replicates")
        for item in self.replicates[1:]:
            for field in (
                "evaluation_version",
                "corpus_version",
                "dataset_fingerprint",
            ):
                if getattr(item.baseline, field) != getattr(first.baseline, field):
                    raise ValueError("replicate provenance must match")
                if getattr(item.candidate, field) != getattr(first.candidate, field):
                    raise ValueError("replicate provenance must match")
            for side, top in (
                (item.baseline, top_baseline),
                (item.candidate, top_candidate),
            ):
                for field in (
                    "evaluation_version",
                    "corpus_version",
                    "dataset_fingerprint",
                ):
                    if getattr(side, field) != getattr(top, field):
                        raise ValueError(
                            "top-level provenance must bind all replicates"
                        )
            if {result.scenario_id for result in item.baseline.results} != first_ids:
                raise ValueError("replicate scenario IDs must match exactly")
        if len(self.replicates) < 3:
            raise ValueError("at least three complete replicates are required")
        return self

    def validate_against_manifest(
        self, scenarios: Sequence[Scenario]
    ) -> ReplicateBundle:
        """Validate every bound artifact against authoritative scenario content."""
        _, fingerprint, scenario_ids = _validated_manifest(scenarios)
        for artifact in (
            self.baseline,
            self.candidate,
            *(pair.baseline for pair in self.replicates),
            *(pair.candidate for pair in self.replicates),
        ):
            _assert_manifest_binding(
                _validated_batch(artifact),
                fingerprint=fingerprint,
                scenario_ids=scenario_ids,
            )
        return self


class ComparisonDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    baseline_run_id: str
    candidate_run_id: str
    adopt_multi_agent: bool
    decision: Literal["retain_single_agent", "adopt_multi_agent"]
    reason_code: str
    success_delta: float
    latency_ratio: float
    step_ratio: float
    cost_delta: float | None = None
    replicate_count: int = Field(ge=0)
    replicate_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def consistent(self) -> ComparisonDecision:
        if self.adopt_multi_agent != (self.decision == "adopt_multi_agent"):
            raise ValueError("decision flag is inconsistent")
        if self.replicate_count != len(self.replicate_ids):
            raise ValueError("replicate count must be derived from replicate IDs")
        for value in (self.success_delta, self.latency_ratio, self.step_ratio):
            if not math.isfinite(value):
                raise ValueError("comparison metric must be finite")
        if self.cost_delta is not None and not math.isfinite(self.cost_delta):
            raise ValueError("cost delta must be finite")
        return self


def _decision(
    baseline: BatchEvaluation,
    candidate: BatchEvaluation,
    *,
    success_delta: float,
    latency_ratio: float,
    step_ratio: float,
    cost_delta: float | None,
    replicate_ids: tuple[str, ...],
    reasons: list[str],
) -> ComparisonDecision:
    adopt = not reasons
    return ComparisonDecision(
        baseline_run_id=baseline.run_id,
        candidate_run_id=candidate.run_id,
        adopt_multi_agent=adopt,
        decision="adopt_multi_agent" if adopt else "retain_single_agent",
        reason_code="adopted" if adopt else "+".join(reasons),
        success_delta=success_delta,
        latency_ratio=latency_ratio,
        step_ratio=step_ratio,
        cost_delta=cost_delta,
        replicate_count=len(replicate_ids),
        replicate_ids=replicate_ids,
    )


def compare_stage14_baseline(
    baseline: BatchEvaluation,
    candidate: BatchEvaluation,
    *,
    minimum_success_delta: float = 0.05,
    maximum_latency_ratio: float = 1.25,
    maximum_step_ratio: float = 1.50,
    maximum_cost_ratio: float = 1.25,
    replicate_bundle: ReplicateBundle | None = None,
    authoritative_scenarios: Sequence[Scenario] | None = None,
    statistical_evidence: StatisticalEvidence | None = None,
) -> ComparisonDecision:
    """Adopt only from powered, verifiable replicates with bounded overhead."""
    baseline = _validated_batch(baseline)
    candidate = _validated_batch(candidate)
    if minimum_success_delta <= 0 or not math.isfinite(minimum_success_delta):
        raise ValueError("minimum success delta must be positive")
    if minimum_success_delta > 1:
        raise ValueError("success delta threshold is invalid")
    if (
        maximum_latency_ratio <= 0
        or maximum_step_ratio <= 0
        or maximum_cost_ratio <= 0
        or not math.isfinite(maximum_latency_ratio)
        or not math.isfinite(maximum_step_ratio)
        or not math.isfinite(maximum_cost_ratio)
    ):
        raise ValueError("operational thresholds must be positive")
    baseline_ids = {item.scenario_id for item in baseline.results}
    candidate_ids = {item.scenario_id for item in candidate.results}
    if baseline_ids != candidate_ids:
        raise ValueError("baseline and candidate scenario IDs must match exactly")

    if authoritative_scenarios is None:
        return _decision(
            baseline,
            candidate,
            success_delta=candidate.success_rate - baseline.success_rate,
            latency_ratio=candidate.mean_latency_ms
            / max(1e-9, baseline.mean_latency_ms),
            step_ratio=candidate.mean_steps / max(1e-9, baseline.mean_steps),
            cost_delta=None,
            replicate_ids=(),
            reasons=[
                "replicate_evidence_unavailable"
                if replicate_bundle is None
                else "authoritative_manifest_unavailable"
            ],
        )

    _, manifest_fingerprint, manifest_ids = _validated_manifest(authoritative_scenarios)
    if baseline_ids != manifest_ids:
        raise ValueError("authoritative manifest scenario IDs must match artifacts")
    _assert_manifest_binding(
        baseline, fingerprint=manifest_fingerprint, scenario_ids=manifest_ids
    )
    _assert_manifest_binding(
        candidate, fingerprint=manifest_fingerprint, scenario_ids=manifest_ids
    )

    if replicate_bundle is None:
        return _decision(
            baseline,
            candidate,
            success_delta=candidate.success_rate - baseline.success_rate,
            latency_ratio=candidate.mean_latency_ms
            / max(1e-9, baseline.mean_latency_ms),
            step_ratio=candidate.mean_steps / max(1e-9, baseline.mean_steps),
            cost_delta=None,
            replicate_ids=(),
            reasons=["replicate_evidence_unavailable"],
        )

    try:
        replicate_bundle = ReplicateBundle.model_validate(replicate_bundle.model_dump())
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise ValueError("replicate bundle is invalid") from exc
    if (
        replicate_bundle.baseline.run_id != baseline.run_id
        or replicate_bundle.candidate.run_id != candidate.run_id
    ):
        raise ValueError("top-level artifacts do not match replicate bundle")
    replicate_bundle.validate_against_manifest(authoritative_scenarios)
    pairs = replicate_bundle.replicates
    for pair in pairs:
        if pair.baseline.scenario_count != baseline.scenario_count:
            raise ValueError("replicate does not match declared baseline")
        if {r.scenario_id for r in pair.baseline.results} != baseline_ids:
            raise ValueError("replicate scenario IDs must match exactly")

    def mean(field: str, side: str) -> float:
        return sum(float(getattr(getattr(pair, side), field)) for pair in pairs) / len(
            pairs
        )

    success_delta = mean("success_rate", "candidate") - mean("success_rate", "baseline")
    latency_ratio = mean("mean_latency_ms", "candidate") / max(
        1e-9, mean("mean_latency_ms", "baseline")
    )
    step_ratio = mean("mean_steps", "candidate") / max(
        1e-9, mean("mean_steps", "baseline")
    )
    tool_accuracy_baseline = mean("mean_tool_accuracy", "baseline")
    tool_accuracy_candidate = mean("mean_tool_accuracy", "candidate")
    argument_accuracy_baseline = mean("mean_argument_accuracy", "baseline")
    argument_accuracy_candidate = mean("mean_argument_accuracy", "candidate")

    def mean_result_boolean(side: str, field: str) -> float:
        """Derive a correctness rate from every result, not a caller summary."""
        values = [
            float(getattr(result, field))
            for pair in pairs
            for result in getattr(pair, side).results
        ]
        return sum(values) / len(values)

    escalation_baseline = mean_result_boolean("baseline", "escalation_correct")
    escalation_candidate = mean_result_boolean("candidate", "escalation_correct")
    all_cost_available = all(
        pair.baseline.cost_available and pair.candidate.cost_available for pair in pairs
    )
    cost_delta = None
    if all_cost_available:
        cost_delta = mean("estimated_cost", "candidate") - mean(
            "estimated_cost", "baseline"
        )
        baseline_cost = mean("estimated_cost", "baseline")
        candidate_cost = mean("estimated_cost", "candidate")
        cost_ratio = (
            candidate_cost / baseline_cost
            if baseline_cost > 0
            else (1 if candidate_cost == 0 else math.inf)
        )
    else:
        cost_ratio = math.inf
    reasons: list[str] = []
    if success_delta < minimum_success_delta or success_delta <= 0:
        reasons.append("benefit_below_threshold")
    if latency_ratio > maximum_latency_ratio:
        reasons.append("latency_overhead")
    if step_ratio > maximum_step_ratio:
        reasons.append("step_overhead")
    if tool_accuracy_candidate < tool_accuracy_baseline:
        reasons.append("tool_accuracy_regressed")
    if argument_accuracy_candidate < argument_accuracy_baseline:
        reasons.append("argument_accuracy_regressed")
    if mean("mean_unnecessary_calls", "candidate") > mean(
        "mean_unnecessary_calls", "baseline"
    ):
        reasons.append("unnecessary_calls_regressed")
    if escalation_candidate < escalation_baseline:
        reasons.append("escalation_correctness_regressed")
    if any(pair.candidate.approval_compliance_rate < 1 for pair in pairs):
        reasons.append("approval_compliance_failed")
    if cost_ratio > maximum_cost_ratio:
        reasons.append(
            "cost_unavailable" if not all_cost_available else "cost_overhead"
        )
    if statistical_evidence is None:
        reasons.append("statistical_evidence_unavailable")
    else:
        try:
            evidence = StatisticalEvidence.model_validate(
                statistical_evidence.model_dump()
            )
        except (AttributeError, TypeError, ValidationError, ValueError) as exc:
            raise ValueError("statistical evidence is invalid") from exc
        pair_ids = tuple(pair.replicate_id for pair in pairs)
        if set(evidence.replicate_ids) != set(pair_ids):
            reasons.append("statistical_evidence_replicates_mismatch")
        if (
            evidence.evaluation_version != baseline.evaluation_version
            or evidence.corpus_version != baseline.corpus_version
            or evidence.dataset_fingerprint != manifest_fingerprint
        ):
            reasons.append("statistical_evidence_provenance_mismatch")
        if not evidence.authoritative or not evidence.independently_verified:
            reasons.append("statistical_evidence_unverified")
        if not math.isclose(
            evidence.observed_success_delta,
            success_delta,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            reasons.append("statistical_evidence_effect_mismatch")
    return _decision(
        baseline,
        candidate,
        success_delta=success_delta,
        latency_ratio=latency_ratio,
        step_ratio=step_ratio,
        cost_delta=cost_delta,
        replicate_ids=tuple(pair.replicate_id for pair in pairs),
        reasons=reasons,
    )


def rollback_decision(decision: ComparisonDecision) -> bool:
    """Return true when the candidate must remain disabled."""
    return not decision.adopt_multi_agent
