"""Immutable approval request contracts."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.tools.models import validate_nested_value

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")


def thaw_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: thaw_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [thaw_value(item) for item in value]
    return value


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"
    EXECUTION_UNKNOWN = "execution_unknown"


class ApprovalRequest(BaseModel):
    """A request whose action and validated parameter digest are immutable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    proposal_id: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=200)
    parameters: dict[str, Any] = Field(default_factory=dict, max_length=32)
    action_hash: str = Field(min_length=64, max_length=64)
    parameter_hash: str = Field(min_length=64, max_length=64)
    requester: str = Field(min_length=1, max_length=128)
    correlation_id: str = Field(min_length=1, max_length=128)
    required_roles: frozenset[str] = frozenset({"approver", "admin"})
    created_at: datetime
    expires_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    approved_by: str | None = None
    rejected_by: str | None = None
    execution_idempotency_key: str | None = None
    execution_result: str | None = None

    @model_validator(mode="after")
    def validate_binding(self) -> ApprovalRequest:
        if self.expires_at <= self.created_at:
            raise ValueError("approval expiry must be after creation")
        action_hash = hashlib.sha256(self.action.encode("utf-8")).hexdigest()
        parameter_hash = hashlib.sha256(
            json.dumps(
                self.parameters,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        if self.action_hash != action_hash or self.parameter_hash != parameter_hash:
            raise ValueError("approval binding digest does not match request")
        return self

    @field_validator("request_id", "proposal_id", "requester", "correlation_id")
    @classmethod
    def safe_ids(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("identifier is invalid")
        return value

    @field_validator("action")
    @classmethod
    def safe_action(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value) or not value.strip():
            raise ValueError("action is invalid")
        return value.strip()

    @field_validator("parameters", mode="before")
    @classmethod
    def bounded_parameters(cls, value: object) -> dict[str, Any]:
        bounded = validate_nested_value(value)
        if not isinstance(bounded, dict):
            raise ValueError("parameters must be an object")
        return bounded

    @field_validator("required_roles")
    @classmethod
    def bounded_roles(cls, value: frozenset[str]) -> frozenset[str]:
        if not value or any(not _SAFE_ID.fullmatch(role) for role in value):
            raise ValueError("required roles are invalid")
        return value

    @field_validator("action_hash", "parameter_hash")
    @classmethod
    def valid_hash(cls, value: str) -> str:
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("hash is invalid")
        return value
