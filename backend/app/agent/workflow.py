"""Provider-neutral, explicit-node single-agent workflow.

The workflow intentionally does not depend on LangGraph: no approved dependency
exists in this project and the small state graph is easier to audit at this stage.
It can be adapted behind the same contracts if a later stage proves that a graph
runtime is needed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from typing import Protocol

from pydantic import ValidationError

from backend.app.agent.checkpoint import CheckpointStore, InMemoryCheckpointStore
from backend.app.agent.errors import (
    AgentHostileOutputError,
    AgentRetryLimitError,
    AgentStepLimitError,
    CheckpointError,
    InvalidAgentStateError,
)
from backend.app.agent.models import (
    AgentDecision,
    AgentMessage,
    AgentRunResult,
    AgentState,
    AgentStatus,
    NoToolDecision,
    TerminateDecision,
    ToolCallDecision,
)
from backend.app.authorization.models import Principal
from backend.app.tools.errors import ToolError
from backend.app.tools.models import ToolExecutionContext, ToolResult, ToolResultKind
from backend.app.tools.protocol import ToolRegistry

logger = logging.getLogger(__name__)


class AgentPolicy(Protocol):
    async def decide(self, state: AgentState) -> AgentDecision: ...


class SingleAgentWorkflow:
    """Run one decision node and one tool node under hard budgets."""

    def __init__(
        self,
        *,
        policy: AgentPolicy,
        tools: ToolRegistry,
        checkpoints: CheckpointStore | None = None,
        max_steps: int = 8,
        max_retries: int = 2,
    ) -> None:
        if max_steps < 1 or max_steps > 100:
            raise ValueError("agent max steps must be between 1 and 100")
        if max_retries < 0 or max_retries > 10:
            raise ValueError("agent max retries must be between 0 and 10")
        self._policy = policy
        self._tools = tools
        self._checkpoints = checkpoints or InMemoryCheckpointStore()
        self._max_steps = max_steps
        self._max_retries = max_retries

    async def run(
        self,
        user_message: str,
        *,
        principal: Principal,
        correlation_id: str,
        run_id: str | None = None,
        resume: bool = False,
        authorization_checked: bool = False,
    ) -> AgentRunResult:
        if not user_message or len(user_message) > 8_000:
            raise InvalidAgentStateError("user message is invalid")
        resolved_run_id = run_id or uuid.uuid4().hex
        state = self._load_or_create(
            resolved_run_id,
            user_message,
            correlation_id,
            principal=principal,
            authorization_checked=authorization_checked,
            resume=resume,
        )
        if resume and not authorization_checked:
            raise InvalidAgentStateError(
                "fresh authorization is required to resume an agent run"
            )
        if state.status in {
            AgentStatus.COMPLETED,
            AgentStatus.AWAITING_APPROVAL,
            AgentStatus.FAILED,
            AgentStatus.TERMINATED,
        }:
            return self._result(state)
        state.status = AgentStatus.RUNNING
        self._checkpoint(state)
        while state.step_count < self._max_steps:
            try:
                decision = await self._policy.decide(state.model_copy(deep=True))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state.retry_count += 1
                self._checkpoint(state)
                if state.retry_count > self._max_retries:
                    state.status = AgentStatus.FAILED
                    state.termination_reason = "decision_failed"
                    self._checkpoint(state)
                    raise AgentRetryLimitError(
                        "agent decision retries exhausted"
                    ) from exc
                continue
            if not isinstance(
                decision, (ToolCallDecision, NoToolDecision, TerminateDecision)
            ):
                state.status = AgentStatus.FAILED
                state.termination_reason = "invalid_decision"
                self._checkpoint(state)
                raise InvalidAgentStateError("agent returned an invalid decision")
            try:
                decision = type(decision).model_validate(decision.model_dump())
            except (ValidationError, ValueError) as exc:
                state.status = AgentStatus.FAILED
                state.termination_reason = "invalid_decision_payload"
                self._checkpoint(state)
                raise InvalidAgentStateError(
                    "agent decision payload was invalid"
                ) from exc
            state.step_count += 1
            if isinstance(decision, NoToolDecision):
                self._append_message(
                    state, AgentMessage(role="agent", content=decision.answer)
                )
                state.status = AgentStatus.COMPLETED
                self._checkpoint(state)
                return self._result(state)
            if isinstance(decision, TerminateDecision):
                state.status = AgentStatus.TERMINATED
                state.termination_reason = self._safe_reason(decision.reason)
                self._checkpoint(state)
                return self._result(state)
            fingerprint = self._call_fingerprint(decision)
            if fingerprint in state.call_fingerprints:
                state.status = AgentStatus.TERMINATED
                state.termination_reason = "repeated_tool_call"
                self._checkpoint(state)
                return self._result(state)
            if len(state.call_fingerprints) >= 32:
                state.status = AgentStatus.TERMINATED
                state.termination_reason = "call_fingerprint_limit"
                self._checkpoint(state)
                raise AgentStepLimitError("agent tool-call fingerprint budget reached")
            state.last_tool_name = decision.tool_name
            try:
                result = await self._tools.execute(
                    decision.tool_name,
                    decision.arguments,
                    context=ToolExecutionContext(
                        principal=principal,
                        correlation_id=correlation_id,
                        authorization_checked=authorization_checked,
                    ),
                )
            except asyncio.CancelledError:
                raise
            except ToolError as exc:
                state.retry_count += 1
                self._append_message(
                    state,
                    AgentMessage(
                        role="tool",
                        content="Tool unavailable; no trusted result was returned.",
                    ),
                )
                self._checkpoint(state)
                if state.retry_count > self._max_retries:
                    state.status = AgentStatus.FAILED
                    state.termination_reason = "tool_failed"
                    self._checkpoint(state)
                    raise AgentRetryLimitError("tool retries exhausted") from exc
                continue
            state.retry_count = 0
            state.call_fingerprints.append(fingerprint)
            try:
                content = self._safe_tool_content(result)
            except AgentHostileOutputError:
                state.status = AgentStatus.FAILED
                state.termination_reason = "hostile_tool_output"
                self._checkpoint(state)
                raise
            self._append_message(
                state,
                AgentMessage(
                    role="tool", content=f"[UNTRUSTED_TOOL_OUTPUT]\n{content}"
                ),
            )
            if result.kind == ToolResultKind.PROPOSAL:
                state.status = AgentStatus.AWAITING_APPROVAL
                state.pending_proposal_id = result.proposal_id
                state.termination_reason = "approval_required"
                self._checkpoint(state)
                return self._result(state)
            self._checkpoint(state)
        state.status = AgentStatus.TERMINATED
        state.termination_reason = "step_limit"
        self._checkpoint(state)
        raise AgentStepLimitError("agent step limit reached")

    def _load_or_create(
        self,
        run_id: str,
        message: str,
        correlation_id: str,
        *,
        principal: Principal,
        authorization_checked: bool,
        resume: bool,
    ) -> AgentState:
        try:
            checkpoint = self._checkpoints.load(run_id)
        except Exception as exc:
            raise CheckpointError("checkpoint load failed") from exc
        if not resume and checkpoint is not None and run_id:
            raise InvalidAgentStateError("run id already has a checkpoint")
        existing = checkpoint if resume else None
        if existing is not None:
            if not isinstance(existing, AgentState):
                raise InvalidAgentStateError("checkpoint state is invalid")
            if (
                existing.correlation_id != correlation_id
                or existing.user_message != message
                or existing.principal_fingerprint
                != self._principal_fingerprint(principal)
            ):
                raise InvalidAgentStateError("checkpoint does not match this request")
            return existing
        if resume and checkpoint is None:
            raise CheckpointError("requested checkpoint was not found")
        return AgentState(
            run_id=run_id,
            correlation_id=correlation_id,
            principal_subject=principal.subject,
            principal_fingerprint=self._principal_fingerprint(principal),
            authorization_checked_at_start=authorization_checked,
            user_message=message,
            messages=[AgentMessage(role="user", content=message)],
        )

    def _checkpoint(self, state: AgentState) -> None:
        try:
            self._checkpoints.save(state)
        except Exception as exc:
            raise CheckpointError("checkpoint save failed") from exc

    @staticmethod
    def _append_message(state: AgentState, message: AgentMessage) -> None:
        if len(state.messages) >= 100:
            raise InvalidAgentStateError("message budget exceeded")
        state.messages.append(message)

    @staticmethod
    def _safe_tool_content(result: ToolResult) -> str:
        content = result.content.replace("\x00", "")
        # Reserve space for the untrusted-output marker added to the message.
        if (
            not content.strip()
            or len(content) > 7_960
            or any(ord(char) < 32 and char not in "\t\n" for char in content)
        ):
            raise AgentHostileOutputError("tool output exceeded the content budget")
        return content

    @staticmethod
    def _safe_reason(reason: str) -> str:
        return reason.replace("\x00", "")[:200]

    @staticmethod
    def _call_fingerprint(decision: ToolCallDecision) -> str:
        payload = json.dumps(
            [decision.tool_name, SingleAgentWorkflow._canonicalize(decision.arguments)],
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _canonicalize(value: object) -> object:
        if isinstance(value, dict):
            return {
                str(key): SingleAgentWorkflow._canonicalize(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        if isinstance(value, (list, tuple)):
            return [SingleAgentWorkflow._canonicalize(item) for item in value]
        return value

    @staticmethod
    def _principal_fingerprint(principal: Principal) -> str:
        payload = json.dumps(
            {
                "subject": principal.subject,
                "roles": sorted(principal.roles),
                "groups": sorted(principal.groups),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _result(state: AgentState) -> AgentRunResult:
        answer = next(
            (
                message.content
                for message in reversed(state.messages)
                if message.role == "agent"
            ),
            None,
        )
        return AgentRunResult(
            run_id=state.run_id,
            status=state.status,
            answer=answer,
            pending_proposal_id=state.pending_proposal_id,
            steps=state.step_count,
            retries=state.retry_count,
        )
