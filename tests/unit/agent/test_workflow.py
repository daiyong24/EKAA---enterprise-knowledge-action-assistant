from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from backend.app.agent import (
    InMemoryCheckpointStore,
    NoToolDecision,
    SingleAgentWorkflow,
    TerminateDecision,
    ToolCallDecision,
)
from backend.app.agent.errors import (
    AgentHostileOutputError,
    AgentRetryLimitError,
    AgentStepLimitError,
    InvalidAgentStateError,
)
from backend.app.agent.models import AgentState
from backend.app.agent.workflow import AgentPolicy
from backend.app.authorization.models import Principal
from backend.app.tools.audit import InMemoryToolAuditSink
from backend.app.tools.builtins import register_baseline_tools
from backend.app.tools.models import (
    ToolDefinition,
    ToolExecutionContext,
    ToolPolicy,
    ToolResult,
    ToolResultKind,
)
from backend.app.tools.protocol import ToolRegistry


def setup() -> tuple[ToolRegistry, InMemoryCheckpointStore]:
    registry = ToolRegistry(audit_sink=InMemoryToolAuditSink())
    register_baseline_tools(registry)
    return registry, InMemoryCheckpointStore()


class AnswerPolicy:
    async def decide(self, state: AgentState) -> NoToolDecision:
        return NoToolDecision(kind="answer", answer=f"answer: {state.user_message}")


class LookupThenAnswerPolicy:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, state: AgentState) -> NoToolDecision | ToolCallDecision:
        self.calls += 1
        if self.calls == 1:
            return ToolCallDecision(
                kind="tool_call", tool_name="knowledge_lookup", arguments={"query": "x"}
            )
        return NoToolDecision(kind="answer", answer="done")


class ProposalPolicy:
    async def decide(self, state: AgentState) -> ToolCallDecision:
        return ToolCallDecision(
            kind="tool_call",
            tool_name="change_proposal",
            arguments={"action": "change", "parameters": {}},
        )


class LoopPolicy:
    async def decide(self, state: AgentState) -> ToolCallDecision:
        return ToolCallDecision(
            kind="tool_call", tool_name="knowledge_lookup", arguments={"query": "same"}
        )


class ReorderedLoopPolicy:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, state: AgentState) -> ToolCallDecision:
        self.calls += 1
        nested = {"x": 1, "y": [2]} if self.calls == 1 else {"y": [2], "x": 1}
        return ToolCallDecision(
            kind="tool_call",
            tool_name="knowledge_lookup",
            arguments={"query": "same", "nested": nested},
        )


class DistinctToolPolicy:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, state: AgentState) -> ToolCallDecision:
        self.calls += 1
        return ToolCallDecision(
            kind="tool_call",
            tool_name="knowledge_lookup",
            arguments={"query": f"distinct-{self.calls}"},
        )


class FailingPolicy:
    async def decide(self, state: AgentState) -> NoToolDecision:
        raise RuntimeError("provider detail must not leak")


class MalformedAnswerPolicy:
    async def decide(self, state: AgentState) -> NoToolDecision:
        return NoToolDecision.model_construct(kind="answer", answer="\x00")


class MalformedTerminationPolicy:
    async def decide(self, state: AgentState) -> TerminateDecision:
        return TerminateDecision.model_construct(kind="terminate", reason="   ")


