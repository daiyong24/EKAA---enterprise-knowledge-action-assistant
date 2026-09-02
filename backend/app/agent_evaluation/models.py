"""Strict contracts for agent evaluation inputs and content-free results."""

from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.tools.models import validate_nested_value

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")


class ToolCallOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    PROPOSED = "proposed"


class ExpectedToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tool_name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=32)

    @field_validator("tool_name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("tool name is invalid")
        return value

    @field_validator("arguments")
    @classmethod
    def bounded_args(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_nested_value(value)  # type: ignore[return-value]


class TraceToolCall(ExpectedToolCall):
    step: int = Field(ge=1, le=100)
    outcome: ToolCallOutcome
    approval_required: bool = False
    approval_status: str | None = Field(default=None, max_length=32)


class Scenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scenario_id: str = Field(min_length=1, max_length=128)
    expected_tool_calls: tuple[ExpectedToolCall, ...] = Field(default=(), max_length=32)
    expected_success: bool
    expected_escalated: bool = False
    approval_required: bool = False
    ambiguous: bool = False
    reviewer_required: bool = False

    @field_validator("scenario_id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("scenario ID is invalid")
        return value


class AgentTrace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: str = Field(min_length=1, max_length=128)
    scenario_id: str = Field(min_length=1, max_length=128)
    tool_calls: tuple[TraceToolCall, ...] = Field(default=(), max_length=32)
    success: bool
    escalated: bool = False
    approval_compliance: bool = True
    steps: int = Field(ge=0, le=100)
    retries: int = Field(ge=0, le=100)
    latency_ms: float = Field(ge=0, le=3_600_000)
    token_count: int | None = Field(default=None, ge=0, le=10_000_000)
    trace_id: str = Field(min_length=1, max_length=128)
    result_ref: str = Field(min_length=1, max_length=128)

    @field_validator("run_id", "scenario_id", "trace_id", "result_ref")
    @classmethod
    def safe_ids(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("trace identifier is invalid")
        return value

    @field_validator("latency_ms")
    @classmethod
    def finite_latency(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("latency must be finite")
        return value

    @model_validator(mode="after")
    def validate_trace_integrity(self) -> AgentTrace:
        steps = [call.step for call in self.tool_calls]
        if steps != sorted(set(steps)):
            raise ValueError("tool-call steps must be strictly increasing")
        if steps and max(steps) > self.steps:
            raise ValueError("tool-call step exceeds trace step budget")
        if len(self.tool_calls) > self.steps:
            raise ValueError("trace has more tool calls than recorded steps")
        if self.retries > self.steps:
            raise ValueError("retry count exceeds trace step budget")
        if (
            any(call.outcome == ToolCallOutcome.FAILED for call in self.tool_calls)
            and self.success
            and self.retries == 0
        ):
            raise ValueError("failed tool outcome cannot be successful without retry")
        return self


class AgentEvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scenario_id: str
    trace_id: str
    tool_accuracy: float
    argument_accuracy: float
    task_success: bool
    steps: int = Field(ge=0, le=100)
    retries: int = Field(ge=0, le=100)
    unnecessary_calls: int = Field(ge=0, le=100)
    escalation_correct: bool
    approval_compliance: bool
    latency_ms: float = Field(ge=0, le=3_600_000)
    estimated_cost: float | None = Field(default=None, ge=0)
    failure_categories: tuple[str, ...] = ()
    prioritized_fixes: tuple[str, ...] = ()
    requires_human_review: bool = False

    @field_validator(
        "tool_accuracy",
        "argument_accuracy",
        "latency_ms",
        "estimated_cost",
    )
    @classmethod
    def finite_metrics(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("evaluation metric must be finite")
        return value

    @field_validator("tool_accuracy", "argument_accuracy")
    @classmethod
    def bounded_accuracy(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("accuracy must be between 0 and 1")
        return value


class BatchEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: str
    scenario_count: int = Field(ge=1)
    successful_tasks: int
    success_rate: float
    mean_tool_accuracy: float
    mean_argument_accuracy: float
    mean_steps: float
    mean_retries: float
    mean_unnecessary_calls: float
    approval_compliance_rate: float
    mean_latency_ms: float
    estimated_cost: float | None = None
    cost_available: bool = False
    failure_categories: tuple[str, ...] = ()
    prioritized_fixes: tuple[str, ...] = ()
    results: tuple[AgentEvaluationResult, ...] = Field(min_length=1)
    evaluation_version: str | None = Field(default=None, max_length=64)
    corpus_version: str | None = Field(default=None, max_length=64)
    dataset_fingerprint: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_aggregate_integrity(self) -> BatchEvaluation:
        for label, version_value in (
            ("evaluation version", self.evaluation_version),
            ("corpus version", self.corpus_version),
        ):
            if version_value is not None and not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._/-]{0,63}", version_value
            ):
                raise ValueError(f"{label} is invalid")
        if self.dataset_fingerprint is not None and (
            len(self.dataset_fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in self.dataset_fingerprint)
        ):
            raise ValueError("dataset fingerprint is invalid")
        if self.scenario_count != len(self.results):
            raise ValueError("scenario count must equal result count")
        if len({result.scenario_id for result in self.results}) != self.scenario_count:
            raise ValueError("scenario result IDs must be unique")
        if len({result.trace_id for result in self.results}) != self.scenario_count:
            raise ValueError("trace result IDs must be unique")
        count = self.scenario_count
        if self.successful_tasks != sum(result.task_success for result in self.results):
            raise ValueError("successful task aggregate is inconsistent")
        expected = {
            "success_rate": sum(result.task_success for result in self.results) / count,
            "mean_tool_accuracy": sum(result.tool_accuracy for result in self.results)
            / count,
            "mean_argument_accuracy": sum(
                result.argument_accuracy for result in self.results
            )
            / count,
            "mean_steps": sum(result.steps for result in self.results) / count,
            "mean_retries": sum(result.retries for result in self.results) / count,
            "mean_unnecessary_calls": sum(
                result.unnecessary_calls for result in self.results
            )
            / count,
            "approval_compliance_rate": sum(
                result.approval_compliance for result in self.results
            )
            / count,
            "mean_latency_ms": sum(result.latency_ms for result in self.results)
            / count,
        }
        for field, value in expected.items():
            if not math.isclose(
                getattr(self, field), value, rel_tol=1e-9, abs_tol=1e-9
            ):
                raise ValueError(f"{field} aggregate is inconsistent")
        costs = [result.estimated_cost for result in self.results]
        if self.cost_available and self.estimated_cost is None:
            raise ValueError("estimated cost is required when cost is available")
        if self.cost_available != all(cost is not None for cost in costs):
            raise ValueError("cost availability aggregate is inconsistent")
        if self.cost_available and not math.isclose(
            self.estimated_cost or 0,
            sum(cost for cost in costs if cost is not None),
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError("estimated cost aggregate is inconsistent")
        if not self.cost_available and self.estimated_cost is not None:
            raise ValueError("estimated cost must be absent when unavailable")
        return self
