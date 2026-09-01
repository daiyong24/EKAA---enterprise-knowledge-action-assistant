"""Deterministic scoring and regression gates; human review remains authoritative."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence

from backend.app.agent_evaluation.models import (
    AgentEvaluationResult,
    AgentTrace,
    BatchEvaluation,
    ExpectedToolCall,
    Scenario,
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _same_call(actual: object, expected: ExpectedToolCall) -> bool:
    if not hasattr(actual, "tool_name") or not hasattr(actual, "arguments"):
        return False
    return actual.tool_name == expected.tool_name and _canonical(
        actual.arguments
    ) == _canonical(expected.arguments)


def evaluate_trace(
    scenario: Scenario, trace: AgentTrace, *, price_per_1k_tokens: float | None = None
) -> AgentEvaluationResult:
    """Score every supplied trace, including failures; never cherry-pick successes."""
    if price_per_1k_tokens is not None and (
        price_per_1k_tokens < 0 or not math.isfinite(price_per_1k_tokens)
    ):
        raise ValueError("price must be finite and non-negative")
    if scenario.scenario_id != trace.scenario_id:
        raise ValueError("trace does not belong to scenario")
    expected = scenario.expected_tool_calls
    matched = sum(
        _same_call(actual, expected[index])
        for index, actual in enumerate(trace.tool_calls[: len(expected)])
    )
    denominator = max(len(expected), len(trace.tool_calls))
    tool_accuracy = matched / denominator if denominator else 1.0
    argument_matches = sum(
        hasattr(actual, "arguments")
        and index < len(expected)
        and _canonical(actual.arguments) == _canonical(expected[index].arguments)
        for index, actual in enumerate(trace.tool_calls)
    )
    argument_accuracy = argument_matches / denominator if denominator else 1.0
    unnecessary = max(0, len(trace.tool_calls) - len(expected)) + sum(
        1
        for index, actual in enumerate(trace.tool_calls[: len(expected)])
        if not _same_call(actual, expected[index])
    )
    consequential_calls = [call for call in trace.tool_calls if call.approval_required]
    approval = trace.approval_compliance and (
        not scenario.approval_required
        or (
            bool(consequential_calls)
            and all(
                call.approval_status in {"approved", "executed"}
                for call in consequential_calls
            )
        )
    )
    failures: list[str] = []
    fixes: list[str] = []
    if trace.success != scenario.expected_success:
        failures.append("task_failure")
        fixes.append("inspect terminal outcome and tool/provider error path")
    if tool_accuracy < 1:
        failures.append("tool_selection_error")
        fixes.append("improve tool selection contract and scenario examples")
    if argument_accuracy < 1:
        failures.append("argument_error")
        fixes.append("tighten argument schema validation and decision prompting")
    if unnecessary:
        failures.append("unnecessary_tool_call")
        fixes.append("add termination and duplicate-call guardrails")
    if trace.escalated != scenario.expected_escalated:
        failures.append("escalation_error")
        fixes.append("define escalation trigger and terminal handling")
    if any(call.outcome.value == "failed" for call in trace.tool_calls):
        failures.append("tool_failure_observed")
        fixes.append("review failed tool call and recovery behavior")
    if not approval:
        failures.append("approval_non_compliance")
        fixes.append("stop consequential execution until scoped approval is recorded")
    if scenario.ambiguous or scenario.reviewer_required:
        failures.append("ambiguous_requires_review")
    cost = None
    if price_per_1k_tokens is None and trace.token_count is not None:
        failures.append("cost_unavailable")
    elif price_per_1k_tokens is not None and trace.token_count is not None:
        cost = trace.token_count / 1000 * price_per_1k_tokens
    return AgentEvaluationResult(
        scenario_id=scenario.scenario_id,
        trace_id=trace.trace_id,
        tool_accuracy=tool_accuracy,
        argument_accuracy=argument_accuracy,
        task_success=trace.success == scenario.expected_success,
        steps=trace.steps,
        retries=trace.retries,
        unnecessary_calls=unnecessary,
        escalation_correct=trace.escalated == scenario.expected_escalated,
        approval_compliance=approval,
        latency_ms=trace.latency_ms,
        estimated_cost=cost,
        failure_categories=tuple(dict.fromkeys(failures)),
        prioritized_fixes=tuple(dict.fromkeys(fixes)),
        requires_human_review=scenario.ambiguous or scenario.reviewer_required,
    )


def evaluate_batch(
    scenarios: Sequence[Scenario],
    traces: Sequence[AgentTrace],
    *,
    run_id: str,
    price_per_1k_tokens: float | None = None,
) -> BatchEvaluation:
    """Evaluate a complete declared suite and reject missing/duplicate trace mapping."""
    by_id = {trace.scenario_id: trace for trace in traces}
    scenario_ids = [item.scenario_id for item in scenarios]
    trace_ids = [trace.trace_id for trace in traces]
    if (
        len(by_id) != len(traces)
        or len(set(scenario_ids)) != len(scenario_ids)
        or len(set(trace_ids)) != len(trace_ids)
        or set(by_id) != set(scenario_ids)
    ):
        raise ValueError("scenario and trace sets must map one-to-one")
    results = tuple(
        evaluate_trace(
            scenario,
            by_id[scenario.scenario_id],
            price_per_1k_tokens=price_per_1k_tokens,
        )
        for scenario in scenarios
    )
    count = len(results)
    categories = Counter(
        category for result in results for category in result.failure_categories
    )
    fixes = tuple(
        dict.fromkeys(fix for result in results for fix in result.prioritized_fixes)
    )
    costs = [result.estimated_cost for result in results]
    cost = (
        sum(item for item in costs if item is not None)
        if all(item is not None for item in costs)
        else None
    )
    return BatchEvaluation(
        run_id=run_id,
        scenario_count=count,
        successful_tasks=sum(result.task_success for result in results),
        success_rate=sum(result.task_success for result in results) / count
        if count
        else 0,
        mean_tool_accuracy=sum(result.tool_accuracy for result in results) / count
        if count
        else 0,
        mean_argument_accuracy=sum(result.argument_accuracy for result in results)
        / count
        if count
        else 0,
        mean_steps=sum(result.steps for result in results) / count if count else 0,
        mean_retries=sum(result.retries for result in results) / count if count else 0,
        mean_unnecessary_calls=sum(result.unnecessary_calls for result in results)
        / count
        if count
        else 0,
        approval_compliance_rate=sum(result.approval_compliance for result in results)
        / count
        if count
        else 0,
        mean_latency_ms=sum(result.latency_ms for result in results) / count
        if count
        else 0,
        estimated_cost=cost,
        cost_available=cost is not None,
        failure_categories=tuple(category for category, _ in categories.most_common()),
        prioritized_fixes=fixes,
        results=results,
    )


def reviewer_agreement(first: Mapping[str, bool], second: Mapping[str, bool]) -> float:
    """Return exact-label agreement; missing IDs are an invalid calibration set."""
    if (
        not first
        or set(first) != set(second)
        or any(type(value) is not bool for value in first.values())
        or any(type(value) is not bool for value in second.values())
    ):
        raise ValueError("reviewers must score the same non-empty scenario set")
    return sum(first[key] == second[key] for key in first) / len(first)


def regression_gate(
    baseline: BatchEvaluation,
    candidate: BatchEvaluation,
    *,
    minimum_success_rate: float,
    maximum_mean_steps: float | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Apply declared gates to the whole run, with no selective metric reporting."""
    # Re-validate serialized data so model_copy()/deserialization cannot bypass
    # the aggregate consistency checks on BatchEvaluation.
    try:
        BatchEvaluation.model_validate(baseline.model_dump())
        BatchEvaluation.model_validate(candidate.model_dump())
    except ValueError as exc:
        raise ValueError("baseline or candidate aggregate is inconsistent") from exc
    if (
        baseline.scenario_count != candidate.scenario_count
        or baseline.scenario_count == 0
    ):
        raise ValueError("baseline and candidate suites must have equal non-zero size")
    if {item.scenario_id for item in baseline.results} != {
        item.scenario_id for item in candidate.results
    }:
        raise ValueError("baseline and candidate scenario IDs must match")
    if not 0 <= minimum_success_rate <= 1 or not math.isfinite(minimum_success_rate):
        raise ValueError("minimum success rate must be between 0 and 1")
    failures: list[str] = []
    if candidate.success_rate < minimum_success_rate:
        failures.append("success_rate_below_threshold")
    if candidate.mean_tool_accuracy < baseline.mean_tool_accuracy:
        failures.append("tool_accuracy_regressed")
    if candidate.mean_argument_accuracy < baseline.mean_argument_accuracy:
        failures.append("argument_accuracy_regressed")
    if maximum_mean_steps is not None and candidate.mean_steps > maximum_mean_steps:
        failures.append("step_budget_regressed")
    if candidate.approval_compliance_rate < 1:
        failures.append("approval_compliance_failed")
    return not failures, tuple(failures)
