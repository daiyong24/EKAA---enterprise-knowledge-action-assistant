from __future__ import annotations

import pytest

from backend.app.agent_evaluation.models import (
    AgentTrace,
    BatchEvaluation,
    ExpectedToolCall,
    Scenario,
    ToolCallOutcome,
    TraceToolCall,
)
from backend.app.agent_evaluation.scoring import evaluate_batch
from backend.app.multi_agent import (
    EVALUATION_CAPABILITY_TOKEN,
    CandidateRouter,
    Claim,
    MultiAgentOrchestrator,
    PermissionContext,
    ReplicateBundle,
    ReplicatePair,
    RoutePlan,
    RunBudget,
    RunStatus,
    SharedState,
    StatisticalEvidence,
    WorkerInput,
    WorkerOutput,
    WorkerStatus,
    compare_stage14_baseline,
    rollback_decision,
)
from backend.app.multi_agent.telemetry import (
    BoundedTelemetrySink,
    InMemoryTelemetrySink,
)

FP = "a" * 64


def state() -> SharedState:
    return SharedState(
        task_id="task-1",
        correlation_id="corr-1",
        user_message="answer this",
        permission=PermissionContext(
            principal_fingerprint=FP, authorization_checked=True
        ),
    )


def output(
    request: WorkerInput,
    worker_id: str,
    *,
    answer: str | None = None,
    claims: tuple[Claim, ...] = (),
) -> WorkerOutput:
    permission = request.state.permission
    return WorkerOutput(
        worker_id=worker_id,
        status=WorkerStatus.COMPLETED,
        answer=answer,
        claims=claims,
        principal_fingerprint=permission.principal_fingerprint,
        authorization_checked=permission.authorization_checked,
        approval_request_id=permission.approval_request_id,
        action_hash=permission.action_hash,
        parameter_hash=permission.parameter_hash,
        capability_token=EVALUATION_CAPABILITY_TOKEN,
    )


class Researcher:
    async def run(self, request: WorkerInput) -> WorkerOutput:
        return output(request, "researcher", claims=(Claim(key="answer", value="42"),))


class Verifier:
    async def run(self, request: WorkerInput) -> WorkerOutput:
        assert request.handoff is not None
        return output(request, "verifier", claims=(Claim(key="answer", value="42"),))


class Synthesizer:
    async def run(self, request: WorkerInput) -> WorkerOutput:
        assert request.handoff is not None
        return output(request, "synthesizer", answer="answer: 42")


class SlowResearcher:
    async def run(self, request: WorkerInput) -> WorkerOutput:
        import asyncio

        await asyncio.sleep(0.05)
        return output(request, "researcher", answer="late")


def plan() -> RoutePlan:
    return RoutePlan(
        workers=("researcher", "verifier", "synthesizer"),
        reason_code="specialized_candidate",
    )


def test_router_keeps_single_agent_as_default() -> None:
    router = CandidateRouter()
    assert router.plan(requires_specialization=False).workers == ("single-agent",)
    assert router.plan(requires_specialization=True).workers == ("single-agent",)
    assert router.plan(requires_specialization=True, enabled=True).workers == (
        "researcher",
        "verifier",
        "synthesizer",
    )


@pytest.mark.anyio
async def test_bounded_route_preserves_handoffs_and_combined_counters() -> None:
    runner = MultiAgentOrchestrator(
        workers={
            "researcher": Researcher(),
            "verifier": Verifier(),
            "synthesizer": Synthesizer(),
        },
        candidate_enabled=True,
    )
    result = await runner.run(state(), plan=plan())
    assert result.status == RunStatus.COMPLETED
    assert result.workers_used == ("researcher", "verifier", "synthesizer")
    assert result.handoffs == 2
    assert result.answer is None
    assert result.evaluation_only is True


@pytest.mark.anyio
async def test_specialized_route_requires_explicit_enablement() -> None:
    async def fallback(_state: SharedState) -> str:
        return "fallback"

    runner = MultiAgentOrchestrator(
        workers={"researcher": Researcher()}, fallback=fallback
    )
    result = await runner.run(
        state(),
        plan=RoutePlan(workers=("researcher",), reason_code="specialized_candidate"),
    )
    assert result.status == RunStatus.FALLBACK_SINGLE_AGENT
    assert result.failure_code == "candidate_disabled"


