"""Atomic human approval and exactly-once execution boundary."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import threading
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.approval.audit import (
    ApprovalAuditEvent,
    ApprovalAuditSink,
    InMemoryApprovalAuditSink,
)
from backend.app.approval.errors import (
    ApprovalConflictError,
    ApprovalDeniedError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalStateError,
    ExecutionUnknownError,
)
from backend.app.approval.models import ApprovalRequest, ApprovalStatus, thaw_value
from backend.app.authorization.models import Principal
from backend.app.tools.models import validate_nested_value

Execution = Callable[[str, Mapping[str, Any]], str | Awaitable[str]]
_SAFE_REASON_CODES = frozenset(
    {
        "approval_expired",
        "approver_not_authorized",
        "approval_required",
        "approval_not_pending",
        "action_binding_conflict",
        "parameter_binding_conflict",
        "idempotency_conflict",
        "idempotent_replay",
        "execution_failed",
        "crash_recovery_requires_reconciliation",
        "request_id_conflict",
        "rejected_by_approver",
    }
)


class ApprovalService:
    """In-memory reference service with a replaceable persistence boundary."""

    def __init__(
        self,
        *,
        audit_sink: ApprovalAuditSink | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sink = audit_sink or InMemoryApprovalAuditSink()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._requests: dict[str, ApprovalRequest] = {}
        self._lock = threading.RLock()

    def create_request(
        self,
        *,
        proposal_id: str,
        action: str,
        parameters: Mapping[str, Any],
        requester: Principal,
        correlation_id: str,
        ttl_seconds: float = 900.0,
        required_roles: frozenset[str] = frozenset({"approver", "admin"}),
        request_id: str | None = None,
    ) -> ApprovalRequest:
        if not requester.authenticated or ttl_seconds <= 0 or ttl_seconds > 86_400:
            raise ApprovalDeniedError("requester is not eligible")
        bounded = validate_nested_value(dict(parameters))
        if not isinstance(bounded, dict):
            raise ApprovalStateError("parameters must be an object")
        normalized = action.strip()
        if not normalized or any(ord(char) < 32 for char in normalized):
            raise ApprovalStateError("action is invalid")
        action_hash = self._hash(normalized)
        parameter_hash = self._hash(bounded)
        now = self._clock()
        request = ApprovalRequest(
            request_id=request_id or uuid.uuid4().hex,
            proposal_id=proposal_id,
            action=normalized,
            parameters=bounded,
            action_hash=action_hash,
            parameter_hash=parameter_hash,
            requester=requester.subject or "anonymous",
            correlation_id=correlation_id,
            required_roles=required_roles,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        with self._lock:
            if request.request_id in self._requests:
                self._audit(
                    request,
                    requester.subject or "anonymous",
                    "denied",
                    reason="request_id_conflict",
                )
                raise ApprovalConflictError("request already exists")
            self._requests[request.request_id] = copy.deepcopy(request)
        self._audit(request, requester.subject or "anonymous", "created")
        return copy.deepcopy(request)

    def get(self, request_id: str) -> ApprovalRequest:
        with self._lock:
            stored = self._requests.get(request_id)
            request = copy.deepcopy(stored) if stored is not None else None
            if request is None:
                raise ApprovalNotFoundError("approval request was not found")
            if (
                request.status == ApprovalStatus.PENDING
                and self._clock() >= request.expires_at
            ):
                request = request.model_copy(update={"status": ApprovalStatus.EXPIRED})
                self._requests[request_id] = copy.deepcopy(request)
                self._audit(request, "system", "expired", reason="approval_expired")
            return copy.deepcopy(request)

    def approve(
        self,
        request_id: str,
        approver: Principal,
        *,
        correlation_id: str,
        action: str | None = None,
        parameters: Mapping[str, Any] | None = None,
    ) -> ApprovalRequest:
        return self._decide(
            request_id,
            approver,
            correlation_id=correlation_id,
            approved=True,
            action=action,
            parameters=parameters,
        )

    def reject(
        self,
        request_id: str,
        approver: Principal,
        *,
        correlation_id: str,
        reason: str = "rejected",
    ) -> ApprovalRequest:
        if not reason.strip() or len(reason) > 200:
            raise ApprovalStateError("rejection reason is invalid")
        return self._decide(
            request_id,
            approver,
            correlation_id=correlation_id,
            approved=False,
            reason="rejected_by_approver",
        )

    def _decide(
        self,
        request_id: str,
        approver: Principal,
        *,
        correlation_id: str,
        approved: bool,
        action: str | None = None,
        parameters: Mapping[str, Any] | None = None,
        reason: str | None = None,
    ) -> ApprovalRequest:
        with self._lock:
            request = self.get(request_id)
            if not approver.authenticated or not (
                approver.is_admin or approver.roles & request.required_roles
            ):
                self._audit(
                    request,
                    approver.subject or "anonymous",
                    "denied",
                    reason="approver_not_authorized",
                )
                raise ApprovalDeniedError("approver is not authorized")
            if action is not None and self._hash(action.strip()) != request.action_hash:
                self._audit(
                    request,
                    approver.subject or "anonymous",
                    "denied",
                    reason="action_binding_conflict",
                )
                raise ApprovalConflictError("action changed; new approval is required")
            if parameters is not None:
                try:
                    parameter_hash = self._hash(validate_nested_value(dict(parameters)))
                except (TypeError, ValueError) as exc:
                    self._audit(
                        request,
                        approver.subject or "anonymous",
                        "denied",
                        reason="parameter_binding_conflict",
                    )
                    raise ApprovalConflictError(
                        "parameters changed; new approval is required"
                    ) from exc
                if parameter_hash != request.parameter_hash:
                    self._audit(
                        request,
                        approver.subject or "anonymous",
                        "denied",
                        reason="parameter_binding_conflict",
                    )
                    raise ApprovalConflictError(
                        "parameters changed; new approval is required"
                    )
            if request.status == ApprovalStatus.EXPIRED:
                self._audit(
                    request,
                    approver.subject or "anonymous",
                    "denied",
                    reason="approval_expired",
                )
                raise ApprovalExpiredError("approval request expired")
            if request.status != ApprovalStatus.PENDING:
                self._audit(
                    request,
                    approver.subject or "anonymous",
                    "denied",
                    reason="approval_not_pending",
                )
                raise ApprovalStateError("approval request is no longer pending")
            status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
            request = request.model_copy(
                update={
                    "status": status,
                    "approved_by": approver.subject if approved else None,
                    "rejected_by": approver.subject if not approved else None,
                }
            )
            self._requests[request_id] = copy.deepcopy(request)
        self._audit(
            request,
            approver.subject or "anonymous",
            "approved" if approved else "rejected",
            reason=reason,
        )
        return copy.deepcopy(request)

    async def execute(
        self,
        request_id: str,
        *,
        executor: Execution,
        idempotency_key: str,
        correlation_id: str,
    ) -> ApprovalRequest:
        if (
            not idempotency_key
            or len(idempotency_key) > 128
            or any(ord(char) < 32 for char in idempotency_key)
        ):
            raise ApprovalStateError("idempotency key is invalid")
        with self._lock:
            request = self.get(request_id)
            if request.status == ApprovalStatus.EXECUTED:
                if request.execution_idempotency_key == idempotency_key:
                    self._audit(request, "system", "denied", reason="idempotent_replay")
                    return request
                self._audit(request, "system", "denied", reason="idempotency_conflict")
                raise ApprovalConflictError("request was already executed")
            if request.status == ApprovalStatus.EXPIRED:
                self._audit(request, "system", "denied", reason="approval_expired")
            if request.status != ApprovalStatus.APPROVED:
                self._audit(request, "system", "denied", reason="approval_required")
                raise ApprovalDeniedError("explicit approval is required")
            request = request.model_copy(
                update={
                    "status": ApprovalStatus.EXECUTING,
                    "execution_idempotency_key": idempotency_key,
                }
            )
            self._requests[request_id] = copy.deepcopy(request)
        self._audit(request, "system", "executing")
        try:
            result = executor(request.action, request.parameters)
            outcome = await result if inspect.isawaitable(result) else result
            # Consequential output is intentionally not persisted in the approval
            # record; only a safe outcome marker is retained.
            _ = str(outcome)[:500]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with self._lock:
                current = self._requests.get(request_id)
                if (
                    current is None
                    or current.status != ApprovalStatus.EXECUTING
                    or current.execution_idempotency_key != idempotency_key
                ):
                    # A concurrent recovery owns the final state. Do not turn
                    # an uncertain external outcome into a retryable failure.
                    raise ExecutionUnknownError(
                        "execution outcome requires manual reconciliation"
                    ) from exc
                failed = request.model_copy(
                    update={
                        "status": ApprovalStatus.FAILED,
                        "execution_result": "execution_failed",
                    }
                )
                self._requests[request_id] = copy.deepcopy(failed)
            self._audit(failed, "system", "failed", reason="execution_failed")
            raise ApprovalStateError("approved action failed") from exc
        with self._lock:
            current = self._requests.get(request_id)
            if (
                current is None
                or current.status != ApprovalStatus.EXECUTING
                or current.execution_idempotency_key != idempotency_key
            ):
                # Recovery may have marked the operation unknown while the
                # executor was still unwinding. Never overwrite that decision.
                raise ExecutionUnknownError(
                    "execution outcome requires manual reconciliation"
                )
            done = request.model_copy(
                update={
                    "status": ApprovalStatus.EXECUTED,
                    "execution_result": "completed",
                }
            )
            self._requests[request_id] = copy.deepcopy(done)
        self._audit(done, "system", "executed")
        return done

    def recover_in_flight(self, request_id: str) -> ApprovalRequest:
        with self._lock:
            request = self.get(request_id)
            if request.status != ApprovalStatus.EXECUTING:
                raise ApprovalStateError("request is not in flight")
            unknown = request.model_copy(
                update={
                    "status": ApprovalStatus.EXECUTION_UNKNOWN,
                    "execution_result": "manual_reconciliation_required",
                }
            )
            self._requests[request_id] = copy.deepcopy(unknown)
        self._audit(
            unknown,
            "system",
            "unknown",
            reason="crash_recovery_requires_reconciliation",
        )
        return copy.deepcopy(unknown)

    def _audit(
        self,
        request: ApprovalRequest,
        actor: str,
        event: str,
        *,
        reason: str | None = None,
    ) -> None:
        self._sink.record(
            ApprovalAuditEvent.now(
                request_id=request.request_id,
                actor=actor,
                correlation_id=request.correlation_id,
                event=event,
                action_hash=request.action_hash,
                parameter_hash=request.parameter_hash,
                reason_code=reason if reason in _SAFE_REASON_CODES else None,
            )
        )

    @staticmethod
    def _hash(value: object) -> str:
        payload = (
            value
            if isinstance(value, str)
            else json.dumps(
                thaw_value(value),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
