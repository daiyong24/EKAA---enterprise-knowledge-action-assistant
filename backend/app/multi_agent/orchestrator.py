"""Bounded sequential orchestration with fail-closed handoffs."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol, cast

from pydantic import ValidationError

from backend.app.multi_agent.errors import (
    AgentConflictError,
    AgentLoopError,
    CombinedBudgetError,
    HandoffError,
    PartialFailureError,
    PermissionBoundaryError,
)
from backend.app.multi_agent.models import (
    Claim,
    HandoffEnvelope,
    MultiAgentRunResult,
    PermissionContext,
    RoutePlan,
    RunBudget,
    RunStatus,
    SharedState,
    WorkerInput,
    WorkerOutput,
    WorkerRole,
    WorkerStatus,
)
from backend.app.multi_agent.telemetry import (
    BoundedTelemetrySink,
    InMemoryTelemetrySink,
    RouteTelemetryEvent,
    TelemetryEventName,
    TelemetryOutcome,
    TelemetrySink,
)


class Worker(Protocol):
    async def run(self, request: WorkerInput) -> WorkerOutput: ...


SingleAgentFallback = Callable[[SharedState], Awaitable[str]]
HandoffTransport = Callable[
    [HandoffEnvelope], HandoffEnvelope | Awaitable[HandoffEnvelope | None] | None
]


class MultiAgentOrchestrator:
    """A candidate only; it never executes tools or consequential actions."""

    def __init__(
        self,
        *,
        workers: Mapping[str, Worker],
        budget: RunBudget | None = None,
        fallback: SingleAgentFallback | None = None,
        handoff_transport: HandoffTransport | None = None,
        candidate_enabled: bool = False,
        telemetry: TelemetrySink | None = None,
        sync_boundary_trusted: bool | None = None,
    ) -> None:
        self._workers = dict(workers)
        self._budget = budget or RunBudget()
        self._fallback = fallback
        self._handoff_transport = handoff_transport or (lambda envelope: envelope)
        self._sync_boundary_trusted = (
            handoff_transport is None
            if sync_boundary_trusted is None
            else sync_boundary_trusted
        )
        self._candidate_enabled = candidate_enabled
        if telemetry is None:
            self._telemetry: TelemetrySink = InMemoryTelemetrySink()
        elif isinstance(telemetry, InMemoryTelemetrySink):
            self._telemetry = telemetry
        else:
            self._telemetry = BoundedTelemetrySink(telemetry)

    async def run(
        self,
        state: SharedState,
        *,
        plan: RoutePlan,
    ) -> MultiAgentRunResult:
        run_started = time.monotonic()
        deadline = run_started + self._budget.max_latency_ms / 1000
        if plan.reason_code == "single_agent_default":
            self._emit(
                state,
                TelemetryEventName.ROUTE_SELECTED,
                TelemetryOutcome.FALLBACK,
                reason_code="single_agent_default",
            )
            return await self._fallback_result(
                state, "single_agent_default", deadline=deadline
            )
        self._emit(
            state,
            TelemetryEventName.ROUTE_SELECTED,
            TelemetryOutcome.STARTED,
            reason_code="specialized_candidate",
        )
        if not self._candidate_enabled:
            return await self._fallback_result(
                state,
                "candidate_disabled",
                allow_fallback=plan.fallback_to_single_agent,
                deadline=deadline,
            )
        if (
            not inspect.iscoroutinefunction(self._handoff_transport)
            and not self._sync_boundary_trusted
        ):
            return await self._fallback_result(
                state,
                "unsafe_sync_boundary",
                allow_fallback=plan.fallback_to_single_agent,
                deadline=deadline,
            )
        if len(plan.workers) - 1 > self._budget.max_handoffs:
            return await self._fallback_result(
                state,
                "handoff_budget_exhausted",
                allow_fallback=plan.fallback_to_single_agent,
                deadline=deadline,
            )
        current = state
        used: list[str] = []
        total_steps = total_retries = 0
        total_latency = 0.0
        previous: HandoffEnvelope | None = None
        try:
            for sequence, worker_id in enumerate(plan.workers, start=1):
                if len(used) >= self._budget.max_worker_calls:
                    raise CombinedBudgetError("combined worker-call budget exhausted")
                worker = self._workers.get(worker_id)
                if worker is None:
                    raise PartialFailureError("worker is unavailable")
                if worker_id in used:
                    raise AgentLoopError("worker loop detected")
                try:
                    role = WorkerRole(worker_id)
                except ValueError as exc:
                    raise PartialFailureError("worker role is unsupported") from exc
                request = WorkerInput(state=current, role=role, handoff=previous)
                self._emit(
                    state,
                    TelemetryEventName.WORKER_STARTED,
                    TelemetryOutcome.STARTED,
                    worker_id=worker_id,
                    handoffs=len(used),
                )
                started = time.monotonic()
                remaining = deadline - started
                if remaining <= 0:
                    raise CombinedBudgetError("combined agent deadline exhausted")
                try:
                    output = await asyncio.wait_for(worker.run(request), remaining)
                except TimeoutError as exc:
                    raise PartialFailureError("worker deadline exceeded") from exc
                except Exception as exc:
                    raise PartialFailureError("worker failed") from exc
                elapsed_ms = (time.monotonic() - started) * 1000
                try:
                    output = WorkerOutput.model_validate(output.model_dump())
                except (AttributeError, TypeError, ValidationError, ValueError) as exc:
                    raise PartialFailureError("worker output is invalid") from exc
                if output.worker_id != worker_id:
                    raise PartialFailureError("worker identity mismatch")
                self._verify_permission(current.permission, output)
                if output.status != WorkerStatus.COMPLETED:
                    raise PartialFailureError("worker failed")
                total_steps += output.steps
                total_retries += output.retries
                total_latency += elapsed_ms
                if (
                    len(used) + 1 > self._budget.max_worker_calls
                    or total_steps > self._budget.max_worker_steps
                    or total_retries > self._budget.max_worker_retries
                    or total_latency > self._budget.max_latency_ms
                ):
                    raise CombinedBudgetError("combined agent budget exhausted")
                merged = self._merge_claims(current, output)
                current = current.model_copy(
                    update={"revision": current.revision + 1, "claims": merged}
                )
                used.append(worker_id)
                if sequence != len(plan.workers):
                    envelope = self._handoff(
                        current, worker_id, plan.workers[sequence], sequence
                    )
                    try:
                        handoff_remaining = deadline - time.monotonic()
                        if handoff_remaining <= 0:
                            raise HandoffError("handoff deadline exhausted")
                        delivered = await asyncio.wait_for(
                            self._deliver_handoff(envelope), handoff_remaining
                        )
                    except Exception as exc:
                        raise HandoffError("handoff delivery failed") from exc
                    if delivered != envelope:
                        raise HandoffError("handoff was lost or altered")
                    previous = delivered
                    total_latency = (time.monotonic() - run_started) * 1000
                    if total_latency > self._budget.max_latency_ms:
                        raise CombinedBudgetError("combined agent deadline exhausted")
                    self._emit(
                        state,
                        TelemetryEventName.HANDOFF_CREATED,
                        TelemetryOutcome.COMPLETED,
                        worker_id=worker_id,
                        handoffs=len(used),
                    )
                self._emit(
                    state,
                    TelemetryEventName.WORKER_COMPLETED,
                    TelemetryOutcome.COMPLETED,
                    worker_id=worker_id,
                    duration_ms=elapsed_ms,
                    handoffs=len(used),
                )
            return MultiAgentRunResult(
                task_id=state.task_id,
                status=RunStatus.COMPLETED,
                workers_used=tuple(used),
                handoffs=max(0, len(used) - 1),
                steps=total_steps,
                retries=total_retries,
                latency_ms=total_latency,
            )
        except AgentConflictError:
            self._emit(
                state,
                TelemetryEventName.CONFLICT,
                TelemetryOutcome.FAILED,
                reason_code="conflicting_claims",
                handoffs=max(0, len(used) - 1),
            )
            return MultiAgentRunResult(
                task_id=state.task_id,
                status=RunStatus.CONFLICT_REQUIRES_REVIEW,
                workers_used=tuple(used),
                handoffs=max(0, len(used) - 1),
                steps=total_steps,
                retries=total_retries,
                latency_ms=total_latency,
                failure_code="conflicting_claims",
            )
        except (
            AgentLoopError,
            HandoffError,
            PermissionBoundaryError,
            PartialFailureError,
            CombinedBudgetError,
        ) as exc:
            self._emit(
                state,
                TelemetryEventName.FAILURE,
                TelemetryOutcome.FAILED,
                reason_code=type(exc).__name__.replace("Error", "").lower(),
                handoffs=max(0, len(used) - 1),
            )
            if self._fallback is not None:
                return await self._fallback_result(
                    state,
                    type(exc).__name__,
                    allow_fallback=plan.fallback_to_single_agent,
                    deadline=deadline,
                )
            return MultiAgentRunResult(
                task_id=state.task_id,
                status=RunStatus.FAILED,
                workers_used=tuple(used),
                handoffs=max(0, len(used) - 1),
                steps=total_steps,
                retries=total_retries,
                latency_ms=total_latency,
                failure_code=type(exc).__name__,
            )

    async def _fallback_result(
        self,
        state: SharedState,
        reason: str,
        *,
        allow_fallback: bool = True,
        deadline: float | None = None,
    ) -> MultiAgentRunResult:
        self._emit(
            state,
            TelemetryEventName.FALLBACK,
            TelemetryOutcome.FALLBACK if allow_fallback else TelemetryOutcome.FAILED,
            reason_code=reason,
        )
        if self._fallback is None or not allow_fallback:
            return MultiAgentRunResult(
                task_id=state.task_id,
                status=RunStatus.TERMINATED if allow_fallback else RunStatus.FAILED,
                failure_code=reason,
                workers_used=("single-agent",),
                handoffs=0,
                steps=0,
                retries=0,
                latency_ms=0,
            )
        if (
            not inspect.iscoroutinefunction(self._fallback)
            and not self._sync_boundary_trusted
        ):
            return MultiAgentRunResult(
                task_id=state.task_id,
                status=RunStatus.FAILED,
                workers_used=("single-agent",),
                failure_code="unsafe_sync_boundary",
                handoffs=0,
                steps=0,
                retries=0,
                latency_ms=0,
            )
        try:
            remaining = (
                max(0.0, deadline - time.monotonic()) if deadline is not None else 8.0
            )
            if remaining <= 0:
                raise TimeoutError("fallback deadline exhausted")
            answer = await asyncio.wait_for(self._invoke_fallback(state), remaining)
            if (
                not isinstance(answer, str)
                or len(answer) > 8_000
                or any(ord(char) < 32 and char not in "\t\n" for char in answer)
            ):
                raise ValueError("fallback output is invalid")
        except Exception:
            return MultiAgentRunResult(
                task_id=state.task_id,
                status=RunStatus.FAILED,
                workers_used=("single-agent",),
                failure_code="fallback_failed",
                handoffs=0,
                steps=0,
                retries=0,
                latency_ms=0,
            )
        return MultiAgentRunResult(
            task_id=state.task_id,
            status=RunStatus.FALLBACK_SINGLE_AGENT,
            answer=None,
            workers_used=("single-agent",),
            failure_code=reason,
            handoffs=0,
            steps=0,
            retries=0,
            latency_ms=0,
        )

    async def _deliver_handoff(
        self, envelope: HandoffEnvelope
    ) -> HandoffEnvelope | None:
        raw_result: object
        if inspect.iscoroutinefunction(self._handoff_transport):
            raw_result = self._handoff_transport(envelope)
        else:
            sync_transport = cast(
                Callable[[HandoffEnvelope], HandoffEnvelope | None],
                self._handoff_transport,
            )
            raw_result = await asyncio.to_thread(sync_transport, envelope)
        if inspect.isawaitable(raw_result):
            resolved = await raw_result
        else:
            resolved = raw_result
        if resolved is not None and not isinstance(resolved, HandoffEnvelope):
            raise TypeError("handoff output is invalid")
        return resolved

    async def _invoke_fallback(self, state: SharedState) -> str:
        if self._fallback is None:
            raise RuntimeError("fallback unavailable")
        if inspect.iscoroutinefunction(self._fallback):
            return await self._fallback(state)
        raw: object = await asyncio.to_thread(self._fallback, state)
        if inspect.isawaitable(raw):
            raw = await raw
        if not isinstance(raw, str):
            raise TypeError("fallback output is invalid")
        return raw

    def _emit(
        self,
        state: SharedState,
        event: TelemetryEventName,
        outcome: TelemetryOutcome,
        *,
        worker_id: str | None = None,
        duration_ms: float = 0,
        handoffs: int = 0,
        reason_code: str | None = None,
    ) -> None:
        try:
            self._telemetry.record(
                RouteTelemetryEvent(
                    event=event,
                    outcome=outcome,
                    task_id=state.task_id,
                    correlation_id=state.correlation_id,
                    worker_id=worker_id,
                    duration_ms=duration_ms,
                    handoffs=handoffs,
                    reason_code=reason_code,
                )
            )
        except Exception:
            # Observability outages must not change agent safety or behavior.
            return

    @staticmethod
    def _verify_permission(expected: PermissionContext, output: WorkerOutput) -> None:
        if (
            output.principal_fingerprint != expected.principal_fingerprint
            or output.authorization_checked != expected.authorization_checked
            or output.approval_request_id != expected.approval_request_id
            or output.action_hash != expected.action_hash
            or output.parameter_hash != expected.parameter_hash
        ):
            raise PermissionBoundaryError("worker crossed permission boundary")

    @staticmethod
    def _merge_claims(state: SharedState, output: WorkerOutput) -> tuple[Claim, ...]:
        merged = {claim.key: claim for claim in state.claims}
        for claim in output.claims:
            previous = merged.get(claim.key)
            if previous is not None and previous.value != claim.value:
                raise AgentConflictError("workers produced conflicting claims")
            merged[claim.key] = claim
        return tuple(merged.values())

    @staticmethod
    def _handoff(
        state: SharedState, from_worker: str, to_worker: str, sequence: int
    ) -> HandoffEnvelope:
        permission_digest = hashlib.sha256(
            state.permission.model_dump_json().encode()
        ).hexdigest()
        base = {
            "task_id": state.task_id,
            "from_worker": from_worker,
            "to_worker": to_worker,
            "sequence": sequence,
            "state_revision": state.revision,
            "state_digest": state.digest(),
            "permission_digest": permission_digest,
            "claim_keys": tuple(claim.key for claim in state.claims),
        }
        digest = hashlib.sha256(
            json.dumps(
                base, sort_keys=True, separators=(",", ":"), default=list
            ).encode()
        ).hexdigest()
        return HandoffEnvelope(**base, handoff_digest=digest)

    @staticmethod
    def _claims_answer(state: SharedState) -> str:
        return "; ".join(f"{claim.key}: {claim.value}" for claim in state.claims)