@pytest.mark.anyio
async def test_disabled_fallback_is_not_invoked() -> None:
    called = False

    async def fallback(_state: SharedState) -> str:
        nonlocal called
        called = True
        return "fallback"

    runner = MultiAgentOrchestrator(
        workers={"researcher": Researcher()},
        fallback=fallback,
        candidate_enabled=False,
    )
    result = await runner.run(
        state(),
        plan=RoutePlan(
            workers=("researcher",),
            reason_code="specialized_candidate",
            fallback_to_single_agent=False,
        ),
    )
    assert result.status == RunStatus.FAILED
    assert result.failure_code == "candidate_disabled"
    assert called is False


@pytest.mark.anyio
async def test_wall_clock_deadline_is_enforced_and_reported_latency_is_ignored() -> (
    None
):
    runner = MultiAgentOrchestrator(
        workers={"researcher": SlowResearcher()},
        budget=RunBudget(max_latency_ms=10),
        candidate_enabled=True,
    )
    result = await runner.run(
        state(),
        plan=RoutePlan(workers=("researcher",), reason_code="specialized_candidate"),
    )
    assert result.status == RunStatus.FAILED
    assert result.failure_code == "PartialFailureError"
    assert result.latency_ms == 0


@pytest.mark.anyio
async def test_synchronous_handoff_is_bounded_by_the_same_deadline() -> None:
    import time

    def slow_transport(_envelope: object) -> object:
        time.sleep(0.05)
        return None

    runner = MultiAgentOrchestrator(
        workers={"researcher": Researcher(), "verifier": Verifier()},
        budget=RunBudget(max_latency_ms=10),
        handoff_transport=slow_transport,
        candidate_enabled=True,
        sync_boundary_trusted=True,
    )
    result = await runner.run(
        state(),
        plan=RoutePlan(
            workers=("researcher", "verifier"),
            reason_code="specialized_candidate",
        ),
    )
    assert result.status == RunStatus.FAILED
    assert result.failure_code == "HandoffError"


@pytest.mark.anyio
async def test_call_budget_is_checked_before_invoking_next_worker() -> None:
    calls: list[str] = []

    class CountingResearcher(Researcher):
        async def run(self, request: WorkerInput) -> WorkerOutput:
            calls.append("researcher")
            return await super().run(request)

    class CountingVerifier(Verifier):
        async def run(self, request: WorkerInput) -> WorkerOutput:
            calls.append("verifier")
            return await super().run(request)

    runner = MultiAgentOrchestrator(
        workers={"researcher": CountingResearcher(), "verifier": CountingVerifier()},
        budget=RunBudget(max_worker_calls=1),
        candidate_enabled=True,
    )
    result = await runner.run(
        state(),
        plan=RoutePlan(
            workers=("researcher", "verifier"),
            reason_code="specialized_candidate",
        ),
    )
    assert result.status == RunStatus.FAILED
    assert result.failure_code == "CombinedBudgetError"
    assert calls == ["researcher"]


@pytest.mark.anyio
async def test_untrusted_sync_boundary_is_rejected() -> None:
    runner = MultiAgentOrchestrator(
        workers={"researcher": Researcher()},
        handoff_transport=lambda envelope: envelope,
        candidate_enabled=True,
    )
    result = await runner.run(
        state(),
        plan=RoutePlan(workers=("researcher",), reason_code="specialized_candidate"),
    )
    assert result.status == RunStatus.TERMINATED
    assert result.failure_code == "unsafe_sync_boundary"


@pytest.mark.anyio
async def test_fallback_is_bounded_by_the_run_deadline() -> None:
    import asyncio

    async def slow_fallback(_state: SharedState) -> str:
        await asyncio.sleep(0.05)
        return "too late"

    runner = MultiAgentOrchestrator(
        workers={"researcher": Researcher()},
        fallback=slow_fallback,
        budget=RunBudget(max_latency_ms=10),
    )
    result = await runner.run(
        state(),
        plan=RoutePlan(workers=("researcher",), reason_code="specialized_candidate"),
    )
    assert result.status == RunStatus.FAILED
    assert result.failure_code == "fallback_failed"


