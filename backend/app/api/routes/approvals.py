"""Transport adapter for explicit, scoped human approval."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.app.approval import (
    ApprovalConflictError,
    ApprovalDeniedError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalService,
    ApprovalStateError,
)
from backend.app.approval.models import thaw_value
from backend.app.authorization.http import correlation_id, resolve_principal
from backend.app.core.exceptions import ApplicationError

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=200)
    parameters: dict[str, Any] = Field(default_factory=dict, max_length=32)
    ttl_seconds: float = Field(default=900, gt=0, le=86_400)


class ApprovalDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str | None = Field(default=None, max_length=200)
    parameters: dict[str, Any] | None = Field(default=None, max_length=32)


class ApprovalRejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="rejected", min_length=1, max_length=200)


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id: str
    proposal_id: str
    action: str
    parameters: dict[str, Any]
    requester: str
    status: str
    approved_by: str | None
    rejected_by: str | None
    expires_at: str


def _response(request: Any) -> ApprovalResponse:
    return ApprovalResponse(
        request_id=request.request_id,
        proposal_id=request.proposal_id,
        action=request.action,
        parameters=thaw_value(request.parameters),
        requester=request.requester,
        status=request.status.value,
        approved_by=request.approved_by,
        rejected_by=request.rejected_by,
        expires_at=request.expires_at.isoformat(),
    )


def _service(request: Request) -> ApprovalService:
    return cast(ApprovalService, request.app.state.approval_service)


def _authorized_view(request: Any, principal: Any) -> None:
    if not principal.authenticated or not (
        principal.subject == request.requester
        or principal.is_admin
        or principal.roles & request.required_roles
    ):
        raise ApprovalDeniedError("approval request access denied")


@router.post("", response_model=ApprovalResponse, status_code=201)
async def create_approval(
    request: Request, body: ApprovalCreateBody
) -> ApprovalResponse:
    try:
        principal = resolve_principal(request)
        return _response(
            _service(request).create_request(
                proposal_id=body.proposal_id,
                action=body.action,
                parameters=body.parameters,
                requester=principal,
                correlation_id=correlation_id(request),
                ttl_seconds=body.ttl_seconds,
            )
        )
    except ApprovalDeniedError as exc:
        raise ApplicationError(
            code="approval_denied",
            message="Approval request is not allowed.",
            status_code=403,
        ) from exc
    except ApprovalConflictError as exc:
        raise ApplicationError(
            code="approval_conflict",
            message="Approval request already exists.",
            status_code=409,
        ) from exc
    except (ApprovalStateError, ValueError) as exc:
        raise ApplicationError(
            code="invalid_approval_request",
            message="Approval request is invalid.",
            status_code=422,
        ) from exc


@router.get("/{request_id}", response_model=ApprovalResponse)
async def get_approval(request: Request, request_id: str) -> ApprovalResponse:
    try:
        principal = resolve_principal(request)
        current = _service(request).get(request_id)
        _authorized_view(current, principal)
        return _response(current)
    except ApprovalNotFoundError as exc:
        raise ApplicationError(
            code="approval_not_found",
            message="Approval request was not found.",
            status_code=404,
        ) from exc
    except ApprovalDeniedError as exc:
        raise ApplicationError(
            code="approval_denied",
            message="Approval request access denied.",
            status_code=403,
        ) from exc


@router.post("/{request_id}/approve", response_model=ApprovalResponse)
async def approve(
    request: Request, request_id: str, body: ApprovalDecisionBody
) -> ApprovalResponse:
    try:
        principal = resolve_principal(request)
        return _response(
            _service(request).approve(
                request_id,
                principal,
                correlation_id=correlation_id(request),
                action=body.action,
                parameters=body.parameters,
            )
        )
    except ApprovalNotFoundError as exc:
        raise ApplicationError(
            code="approval_not_found",
            message="Approval request was not found.",
            status_code=404,
        ) from exc
    except ApprovalDeniedError as exc:
        raise ApplicationError(
            code="approval_denied",
            message="You are not authorized to approve this request.",
            status_code=403,
        ) from exc
    except ApprovalExpiredError as exc:
        raise ApplicationError(
            code="approval_expired",
            message="Approval request has expired.",
            status_code=409,
        ) from exc
    except (ApprovalConflictError, ApprovalStateError) as exc:
        raise ApplicationError(
            code="approval_conflict",
            message="Approval request cannot be changed.",
            status_code=409,
        ) from exc


@router.post("/{request_id}/reject", response_model=ApprovalResponse)
async def reject(
    request: Request, request_id: str, body: ApprovalRejectBody
) -> ApprovalResponse:
    try:
        principal = resolve_principal(request)
        return _response(
            _service(request).reject(
                request_id,
                principal,
                correlation_id=correlation_id(request),
                reason=body.reason,
            )
        )
    except ApprovalNotFoundError as exc:
        raise ApplicationError(
            code="approval_not_found",
            message="Approval request was not found.",
            status_code=404,
        ) from exc
    except ApprovalDeniedError as exc:
        raise ApplicationError(
            code="approval_denied",
            message="You are not authorized to reject this request.",
            status_code=403,
        ) from exc
    except ApprovalExpiredError as exc:
        raise ApplicationError(
            code="approval_expired",
            message="Approval request has expired.",
            status_code=409,
        ) from exc
    except ApprovalStateError as exc:
        raise ApplicationError(
            code="approval_conflict",
            message="Approval request cannot be changed.",
            status_code=409,
        ) from exc
