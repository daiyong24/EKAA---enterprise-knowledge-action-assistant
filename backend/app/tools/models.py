"""Typed contracts for policy-classified tools.

Tool results and output are untrusted data.  The models deliberately keep a
bounded, content-only result and never provide an ``execute_write`` capability.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.authorization.models import Principal

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")


def _bounded_value(value: object, *, depth: int = 0) -> object:
    if depth > 4:
        raise ValueError("nested value depth is too large")
    if isinstance(value, str):
        if len(value) > 2_000 or any(
            ord(char) < 32 and char not in "\t\n" for char in value
        ):
            raise ValueError("string value is invalid or too large")
        return value
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite numeric values are not allowed")
    if isinstance(value, dict):
        if len(value) > 32:
            raise ValueError("too many nested fields")
        return {
            _bounded_key(key): _bounded_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        if len(value) > 32:
            raise ValueError("too many nested items")
        return [_bounded_value(item, depth=depth + 1) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    raise ValueError("unsupported nested value")


def _bounded_key(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError("nested key is invalid")
    if any(ord(char) < 32 for char in value):
        raise ValueError("nested key contains control characters")
    return value


def validate_nested_value(value: object) -> object:
    """Validate and return a bounded, JSON-like nested argument value."""
    return _bounded_value(value)


class ToolPolicy(StrEnum):
    READ_ONLY = "read_only"
    PROPOSE_WRITE = "propose_write"


class ToolResultKind(StrEnum):
    DATA = "data"
    PROPOSAL = "proposal"


class ToolResult(BaseModel):
    """Bounded output returned by a tool; it is always marked untrusted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ToolResultKind
    content: str = Field(min_length=1, max_length=8_000)
    source_ids: tuple[str, ...] = Field(default=(), max_length=32)
    untrusted: Literal[True] = True
    proposal_id: str | None = Field(default=None, max_length=128)

    @field_validator("source_ids")
    @classmethod
    def validate_source_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _SAFE_ID.fullmatch(item) for item in value):
            raise ValueError("source IDs must be bounded safe identifiers")
        if len(set(value)) != len(value):
            raise ValueError("source IDs must be unique")
        return value

    @field_validator("proposal_id")
    @classmethod
    def validate_proposal_id(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_ID.fullmatch(value):
            raise ValueError("proposal ID must be a bounded safe identifier")
        return value

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip() or any(
            ord(char) < 32 and char not in "\t\n" for char in value
        ):
            raise ValueError("content contains control characters")
        return value

    @model_validator(mode="after")
    def validate_kind_fields(self) -> ToolResult:
        if self.kind == ToolResultKind.PROPOSAL and not self.proposal_id:
            raise ValueError("proposal result requires a proposal ID")
        if self.kind == ToolResultKind.DATA and self.proposal_id is not None:
            raise ValueError("data result cannot carry a proposal ID")
        return self


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    principal: Principal
    correlation_id: str
    authorization_checked: bool = False
    deadline_seconds: float = 5.0

    def __post_init__(self) -> None:
        if type(self.authorization_checked) is not bool:
            raise TypeError("authorization_checked must be a bool")
        if (
            not self.correlation_id.strip()
            or len(self.correlation_id) > 128
            or any(ord(char) < 32 for char in self.correlation_id)
        ):
            raise ValueError("correlation id must be bounded")
        if (
            not math.isfinite(self.deadline_seconds)
            or self.deadline_seconds <= 0
            or self.deadline_seconds > 60
        ):
            raise ValueError("tool deadline must be between 0 and 60 seconds")


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    policy: ToolPolicy
    input_model: type[BaseModel]
    requires_authentication: bool = True
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if type(self.requires_authentication) is not bool:
            raise TypeError("requires_authentication must be a bool")
        if not _SAFE_ID.fullmatch(self.name) or len(self.name) > 64:
            raise ValueError("tool name must be bounded")
        if (
            not self.description.strip()
            or len(self.description) > 500
            or any(ord(char) < 32 for char in self.description)
        ):
            raise ValueError("tool description must be bounded")
        if (
            not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or self.timeout_seconds > 60
        ):
            raise ValueError("tool timeout must be between 0 and 60 seconds")


@dataclass(frozen=True, slots=True)
class ToolAuditEvent:
    tool_name: str
    actor: str
    correlation_id: str
    outcome: Literal["allow", "deny", "error"]
    policy: ToolPolicy
    timestamp: datetime
    input_summary: dict[str, Any]
    output_summary: dict[str, Any] | None = None
    reason_code: str | None = None

    @classmethod
    def now(
        cls,
        *,
        tool_name: str,
        actor: str,
        correlation_id: str,
        outcome: Literal["allow", "deny", "error"],
        policy: ToolPolicy,
        input_summary: dict[str, Any],
        output_summary: dict[str, Any] | None = None,
        reason_code: str | None = None,
    ) -> ToolAuditEvent:
        return cls(
            tool_name=tool_name,
            actor=actor,
            correlation_id=correlation_id,
            outcome=outcome,
            policy=policy,
            timestamp=datetime.now(UTC),
            input_summary=input_summary,
            output_summary=output_summary,
            reason_code=reason_code,
        )