@pytest.mark.anyio
async def test_telemetry_is_sanitized_and_sink_failures_are_isolated() -> None:
    sink = InMemoryTelemetrySink()
    runner = MultiAgentOrchestrator(
        workers={"researcher": Researcher()},
        telemetry=sink,
        candidate_enabled=True,
    )
    result = await runner.run(
        state(),
        plan=RoutePlan(workers=("researcher",), reason_code="specialized_candidate"),
    )
    assert result.status == RunStatus.COMPLETED
    assert sink.events
    assert all(event.task_id == "task-1" for event in sink.events)
    assert all("answer this" not in event.model_dump_json() for event in sink.events)

    class BrokenSink:
        def record(self, _event: object) -> None:
            raise RuntimeError("sink unavailable")

    isolated = MultiAgentOrchestrator(
        workers={"researcher": Researcher()},
        telemetry=BrokenSink(),
        candidate_enabled=True,
    )
    assert (
        await isolated.run(
            state(),
            plan=RoutePlan(
                workers=("researcher",), reason_code="specialized_candidate"
            ),
        )
    ).status == RunStatus.COMPLETED


@pytest.mark.anyio
async def test_blocking_telemetry_sink_does_not_block_orchestration() -> None:
    import time

    class BlockingSink:
        def record(self, _event: object) -> None:
            time.sleep(0.2)

    runner = MultiAgentOrchestrator(
        workers={"researcher": Researcher()},
        telemetry=BlockingSink(),
        candidate_enabled=True,
    )
    started = time.monotonic()
    result = await runner.run(
        state(),
        plan=RoutePlan(workers=("researcher",), reason_code="specialized_candidate"),
    )
    elapsed = time.monotonic() - started
    assert result.status == RunStatus.COMPLETED
    assert elapsed < 0.15

    bounded = BoundedTelemetrySink(BlockingSink(), max_queue=1)
    assert bounded._queue.maxsize == 1


@pytest.mark.anyio
async def test_handoff_loss_fails_closed_and_can_fallback() -> None:
    async def fallback(_state: SharedState) -> str:
        return "single answer"

    runner = MultiAgentOrchestrator(
        workers={
            "researcher": Researcher(),
            "verifier": Verifier(),
            "synthesizer": Synthesizer(),
        },
        handoff_transport=lambda _envelope: None,
        fallback=fallback,
        candidate_enabled=True,
        sync_boundary_trusted=True,
    )
    result = await runner.run(state(), plan=plan())
    assert result.status == RunStatus.FALLBACK_SINGLE_AGENT
    assert result.failure_code == "HandoffError"


@pytest.mark.anyio
async def test_combined_budget_is_applied_across_workers() -> None:
    runner = MultiAgentOrchestrator(
        workers={
            "researcher": Researcher(),
            "verifier": Verifier(),
            "synthesizer": Synthesizer(),
        },
        budget=RunBudget(max_worker_steps=2),
        candidate_enabled=True,
    )
    result = await runner.run(state(), plan=plan())
    assert result.status == RunStatus.FAILED
    assert result.failure_code == "CombinedBudgetError"


@pytest.mark.anyio
async def test_worker_permission_mutation_fails_closed() -> None:
    class BadWorker:
        async def run(self, request: WorkerInput) -> WorkerOutput:
            bad = output(request, "researcher", answer="bad")
            return bad.model_copy(update={"principal_fingerprint": "b" * 64})

    runner = MultiAgentOrchestrator(
        workers={"researcher": BadWorker()}, candidate_enabled=True
    )
    result = await runner.run(
        state(),
        plan=RoutePlan(workers=("researcher",), reason_code="specialized_candidate"),
    )
    assert result.status == RunStatus.FAILED
    assert result.failure_code == "PermissionBoundaryError"