@pytest.mark.anyio
async def test_baseline_artifact_contract() -> None:
    artifact_path = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "evaluation"
        / "stage12_baseline_v1.json"
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["suite_id"] == "stage12-single-agent-baseline-v1"
    assert len(artifact["tasks"]) == 3
    policies = {
        "no-tool-answer": AnswerPolicy(),
        "authorized-read-tool": LookupThenAnswerPolicy(),
        "write-proposal-stops": ProposalPolicy(),
    }
    expected = {task["id"]: task for task in artifact["tasks"]}
    assert set(policies) == set(expected)
    actual: list[dict[str, object]] = []
    for task_id, policy in policies.items():
        registry, checkpoints = setup()
        workflow = SingleAgentWorkflow(
            policy=policy, tools=registry, checkpoints=checkpoints
        )
        result = await workflow.run(
            task_id,
            principal=Principal("baseline"),
            correlation_id=f"baseline-{task_id}",
            run_id=f"baseline-{task_id}",
            authorization_checked=True,
        )
        actual.append(
            {
                "id": task_id,
                "status": result.status.value,
                "steps": result.steps,
            }
        )
    successes = sum(
        item["status"] == expected[item["id"]]["expected_terminal_status"]
        and item["steps"] == expected[item["id"]]["expected_steps"]
        for item in actual
    )
    failed = len(actual) - successes
    assert successes == artifact["recorded_results"]["successful_tasks"]
    assert failed == artifact["recorded_results"]["failed_tasks"]
    assert successes / len(actual) == artifact["recorded_results"]["success_rate"]


@pytest.mark.anyio
async def test_no_tool_path_completes() -> None:
    registry, checkpoints = setup()
    workflow = SingleAgentWorkflow(
        policy=AnswerPolicy(), tools=registry, checkpoints=checkpoints
    )
    result = await workflow.run(
        "hello",
        principal=Principal("alice"),
        correlation_id="c1",
        authorization_checked=True,
    )
    assert result.status == "completed"
    assert result.answer == "answer: hello"
    assert checkpoints.load(result.run_id) is not None


@pytest.mark.anyio
async def test_expected_tool_path_is_bounded_and_completes() -> None:
    registry, checkpoints = setup()
    workflow = SingleAgentWorkflow(
        policy=LookupThenAnswerPolicy(), tools=registry, checkpoints=checkpoints
    )
    result = await workflow.run(
        "lookup",
        principal=Principal("alice"),
        correlation_id="c1",
        authorization_checked=True,
    )
    assert result.status == "completed"
    assert result.steps == 2
    saved = checkpoints.load(result.run_id)
    assert saved is not None
    assert "[UNTRUSTED_TOOL_OUTPUT]" in "\n".join(m.content for m in saved.messages)


@pytest.mark.anyio
async def test_write_path_stops_at_proposal() -> None:
    registry, checkpoints = setup()
    workflow = SingleAgentWorkflow(
        policy=ProposalPolicy(), tools=registry, checkpoints=checkpoints
    )
    result = await workflow.run(
        "change",
        principal=Principal("alice"),
        correlation_id="c1",
        authorization_checked=True,
    )
    assert result.status == "awaiting_approval"
    assert result.pending_proposal_id is not None


@pytest.mark.anyio
async def test_repeated_tool_call_terminates_loop() -> None:
    registry, checkpoints = setup()
    workflow = SingleAgentWorkflow(
        policy=LoopPolicy(), tools=registry, checkpoints=checkpoints
    )
    result = await workflow.run(
        "loop",
        principal=Principal("alice"),
        correlation_id="c1",
        authorization_checked=True,
    )
    assert result.status == "terminated"
    state = checkpoints.load(result.run_id)
    assert state is not None
    assert state.termination_reason == "repeated_tool_call"


@pytest.mark.anyio
async def test_repeated_fingerprint_canonicalizes_nested_mapping_order() -> None:
    registry = ToolRegistry(audit_sink=InMemoryToolAuditSink())

    class Input(BaseModel):
        query: str
        nested: dict[str, object]

    async def handler(
        _context: ToolExecutionContext, _arguments: BaseModel
    ) -> ToolResult:
        return ToolResult(kind=ToolResultKind.DATA, content="safe")

    registry.register(
        ToolDefinition(
            name="knowledge_lookup",
            description="lookup",
            policy=ToolPolicy.READ_ONLY,
            input_model=Input,
        ),
        handler,
    )
    workflow = SingleAgentWorkflow(policy=ReorderedLoopPolicy(), tools=registry)
    result = await workflow.run(
        "loop",
        principal=Principal("alice"),
        correlation_id="c1",
        authorization_checked=True,
    )
    assert result.status == "terminated"


