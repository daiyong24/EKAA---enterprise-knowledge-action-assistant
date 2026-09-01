from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from backend.app.approval import (
    ApprovalConflictError,
    ApprovalDeniedError,
    ApprovalService,
    ApprovalStateError,
    ApprovalStatus,
    ExecutionUnknownError,
    InMemoryApprovalAuditSink,
)
from backend.app.authorization.models import Principal


def _request(service: ApprovalService):
    return service.create_request(
        proposal_id="proposal-1",
        action="restart-service",
        parameters={"service": "synthetic", "count": 1},
        requester=Principal("requester"),
        correlation_id="corr-1",
        request_id="approval-1",
    )


def test_create_approval_is_exactly_bound_and_audited() -> None:
    sink = InMemoryApprovalAuditSink()
    service = ApprovalService(audit_sink=sink)
    request = _request(service)
    assert request.status == ApprovalStatus.PENDING
    assert request.action_hash and request.parameter_hash
    assert "synthetic" not in str(sink.snapshot()[0])


def test_approval_parameter_snapshot_is_recursively_immutable() -> None:
    service = ApprovalService()
    request = service.create_request(
        proposal_id="proposal-immutable",
        action="change",
        parameters={"nested": {"items": ["original"]}},
        requester=Principal("requester"),
        correlation_id="corr-immutable",
        request_id="approval-immutable",
    )
    request.parameters["new"] = "value"
    request.parameters["nested"]["items"].append("mutated")
    stored = service.get("approval-immutable")
    assert stored.parameters == {"nested": {"items": ["original"]}}
    assert stored.parameter_hash == request.parameter_hash


def test_approval_request_has_json_schema_and_round_trip_serialization() -> None:
    service = ApprovalService()
    request = _request(service)
    serialized = request.model_dump_json()
    round_trip = type(request).model_validate_json(serialized)
    assert round_trip.parameters == request.parameters
    assert round_trip.parameter_hash == request.parameter_hash
    assert "parameters" in type(request).model_json_schema()["properties"]


def test_audit_reasons_are_fixed_codes_and_conflicts_are_recorded() -> None:
    sink = InMemoryApprovalAuditSink()
    service = ApprovalService(audit_sink=sink)
    _request(service)
    approver = Principal("approver", roles=frozenset({"approver"}))
    with pytest.raises(ApprovalConflictError):
        service.approve("approval-1", approver, correlation_id="c", action="other")
    with pytest.raises(ApprovalDeniedError):
        import asyncio

        asyncio.run(
            service.execute(
                "approval-1",
                executor=lambda *_: "never",
                idempotency_key="k",
                correlation_id="c",
            )
        )
    rejected = service.reject(
        "approval-1", approver, correlation_id="c", reason="secret detail\n"
    )
    assert rejected.status == ApprovalStatus.REJECTED
    assert sink.snapshot()[-1].reason_code == "rejected_by_approver"


def test_approve_rejects_unauthorized_and_parameter_mutation() -> None:
    service = ApprovalService()
    _request(service)
    with pytest.raises(ApprovalDeniedError):
        service.approve(
            "approval-1",
            Principal("viewer", roles=frozenset({"viewer"})),
            correlation_id="c",
        )
    with pytest.raises(ApprovalConflictError):
        service.approve(
            "approval-1",
            Principal("approver", roles=frozenset({"approver"})),
            correlation_id="c",
            parameters={"service": "other", "count": 1},
        )
    approved = service.approve(
        "approval-1",
        Principal("approver", roles=frozenset({"approver"})),
        correlation_id="c",
    )
    assert approved.status == ApprovalStatus.APPROVED


@pytest.mark.anyio
async def test_expiry_and_replay_are_terminal() -> None:
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    service = ApprovalService(clock=lambda: now[0])
    _request(service)
    now[0] += timedelta(seconds=901)
    assert service.get("approval-1").status == ApprovalStatus.EXPIRED
    with pytest.raises(ApprovalDeniedError):
        await service.execute(
            "approval-1",
            executor=lambda *_: "bad",
            idempotency_key="k",
            correlation_id="c",
        )