@pytest.mark.anyio
async def test_model_constructed_invalid_worker_output_is_rejected() -> None:
    class InvalidWorker:
        async def run(self, request: WorkerInput) -> WorkerOutput:
            return WorkerOutput.model_construct(
                worker_id="researcher",
                status=WorkerStatus.COMPLETED,
                answer="\x00invalid",
                principal_fingerprint=request.state.permission.principal_fingerprint,
                authorization_checked=True,
            )

    runner = MultiAgentOrchestrator(
        workers={"researcher": InvalidWorker()}, candidate_enabled=True
    )
    result = await runner.run(
        state(),
        plan=RoutePlan(workers=("researcher",), reason_code="specialized_candidate"),
    )
    assert result.status == RunStatus.FAILED
    assert result.failure_code == "PartialFailureError"


@pytest.mark.anyio
async def test_worker_cannot_escalate_evaluation_capability() -> None:
    class UnsafeWorker:
        async def run(self, request: WorkerInput) -> WorkerOutput:
            return output(request, "researcher", answer="unsafe").model_copy(
                update={"capability_token": "network-write-v1"}
            )

    runner = MultiAgentOrchestrator(
        workers={"researcher": UnsafeWorker()}, candidate_enabled=True
    )
    result = await runner.run(
        state(),
        plan=RoutePlan(workers=("researcher",), reason_code="specialized_candidate"),
    )
    assert result.status == RunStatus.FAILED
    assert result.failure_code == "PartialFailureError"


@pytest.mark.anyio
async def test_conflicting_claims_stop_synthesis() -> None:
    class ConflictingVerifier:
        async def run(self, request: WorkerInput) -> WorkerOutput:
            return output(
                request, "verifier", claims=(Claim(key="answer", value="43"),)
            )

    runner = MultiAgentOrchestrator(
        workers={
            "researcher": Researcher(),
            "verifier": ConflictingVerifier(),
            "synthesizer": Synthesizer(),
        },
        candidate_enabled=True,
    )
    result = await runner.run(state(), plan=plan())
    assert result.status == RunStatus.CONFLICT_REQUIRES_REVIEW
    assert result.failure_code == "conflicting_claims"


def test_route_and_handoff_budgets_are_bounded() -> None:
    with pytest.raises(ValueError):
        RoutePlan(
            workers=("researcher", "researcher"), reason_code="specialized_candidate"
        )
    # RoutePlan itself is valid; the aggregate handoff budget terminates safely.
    import asyncio

    result = asyncio.run(
        MultiAgentOrchestrator(
            workers={"researcher": Researcher(), "verifier": Verifier()},
            budget=RunBudget(max_handoffs=0),
            candidate_enabled=True,
        ).run(
            state(),
            plan=RoutePlan(
                workers=("researcher", "verifier"),
                reason_code="specialized_candidate",
            ),
        )
    )
    assert result.status == RunStatus.TERMINATED


def _scenarios(*, variant: bool = False) -> tuple[Scenario, ...]:
    return (
        Scenario(scenario_id="s1", expected_success=True),
        Scenario(
            scenario_id="s2",
            expected_success=True,
            expected_tool_calls=(
                (ExpectedToolCall(tool_name="knowledge_lookup"),) if variant else ()
            ),
        ),
    )


def _batch(
    run_id: str,
    latency: float,
    *,
    price: float | None = None,
    success: bool = True,
    variant: bool = False,
    escalated: bool = False,
) -> object:
    scenarios = _scenarios(variant=variant)
    traces = tuple(
        AgentTrace(
            run_id=run_id,
            scenario_id=item.scenario_id,
            success=success,
            escalated=escalated,
            steps=1,
            retries=0,
            latency_ms=latency,
            token_count=100,
            trace_id=f"trace-{item.scenario_id}",
            result_ref=f"result-{item.scenario_id}",
        )
        for item in scenarios
    )
    return evaluate_batch(
        scenarios,
        traces,
        run_id=run_id,
        price_per_1k_tokens=price,
        evaluation_version="stage14-v1",
        corpus_version="stage14-corpus-v1",
    )


def test_stage14_comparison_retains_single_agent_when_no_benefit() -> None:
    baseline = _batch("baseline", 10.0)
    candidate = _batch("candidate", 20.0)
    decision = compare_stage14_baseline(baseline, candidate)
    assert decision.decision == "retain_single_agent"
    assert decision.reason_code == "replicate_evidence_unavailable"
    assert rollback_decision(decision)


