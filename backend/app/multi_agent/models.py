"""Provider-neutral contracts for a bounded multi-agent candidate."""

from __future__ import annotations

import hashlib
import json
import math
import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")


class WorkerRole(StrEnum):
    SINGLE = "single-agent"
    ROUTER = "router"
    RESEARCHER = "researcher"
    VERIFIER = "verifier"
    SYNTHESIZER = "synthesizer"


class WorkerStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    REFUSED = "refused"


EVALUATION_CAPABILITY_TOKEN: Literal["evaluation-read-only-v1"] = (
    "evaluation-read-only-v1"
)


class RunStatus(StrEnum):
    COMPLETED = "completed"
    FALLBACK_SINGLE_AGENT = "fallback_single_agent"
    FAILED = "failed"
    CONFLICT_REQUIRES_REVIEW = "conflict_requires_review"
    TERMINATED = "terminated"


class Claim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def safe(self) -> Claim:
        if not _SAFE_ID.fullmatch(self.key):
            raise ValueError("claim key is invalid")
        if any(ord(c) < 32 and c not in "\t\n" for c in self.value):
            raise ValueError("claim value contains control characters")
        return self


class RunBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_handoffs: int = Field(default=3, ge=0, le=16)
    max_worker_steps: int = Field(default=16, ge=1, le=100)
    max_worker_retries: int = Field(default=4, ge=0, le=50)
    max_worker_calls: int = Field(default=8, ge=1, le=50)
    max_latency_ms: float = Field(default=120_000, gt=0, le=3_600_000)

    @model_validator(mode="after")
    def finite_latency(self) -> RunBudget:
        if not math.isfinite(self.max_latency_ms):
            raise ValueError("latency budget must be finite")
        return self


class PermissionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    principal_fingerprint: str = Field(min_length=64, max_length=64)
    authorization_checked: bool = False
    approval_request_id: str | None = Field(default=None, max_length=128)
    action_hash: str | None = Field(default=None, max_length=64)
    parameter_hash: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_binding(self) -> PermissionContext:
        for value in (
            self.principal_fingerprint,
            self.action_hash,
            self.parameter_hash,
        ):
            if value is not None and (
                len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            ):
                raise ValueError("permission digest is invalid")
        if self.approval_request_id is not None and not _SAFE_ID.fullmatch(
            self.approval_request_id
        ):
            raise ValueError("approval request identifier is invalid")
        if self.approval_request_id is not None and (
            not self.authorization_checked
            or self.action_hash is None
            or self.parameter_hash is None
        ):
            raise ValueError("approval binding requires authorization proof")
        if (self.action_hash is None) != (self.parameter_hash is None):
            raise ValueError("approval binding must include both hashes")
        return self


