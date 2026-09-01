"""Explicit human approval and idempotent consequential execution boundary."""

from backend.app.approval.audit import ApprovalAuditEvent, InMemoryApprovalAuditSink
from backend.app.approval.errors import (
    ApprovalConflictError,
    ApprovalDeniedError,
    ApprovalError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalStateError,
    ExecutionUnknownError,
)
from backend.app.approval.models import ApprovalRequest, ApprovalStatus
from backend.app.approval.service import ApprovalService

__all__ = [
    "ApprovalAuditEvent",
    "ApprovalConflictError",
    "ApprovalDeniedError",
    "ApprovalError",
    "ApprovalExpiredError",
    "ApprovalNotFoundError",
    "ApprovalRequest",
    "ApprovalService",
    "ApprovalStateError",
    "ApprovalStatus",
    "ExecutionUnknownError",
    "InMemoryApprovalAuditSink",
]