@pytest.mark.anyio
async def test_distinct_tool_calls_stop_at_fingerprint_budget() -> None:
    registry = ToolRegistry(audit_sink=InMemoryToolAuditSink())
    register_baseline_tools(registry)
    policy = DistinctToolPolicy()
    checkpoints = InMemoryCheckpointStore()
    workflow = SingleAgentWorkflow(
        policy=policy,
        tools=registry,
        checkpoints=checkpoints,
        max_steps=40,
    )
    with pytest.raises(AgentStepLimitError):
        await workflow.run(
            "many reads",
            principal=Principal("alice"),
            correlation_id="c1",
            run_id="many-reads",
            authorization_checked=True,
        )
    state = checkpoints.load("many-reads")
    assert state is not None
    assert state.status == "terminated"
    assert state.termination_reason == "call_fingerprint_limit"
    assert len(state.call_fingerprints) == 32
    assert policy.calls == 33


@pytest.mark.anyio
async def test_decision_retry_is_capped() -> None:
    registry, _ = setup()
    workflow = SingleAgentWorkflow(
        policy=FailingPolicy(), tools=registry, max_retries=1
    )
    with pytest.raises(AgentRetryLimitError):
        await workflow.run(
            "fail",
            principal=Principal("alice"),
            correlation_id="c1",
            authorization_checked=True,
        )


@pytest.mark.anyio
async def test_step_limit_is_hard() -> None:
    registry, _ = setup()
    workflow = SingleAgentWorkflow(policy=LoopPolicy(), tools=registry, max_steps=1)
    # The first call returns data, then the second decision would exceed the budget.
    with pytest.raises(AgentStepLimitError):
        await workflow.run(
            "loop",
            principal=Principal("alice"),
            correlation_id="c1",
            authorization_checked=True,
        )


@pytest.mark.anyio
async def test_resume_checkpoint_requires_matching_request() -> None:
    registry, checkpoints = setup()
    workflow = SingleAgentWorkflow(
        policy=AnswerPolicy(), tools=registry, checkpoints=checkpoints
    )
    first = await workflow.run(
        "same",
        principal=Principal("alice"),
        correlation_id="c1",
        authorization_checked=True,
    )
    with pytest.raises(InvalidAgentStateError):
        await workflow.run(
            "different",
            principal=Principal("alice"),
            correlation_id="c1",
            run_id=first.run_id,
            resume=True,
            authorization_checked=True,
        )


@pytest.mark.anyio
async def test_hostile_tool_output_is_rejected() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(audit_sink=sink)

    class Input(BaseModel):
        query: str

    async def hostile(
        _context: ToolExecutionContext, _arguments: BaseModel
    ) -> ToolResult:
        return ToolResult(kind=ToolResultKind.DATA, content="x" * 8_000)

    registry.register(
        ToolDefinition(
            name="hostile",
            description="hostile",
            policy=ToolPolicy.READ_ONLY,
            input_model=Input,
        ),
        hostile,
    )

    class HostilePolicy:
        async def decide(self, state: AgentState) -> ToolCallDecision:
            return ToolCallDecision(
                kind="tool_call", tool_name="hostile", arguments={"query": "x"}
            )

    checkpoints = InMemoryCheckpointStore()
    workflow = SingleAgentWorkflow(
        policy=HostilePolicy(), tools=registry, checkpoints=checkpoints
    )
    # Exact 8,000 chars is valid at the tool boundary but is rejected by the
    # agent's reserved output budget after its marker is added.
    with pytest.raises(AgentHostileOutputError):
        await workflow.run(
            "hostile",
            principal=Principal("alice"),
            correlation_id="c1",
            authorization_checked=True,
        )
    state = checkpoints.load("hostile-run")
    assert state is None