def test_comparison_can_adopt_only_with_measured_benefit() -> None:
    baseline = _batch("baseline-0", 100.0, price=1.0, success=False)
    candidate = _batch("candidate-0", 50.0, price=1.0)
    pairs = tuple(
        ReplicatePair(
            replicate_id=f"replicate-{index}",
            baseline=_batch(f"baseline-{index}", 100.0, price=1.0, success=False),
            candidate=_batch(f"candidate-{index}", 50.0, price=1.0),
        )
        for index in range(3)
    )
    bundle = ReplicateBundle(
        bundle_id="bundle-1",
        baseline=baseline,
        candidate=candidate,
        replicates=pairs,
    )
    unavailable = compare_stage14_baseline(
        baseline,
        candidate,
        replicate_bundle=bundle,
        minimum_success_delta=0.05,
        authoritative_scenarios=_scenarios(),
    )
    assert unavailable.decision == "retain_single_agent"
    assert unavailable.reason_code == "statistical_evidence_unavailable"
    decision = compare_stage14_baseline(
        baseline,
        candidate,
        minimum_success_delta=0.05,
        replicate_bundle=bundle,
        authoritative_scenarios=_scenarios(),
        statistical_evidence=StatisticalEvidence(
            analysis_id="analysis-1",
            method="paired_permutation",
            replicate_ids=tuple(pair.replicate_id for pair in pairs),
            evaluation_version="stage14-v1",
            corpus_version="stage14-corpus-v1",
            dataset_fingerprint=baseline.dataset_fingerprint,
            power_target=0.8,
            achieved_power=0.95,
            p_value=0.01,
            observed_success_delta=1.0,
            authoritative=True,
            independently_verified=True,
        ),
    )
    assert decision.decision == "adopt_multi_agent"
    assert not rollback_decision(decision)


def test_fake_replicate_count_cannot_adopt_and_zero_delta_is_invalid() -> None:
    baseline = _batch("baseline", 10.0, price=1.0, success=False)
    candidate = _batch("candidate", 10.0, price=1.0)
    retained = compare_stage14_baseline(baseline, candidate)
    assert retained.decision == "retain_single_agent"
    assert retained.reason_code == "replicate_evidence_unavailable"
    with pytest.raises(ValueError, match="minimum success delta"):
        compare_stage14_baseline(baseline, candidate, minimum_success_delta=0.0)


def test_replicate_bundle_rejects_dataset_fingerprint_tampering() -> None:
    baseline = _batch("baseline", 10.0, price=1.0)
    candidate = _batch("candidate", 10.0, price=1.0, variant=True)
    with pytest.raises(ValueError, match="provenance"):
        ReplicatePair(replicate_id="rep-1", baseline=baseline, candidate=candidate)


def test_success_gain_with_tool_accuracy_regression_retains_single_agent() -> None:
    scenario = Scenario(
        scenario_id="tool-scenario",
        expected_tool_calls=(ExpectedToolCall(tool_name="expected_tool"),),
        expected_success=True,
    )

    def tool_batch(run_id: str, tool_name: str, *, success: bool) -> object:
        trace = AgentTrace(
            run_id=run_id,
            scenario_id=scenario.scenario_id,
            tool_calls=(
                TraceToolCall(
                    tool_name=tool_name,
                    step=1,
                    outcome=ToolCallOutcome.COMPLETED,
                ),
            ),
            success=success,
            steps=1,
            retries=0,
            latency_ms=10,
            token_count=100,
            trace_id=f"trace-{run_id}",
            result_ref=f"result-{run_id}",
        )
        return evaluate_batch(
            (scenario,),
            (trace,),
            run_id=run_id,
            price_per_1k_tokens=1.0,
            evaluation_version="stage14-v1",
            corpus_version="stage14-corpus-v1",
        )

    baseline = tool_batch("baseline-0", "expected_tool", success=False)
    candidate = tool_batch("candidate-0", "wrong_tool", success=True)
    pairs = tuple(
        ReplicatePair(
            replicate_id=f"replicate-tool-{index}",
            baseline=tool_batch(f"baseline-{index}", "expected_tool", success=False),
            candidate=tool_batch(f"candidate-{index}", "wrong_tool", success=True),
        )
        for index in range(3)
    )
    decision = compare_stage14_baseline(
        baseline,
        candidate,
        replicate_bundle=ReplicateBundle(
            bundle_id="bundle-tool",
            baseline=baseline,
            candidate=candidate,
            replicates=pairs,
        ),
        minimum_success_delta=0.01,
        authoritative_scenarios=(scenario,),
    )
    assert decision.decision == "retain_single_agent"
    assert "tool_accuracy_regressed" in decision.reason_code
    assert "unnecessary_calls_regressed" in decision.reason_code


