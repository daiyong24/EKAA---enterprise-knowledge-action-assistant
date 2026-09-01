from __future__ import annotations

import pytest

from backend.app.agent_evaluation import (
    AgentEvaluationResult,
    AgentTrace,
    ExpectedToolCall,
    Scenario,
    evaluate_batch,
    evaluate_trace,
    regression_gate,
    reviewer_agreement,
)
from backend.app.agent_evaluation.models import ToolCallOutcome, TraceToolCall


def _scenario(*, ambiguous: bool = False) -> Scenario:
    return Scenario(
        scenario_id="s1",
        expected_tool_calls=(
            ExpectedToolCall(tool_name="lookup", arguments={"q": "x"}),
        ),
        expected_success=True,
        ambiguous=ambiguous,
    )


def _trace(*, success: bool = True, extra: bool = False) -> AgentTrace:
    calls = [
        TraceToolCall(
            step=1,
            tool_name="lookup",
            arguments={"q": "x"},
            outcome=ToolCallOutcome.COMPLETED,
        )
    ]
    if extra:
        calls.append(
            TraceToolCall(
                step=2,
                tool_name="lookup",
                arguments={"q": "y"},
                outcome=ToolCallOutcome.COMPLETED,
            )
        )
    return AgentTrace(
        run_id="r1",
        scenario_id="s1",
        tool_calls=tuple(calls),
        success=success,
        steps=len(calls),
        retries=0,
        latency_ms=10,
        token_count=100,
        trace_id="t1",
        result_ref="r1",
    )


def test_scoring_exposes_unnecessary_calls_and_cost_unknown() -> None:
    result = evaluate_trace(_scenario(), _trace(extra=True))
    assert result.tool_accuracy == 0.5
    assert result.argument_accuracy == 0.5
    assert result.unnecessary_calls == 1
    assert "unnecessary_tool_call" in result.failure_categories
    assert "cost_unavailable" in result.failure_categories


def test_ambiguous_scenario_requires_human_review() -> None:
    result = evaluate_trace(
        _scenario(ambiguous=True), _trace(), price_per_1k_tokens=0.01
    )
    assert result.requires_human_review is True
    assert result.estimated_cost == pytest.approx(0.001)


def test_trace_integrity_rejects_fake_success_and_bad_budget() -> None:
    with pytest.raises(ValueError):
        AgentTrace(
            run_id="r1",
            scenario_id="s1",
            tool_calls=(
                TraceToolCall(
                    step=2,
                    tool_name="lookup",
                    arguments={"q": "x"},
                    outcome=ToolCallOutcome.FAILED,
                ),
            ),
            success=True,
            steps=1,
            retries=0,
            latency_ms=10,
            trace_id="t1",
            result_ref="r1",
        )


def test_recovered_failed_tool_is_visible_in_failure_categories() -> None:
    trace = AgentTrace(
        run_id="r1",
        scenario_id="s1",
        tool_calls=(
            TraceToolCall(
                step=1,
                tool_name="lookup",
                arguments={"q": "x"},
                outcome=ToolCallOutcome.FAILED,
            ),
        ),
        success=True,
        retries=1,
        steps=1,
        latency_ms=10,
        trace_id="t1",
        result_ref="r1",
    )
    result = evaluate_trace(_scenario(), trace)
    assert "tool_failure_observed" in result.failure_categories


def test_evaluation_metrics_reject_negative_values_and_non_bool_reviewers() -> None:
    with pytest.raises(ValueError):
        AgentEvaluationResult(
            scenario_id="s1",
            trace_id="t1",
            tool_accuracy=1,
            argument_accuracy=1,
            task_success=True,
            steps=-1,
            retries=0,
            unnecessary_calls=0,
            escalation_correct=True,
            approval_compliance=True,
            latency_ms=1,
        )
    with pytest.raises(ValueError):
        reviewer_agreement({"s1": 1}, {"s1": True})  # type: ignore[dict-item]


def test_price_schedule_is_validated_even_without_token_usage() -> None:
    trace = _trace().model_copy(update={"token_count": None})
    with pytest.raises(ValueError, match="price must be finite"):
        evaluate_trace(_scenario(), trace, price_per_1k_tokens=float("nan"))


def test_batch_mapping_and_reviewer_agreement_are_strict() -> None:
    batch = evaluate_batch(
        [_scenario()], [_trace()], run_id="run-1", price_per_1k_tokens=0.01
    )
    assert batch.success_rate == 1
    assert batch.cost_available is True
    assert reviewer_agreement({"s1": True}, {"s1": True}) == 1
    with pytest.raises(ValueError):
        reviewer_agreement({"s1": True}, {"s2": True})


def test_regression_gate_applies_declared_thresholds() -> None:
    baseline = evaluate_batch(
        [_scenario()], [_trace()], run_id="base", price_per_1k_tokens=0.01
    )
    candidate = evaluate_batch(
        [_scenario()],
        [_trace(success=False)],
        run_id="candidate",
        price_per_1k_tokens=0.01,
    )
    passed, failures = regression_gate(
        baseline, candidate, minimum_success_rate=1, maximum_mean_steps=2
    )
    assert passed is False
    assert "success_rate_below_threshold" in failures


def test_regression_gate_rejects_tampered_aggregate() -> None:
    baseline = evaluate_batch(
        [_scenario()], [_trace()], run_id="base", price_per_1k_tokens=0.01
    )
    tampered = baseline.model_copy(update={"success_rate": 0.0})
    with pytest.raises(ValueError, match="aggregate is inconsistent"):
        regression_gate(
            baseline, tampered, minimum_success_rate=0, maximum_mean_steps=2
        )


def test_regression_gate_rejects_cost_availability_tampering() -> None:
    baseline = evaluate_batch(
        [_scenario()], [_trace()], run_id="base", price_per_1k_tokens=0.01
    )
    tampered = baseline.model_copy(
        update={"cost_available": True, "estimated_cost": None}
    )
    with pytest.raises(ValueError, match="aggregate is inconsistent"):
        regression_gate(baseline, tampered, minimum_success_rate=0)
