"""Explicit state and decision contracts for the bounded single-agent baseline."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.tools.models import validate_nested_value

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")


class AgentStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"
    TERMINATED = "terminated"


class AgentMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["user", "agent", "tool", "system"]
    content: str = Field(min_length=1, max_length=8_000)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if any(ord(char) < 32 and char not in "\t\n" for char in value):
            raise ValueError("message contains control characters")
        return value


class ToolCallDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["tool_call"]
    tool_name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, object] = Field(default_factory=dict, max_length=32)

    @field_validator("tool_name")
    @classmethod
    def validate_tool_name(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value) or len(value) > 64:
            raise ValueError("tool name is invalid")
        return value

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value: dict[str, object]) -> dict[str, object]:
        return cast(dict[str, object], validate_nested_value(value))


class NoToolDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["answer"]
    answer: str = Field(min_length=1, max_length=8_000)

    @field_validator("answer")
    @classmethod
    def validate_answer(cls, value: str) -> str:
        if not value.strip() or any(
            ord(char) < 32 and char not in "\t\n" for char in value
        ):
            raise ValueError("answer is invalid")
        return value


class TerminateDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["terminate"]
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("termination reason is invalid")
        return value


AgentDecision = ToolCallDecision | NoToolDecision | TerminateDecision


class AgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=128)
    correlation_id: str = Field(min_length=1, max_length=128)
    principal_subject: str | None = Field(default=None, max_length=128)
    principal_fingerprint: str = Field(min_length=64, max_length=64)
    authorization_checked_at_start: bool = False
    user_message: str = Field(min_length=1, max_length=8_000)
    status: AgentStatus = AgentStatus.IDLE
    step_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    messages: list[AgentMessage] = Field(default_factory=list, max_length=100)
    call_fingerprints: list[str] = Field(default_factory=list, max_length=32)
    last_tool_name: str | None = None
    pending_proposal_id: str | None = None
    termination_reason: str | None = None

    @field_validator("user_message")
    @classmethod
    def validate_user_message(cls, value: str) -> str:
        if not value.strip() or any(
            ord(char) < 32 and char not in "\t\n" for char in value
        ):
            raise ValueError("user message contains control characters")
        return value

    @field_validator("call_fingerprints")
    @classmethod
    def validate_fingerprints(cls, value: list[str]) -> list[str]:
        if any(
            len(item) != 64 or any(char not in "0123456789abcdef" for char in item)
            for item in value
        ):
            raise ValueError("call fingerprint is invalid")
        if len(set(value)) != len(value):
            raise ValueError("call fingerprints must be unique")
        return value

    @field_validator("run_id", "correlation_id")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("agent identifier is invalid")
        return value

    @field_validator("principal_fingerprint")
    @classmethod
    def validate_principal_fingerprint(cls, value: str) -> str:
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("principal fingerprint is invalid")
        return value

    @field_validator("pending_proposal_id")
    @classmethod
    def validate_pending_proposal_id(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_ID.fullmatch(value):
            raise ValueError("proposal identifier is invalid")
        return value


class AgentRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    status: AgentStatus
    answer: str | None = None
    pending_proposal_id: str | None = None
    steps: int
    retries: int