class SharedState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=128)
    correlation_id: str = Field(min_length=1, max_length=128)
    user_message: str = Field(min_length=1, max_length=8_000)
    permission: PermissionContext
    revision: int = Field(default=0, ge=0, le=100)
    claims: tuple[Claim, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_ids(self) -> SharedState:
        if not _SAFE_ID.fullmatch(self.task_id) or not _SAFE_ID.fullmatch(
            self.correlation_id
        ):
            raise ValueError("shared state identifier is invalid")
        return self

    def digest(self) -> str:
        payload = self.model_dump(mode="json")
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class HandoffEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    from_worker: str
    to_worker: str
    sequence: int = Field(ge=1, le=16)
    state_revision: int = Field(ge=0, le=100)
    state_digest: str = Field(min_length=64, max_length=64)
    permission_digest: str = Field(min_length=64, max_length=64)
    claim_keys: tuple[str, ...] = Field(default=(), max_length=32)
    handoff_digest: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def safe_ids(self) -> HandoffEnvelope:
        for value in (self.task_id, self.from_worker, self.to_worker):
            if not _SAFE_ID.fullmatch(value):
                raise ValueError("handoff identifier is invalid")
        for value in (self.state_digest, self.permission_digest, self.handoff_digest):
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("handoff digest is invalid")
        if any(not _SAFE_ID.fullmatch(key) for key in self.claim_keys):
            raise ValueError("handoff claim key is invalid")
        if len(set(self.claim_keys)) != len(self.claim_keys):
            raise ValueError("handoff claim keys must be unique")
        return self


class WorkerInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: SharedState
    role: WorkerRole
    handoff: HandoffEnvelope | None = None
    capability_token: Literal["evaluation-read-only-v1"] = EVALUATION_CAPABILITY_TOKEN


class WorkerOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    worker_id: str = Field(min_length=1, max_length=128)
    status: WorkerStatus
    answer: str | None = Field(default=None, max_length=8_000)
    claims: tuple[Claim, ...] = Field(default=(), max_length=32)
    steps: int = Field(default=1, ge=0, le=100)
    retries: int = Field(default=0, ge=0, le=50)
    latency_ms: float = Field(default=0, ge=0, le=3_600_000)
    principal_fingerprint: str = Field(min_length=64, max_length=64)
    authorization_checked: bool
    approval_request_id: str | None = Field(default=None, max_length=128)
    action_hash: str | None = Field(default=None, max_length=64)
    parameter_hash: str | None = Field(default=None, max_length=64)
    capability_token: Literal["evaluation-read-only-v1"]

    @field_validator("answer")
    @classmethod
    def safe_answer(cls, value: str | None) -> str | None:
        if value is not None and any(
            ord(char) < 32 and char not in "\t\n" for char in value
        ):
            raise ValueError("worker answer contains control characters")
        return value

    @model_validator(mode="after")
    def valid_output(self) -> WorkerOutput:
        if not _SAFE_ID.fullmatch(self.worker_id):
            raise ValueError("worker identifier is invalid")
        if (
            self.status == WorkerStatus.COMPLETED
            and not self.answer
            and not self.claims
        ):
            raise ValueError("completed worker must return bounded output")
        if not math.isfinite(self.latency_ms):
            raise ValueError("latency must be finite")
        for value in (
            self.principal_fingerprint,
            self.action_hash,
            self.parameter_hash,
        ):
            if value is not None and (
                len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            ):
                raise ValueError("worker permission digest is invalid")
        if self.approval_request_id is not None and not _SAFE_ID.fullmatch(
            self.approval_request_id
        ):
            raise ValueError("worker approval identifier is invalid")
        if (self.action_hash is None) != (self.parameter_hash is None):
            raise ValueError("worker approval digest must include both hashes")
        return self


class RoutePlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workers: tuple[str, ...] = Field(min_length=1, max_length=8)
    reason_code: Literal["single_agent_default", "specialized_candidate"]
    fallback_to_single_agent: bool = True

    @model_validator(mode="after")
    def valid_workers(self) -> RoutePlan:
        if any(not _SAFE_ID.fullmatch(item) for item in self.workers):
            raise ValueError("worker identifier is invalid")
        if len(set(self.workers)) != len(self.workers):
            raise ValueError("route cannot repeat workers")
        return self


class MultiAgentRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=128)
    status: RunStatus
    # This candidate is evaluation-only. Factual answers cannot cross this boundary.
    answer: None = None
    evaluation_only: Literal[True] = True
    workers_used: tuple[str, ...] = ()
    handoffs: int = Field(ge=0)
    steps: int = Field(ge=0)
    retries: int = Field(ge=0)
    latency_ms: float = Field(ge=0, le=3_600_000)
    failure_code: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def consistent(self) -> MultiAgentRunResult:
        if not _SAFE_ID.fullmatch(self.task_id):
            raise ValueError("run task identifier is invalid")
        if not math.isfinite(self.latency_ms):
            raise ValueError("run latency must be finite")
        if self.status == RunStatus.COMPLETED and self.failure_code is not None:
            raise ValueError("completed run cannot contain a failure")
        if self.status == RunStatus.FALLBACK_SINGLE_AGENT and (
            self.workers_used != ("single-agent",) or self.failure_code is None
        ):
            raise ValueError("fallback result is inconsistent")
        if (
            self.status
            in {
                RunStatus.FAILED,
                RunStatus.CONFLICT_REQUIRES_REVIEW,
                RunStatus.TERMINATED,
            }
            and self.answer is not None
        ):
            raise ValueError("failed run cannot contain a factual answer")
        return self