@pytest.mark.anyio
async def test_hostile_output_checkpoint_is_terminal_and_cannot_resume() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(audit_sink=sink)

    class Input(BaseModel):
        query: str

    async def hostile(
        _context: ToolExecutionContext, _arguments: BaseModel
    ) -> ToolResult:
        return ToolResult(kind=ToolResultKind.DATA, content="x" * 8_000)

    registry.register(
        ToolDefinition(
            name="hostile",
            description="hostile",
            policy=ToolPolicy.READ_ONLY,
            input_model=Input,
        ),
        hostile,
    )

    class HostilePolicy:
        async def decide(self, state: AgentState) -> ToolCallDecision:
            return ToolCallDecision(
                kind="tool_call", tool_name="hostile", arguments={"query": "x"}
            )

    checkpoints = InMemoryCheckpointStore()
    workflow = SingleAgentWorkflow(
        policy=HostilePolicy(), tools=registry, checkpoints=checkpoints
    )
    with pytest.raises(AgentHostileOutputError):
        await workflow.run(
            "hostile",
            principal=Principal("alice"),
            correlation_id="c1",
            run_id="hostile-run",
            authorization_checked=True,
        )
    state = checkpoints.load("hostile-run")
    assert state is not None
    assert state.status == "failed"
    resumed = await workflow.run(
        "hostile",
        principal=Principal("alice"),
        correlation_id="c1",
        run_id="hostile-run",
        resume=True,
        authorization_checked=True,
    )
    assert resumed.status == "failed"


@pytest.mark.anyio
async def test_resume_requires_fresh_authorization_proof() -> None:
    registry, checkpoints = setup()
    workflow = SingleAgentWorkflow(
        policy=AnswerPolicy(), tools=registry, checkpoints=checkpoints
    )
    first = await workflow.run(
        "same",
        principal=Principal("alice"),
        correlation_id="c1",
        authorization_checked=True,
    )
    with pytest.raises(InvalidAgentStateError):
        await workflow.run(
            "same",
            principal=Principal("alice"),
            correlation_id="c1",
            run_id=first.run_id,
            resume=True,
            authorization_checked=False,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "policy", [MalformedAnswerPolicy(), MalformedTerminationPolicy()]
)
async def test_malformed_decision_payload_fails_and_is_checkpointed(
    policy: AgentPolicy,
) -> None:
    registry, checkpoints = setup()
    workflow = SingleAgentWorkflow(
        policy=policy, tools=registry, checkpoints=checkpoints
    )
    with pytest.raises(InvalidAgentStateError):
        await workflow.run(
            "malformed",
            principal=Principal("alice"),
            correlation_id="c1",
            run_id="malformed-run",
            authorization_checked=True,
        )
    state = checkpoints.load("malformed-run")
    assert state is not None
    assert state.status == "failed"
    with pytest.raises(InvalidAgentStateError):
        await workflow.run(
            "malformed",
            principal=Principal("alice"),
            correlation_id="c1",
            run_id="malformed-run",
            resume=False,
            authorization_checked=True,
        )


@pytest.mark.anyio
async def test_checkpoint_is_bound_to_principal_claims() -> None:
    registry, checkpoints = setup()
    workflow = SingleAgentWorkflow(
        policy=AnswerPolicy(), tools=registry, checkpoints=checkpoints
    )
    first = await workflow.run(
        "same",
        principal=Principal("alice", roles=frozenset({"employee"})),
        correlation_id="c1",
        authorization_checked=True,
    )
    with pytest.raises(InvalidAgentStateError):
        await workflow.run(
            "same",
            principal=Principal("alice", roles=frozenset({"admin"})),
            correlation_id="c1",
            run_id=first.run_id,
            resume=True,
            authorization_checked=True,
        )