def test_success_gain_with_escalation_regression_retains_single_agent() -> None:
    baseline = _batch("baseline-0", 10.0, price=1.0, success=False, escalated=False)
    candidate = _batch("candidate-0", 10.0, price=1.0, escalated=True)
    pairs = tuple(
        ReplicatePair(
            replicate_id=f"replicate-escalation-{index}",
            baseline=_batch(
                f"baseline-{index}",
                10.0,
                price=1.0,
                success=False,
                escalated=False,
            ),
            candidate=_batch(f"candidate-{index}", 10.0, price=1.0, escalated=True),
        )
        for index in range(3)
    )
    decision = compare_stage14_baseline(
        baseline,
        candidate,
        replicate_bundle=ReplicateBundle(
            bundle_id="bundle-escalation",
            baseline=baseline,
            candidate=candidate,
            replicates=pairs,
        ),
        minimum_success_delta=0.01,
        authoritative_scenarios=_scenarios(),
    )
    assert decision.decision == "retain_single_agent"
    assert "escalation_correctness_regressed" in decision.reason_code


def test_replicate_bundle_rejects_reused_artifacts_with_distinct_replicate_ids() -> (
    None
):
    baseline = _batch("baseline-reused", 10.0, price=1.0, success=False)
    candidate = _batch("candidate-reused", 10.0, price=1.0)
    pair = ReplicatePair(
        replicate_id="replicate-original",
        baseline=baseline,
        candidate=candidate,
    )
    reused_pairs = tuple(
        pair.model_copy(update={"replicate_id": f"replicate-{index}"})
        for index in range(3)
    )
    with pytest.raises(ValueError, match="artifact run IDs"):
        ReplicateBundle(
            bundle_id="bundle-reused",
            baseline=baseline,
            candidate=candidate,
            replicates=reused_pairs,
        )


def test_comparison_rejects_model_copy_forged_fingerprint() -> None:
    pairs = tuple(
        ReplicatePair(
            replicate_id=f"replicate-forged-{index}",
            baseline=_batch(
                f"baseline-forged-{index}", 100.0, price=1.0, success=False
            ).model_copy(update={"dataset_fingerprint": "b" * 64}),
            candidate=_batch(f"candidate-forged-{index}", 50.0, price=1.0).model_copy(
                update={"dataset_fingerprint": "b" * 64}
            ),
        )
        for index in range(3)
    )
    baseline = pairs[0].baseline
    candidate = pairs[0].candidate
    bundle = ReplicateBundle(
        bundle_id="bundle-forged",
        baseline=baseline,
        candidate=candidate,
        replicates=pairs,
    )
    with pytest.raises(ValueError, match="fingerprint does not match manifest"):
        compare_stage14_baseline(
            baseline,
            candidate,
            replicate_bundle=bundle,
            minimum_success_delta=0.01,
            authoritative_scenarios=_scenarios(),
        )


def test_top_level_bundle_ids_and_malformed_artifacts_are_rejected() -> None:
    candidate = _batch("candidate-0", 10.0, price=1.0)
    pairs = tuple(
        ReplicatePair(
            replicate_id=f"rep-{index}",
            baseline=_batch(f"baseline-{index}", 10.0, price=1.0),
            candidate=_batch(f"candidate-{index}", 10.0, price=1.0),
        )
        for index in range(3)
    )
    with pytest.raises(ValueError, match="top-level artifact IDs"):
        ReplicateBundle(
            bundle_id="bundle-bad",
            baseline=_batch("different", 10.0, price=1.0),
            candidate=candidate,
            replicates=pairs,
        )
    malformed = BatchEvaluation.model_construct(run_id="bad")
    with pytest.raises(ValueError, match="evaluation artifact"):
        compare_stage14_baseline(malformed, candidate)