@pytest.mark.anyio
async def test_execution_is_idempotent_and_concurrent_single_entry() -> None:
    service = ApprovalService()
    _request(service)
    service.approve(
        "approval-1",
        Principal("approver", roles=frozenset({"approver"})),
        correlation_id="c",
    )
    calls = 0

    async def executor(_action: str, _parameters: object) -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "ok"

    first, second = await asyncio.gather(
        service.execute(
            "approval-1", executor=executor, idempotency_key="k", correlation_id="c"
        ),
        service.execute(
            "approval-1", executor=executor, idempotency_key="k", correlation_id="c"
        ),
        return_exceptions=True,
    )
    assert calls == 1
    assert sum(isinstance(item, ApprovalDeniedError) for item in (first, second)) == 1
    assert service.get("approval-1").status == ApprovalStatus.EXECUTED
    assert (
        await service.execute(
            "approval-1", executor=executor, idempotency_key="k", correlation_id="c"
        )
    ).status == ApprovalStatus.EXECUTED


@pytest.mark.anyio
async def test_execution_failure_and_crash_recovery_never_auto_retry() -> None:
    service = ApprovalService()
    _request(service)
    service.approve(
        "approval-1",
        Principal("approver", roles=frozenset({"approver"})),
        correlation_id="c",
    )

    async def fail(_action: str, _parameters: object) -> str:
        raise RuntimeError("private detail")

    with pytest.raises(ApprovalStateError):
        await service.execute(
            "approval-1", executor=fail, idempotency_key="k", correlation_id="c"
        )
    assert service.get("approval-1").status == ApprovalStatus.FAILED

    service2 = ApprovalService()
    _request(service2)
    service2.approve(
        "approval-1",
        Principal("approver", roles=frozenset({"approver"})),
        correlation_id="c",
    )
    entered = asyncio.Event()

    async def hanging(_action: str, _parameters: object) -> str:
        entered.set()
        await asyncio.Event().wait()
        return "never"

    task = asyncio.create_task(
        service2.execute(
            "approval-1", executor=hanging, idempotency_key="k", correlation_id="c"
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (
        service2.recover_in_flight("approval-1").status
        == ApprovalStatus.EXECUTION_UNKNOWN
    )


@pytest.mark.anyio
async def test_late_executor_result_cannot_overwrite_recovered_unknown() -> None:
    service = ApprovalService()
    _request(service)
    service.approve(
        "approval-1",
        Principal("approver", roles=frozenset({"approver"})),
        correlation_id="c",
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def executor(_action: str, _parameters: object) -> str:
        started.set()
        await release.wait()
        return "possibly-completed"

    task = asyncio.create_task(
        service.execute(
            "approval-1", executor=executor, idempotency_key="k", correlation_id="c"
        )
    )
    await started.wait()
    assert (
        service.recover_in_flight("approval-1").status
        == ApprovalStatus.EXECUTION_UNKNOWN
    )
    release.set()
    with pytest.raises(ExecutionUnknownError):
        await task
    assert service.get("approval-1").status == ApprovalStatus.EXECUTION_UNKNOWN


@pytest.mark.anyio
async def test_late_executor_error_cannot_overwrite_recovered_unknown() -> None:
    service = ApprovalService()
    _request(service)
    service.approve(
        "approval-1",
        Principal("approver", roles=frozenset({"approver"})),
        correlation_id="c",
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def executor(_action: str, _parameters: object) -> str:
        started.set()
        await release.wait()
        raise RuntimeError("late private detail")

    task = asyncio.create_task(
        service.execute(
            "approval-1", executor=executor, idempotency_key="k", correlation_id="c"
        )
    )
    await started.wait()
    service.recover_in_flight("approval-1")
    release.set()
    with pytest.raises(ExecutionUnknownError):
        await task
    assert service.get("approval-1").status == ApprovalStatus.EXECUTION_UNKNOWN
