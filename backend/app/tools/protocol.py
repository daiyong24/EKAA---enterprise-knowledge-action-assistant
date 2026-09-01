"""Typed tool protocol and registry with policy enforcement."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ValidationError

from backend.app.tools.audit import ToolAuditSink
from backend.app.tools.errors import (
    ToolAuthorizationError,
    ToolExecutionError,
    ToolInputError,
    ToolNotFoundError,
    ToolTimeoutError,
    ToolUnavailableError,
    WriteExecutionForbiddenError,
)
from backend.app.tools.models import (
    ToolAuditEvent,
    ToolDefinition,
    ToolExecutionContext,
    ToolPolicy,
    ToolResult,
    ToolResultKind,
    validate_nested_value,
)

logger = logging.getLogger(__name__)


class Tool(Protocol):
    definition: ToolDefinition

    async def execute(
        self, context: ToolExecutionContext, arguments: BaseModel
    ) -> ToolResult: ...


ToolHandler = Callable[[ToolExecutionContext, BaseModel], Awaitable[ToolResult]]


class ToolRegistry:
    """Execute registered tools independent of how an agent selected them."""

    def __init__(self, *, audit_sink: ToolAuditSink) -> None:
        self._tools: dict[str, tuple[ToolDefinition, ToolHandler | None]] = {}
        self._audit_sink = audit_sink

    def register(self, definition: ToolDefinition, handler: ToolHandler | None) -> None:
        if definition.name in self._tools:
            raise ValueError(f"tool already registered: {definition.name}")
        if definition.policy == ToolPolicy.PROPOSE_WRITE and handler is not None:
            raise ValueError("write tools have no executable handler")
        if definition.policy == ToolPolicy.READ_ONLY and handler is None:
            raise ValueError("read-only tools require a handler")
        # The optional value is narrowed at execution for read-only tools.
        self._tools[definition.name] = (definition, handler)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools[name][0] for name in sorted(self._tools))

    def definition(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name][0]
        except KeyError as exc:
            raise ToolNotFoundError("tool is not registered") from exc

    async def execute(
        self,
        name: str,
        arguments: Mapping[str, object],
        *,
        context: ToolExecutionContext,
    ) -> ToolResult:
        registered = self._tools.get(name)
        if registered is None:
            raise ToolNotFoundError("tool is not registered")
        definition, handler = registered
        actor = context.principal.subject or "anonymous"
        try:
            bounded_arguments = self._bounded_mapping(arguments)
        except (TypeError, ValueError) as exc:
            self._audit(
                definition,
                actor,
                context,
                "deny",
                {},
                reason="invalid_input",
            )
            raise ToolInputError("tool input must be a bounded object") from exc
        safe_input = self._summarize_input(bounded_arguments)
        try:
            if (
                definition.requires_authentication
                and not context.principal.authenticated
            ):
                raise ToolAuthorizationError("authentication is required")
            if not context.authorization_checked:
                raise ToolAuthorizationError(
                    "authorization must precede tool execution"
                )
            validated_arguments = validate_nested_value(bounded_arguments)
            if not isinstance(validated_arguments, dict):
                raise ValueError("tool arguments must be an object")
            model = definition.input_model.model_validate(validated_arguments)
        except (ValidationError, ValueError) as exc:
            self._audit(
                definition, actor, context, "deny", safe_input, reason="invalid_input"
            )
            raise ToolInputError("tool input rejected") from exc
        except ToolAuthorizationError:
            self._audit(
                definition,
                actor,
                context,
                "deny",
                safe_input,
                reason="authorization_denied",
            )
            raise
        except WriteExecutionForbiddenError:
            self._audit(
                definition,
                actor,
                context,
                "deny",
                safe_input,
                reason="write_execution_forbidden",
            )
            raise
        try:
            if definition.policy == ToolPolicy.PROPOSE_WRITE:
                result = self._build_proposal(model)
            else:
                if handler is None:
                    raise WriteExecutionForbiddenError(
                        "read-only tool handler is missing"
                    )
                result = await asyncio.wait_for(
                    handler(context, model), timeout=definition.timeout_seconds
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            self._audit(
                definition, actor, context, "error", safe_input, reason="tool_timeout"
            )
            raise ToolTimeoutError("tool timed out") from exc
        except (ToolUnavailableError, ToolExecutionError):
            self._audit(
                definition, actor, context, "error", safe_input, reason="tool_failed"
            )
            raise
        except Exception as exc:
            logger.warning(
                "tool_execution_failed tool=%s error_type=%s",
                name,
                type(exc).__name__,
            )
            self._audit(
                definition, actor, context, "error", safe_input, reason="tool_failed"
            )
            raise ToolExecutionError("tool failed") from exc
        if not isinstance(result, ToolResult):
            self._audit(
                definition,
                actor,
                context,
                "error",
                safe_input,
                reason="invalid_output",
            )
            raise ToolExecutionError("tool returned invalid result")
        try:
            result = ToolResult.model_validate(result.model_dump())
        except ValidationError as exc:
            self._audit(
                definition,
                actor,
                context,
                "error",
                safe_input,
                reason="invalid_output",
            )
            raise ToolExecutionError("tool returned invalid result") from exc
        if definition.policy == ToolPolicy.PROPOSE_WRITE and (
            result.kind != ToolResultKind.PROPOSAL or not result.proposal_id
        ):
            self._audit(
                definition,
                actor,
                context,
                "error",
                safe_input,
                reason="invalid_proposal_output",
            )
            raise ToolExecutionError("proposal output is invalid")
        if (
            definition.policy == ToolPolicy.READ_ONLY
            and result.kind != ToolResultKind.DATA
        ):
            self._audit(
                definition,
                actor,
                context,
                "error",
                safe_input,
                reason="invalid_read_output",
            )
            raise ToolExecutionError("read tool returned a proposal")
        safe_output: dict[str, object] = {
            "kind": result.kind.value,
            "content_length": len(result.content),
            "source_count": len(result.source_ids),
            "has_proposal_id": result.proposal_id is not None,
        }
        self._audit(definition, actor, context, "allow", safe_input, safe_output)
        return result

    def _audit(
        self,
        definition: ToolDefinition,
        actor: str,
        context: ToolExecutionContext,
        outcome: str,
        input_summary: dict[str, object],
        output_summary: dict[str, object] | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        self._audit_sink.record(
            ToolAuditEvent.now(
                tool_name=definition.name,
                actor=actor,
                correlation_id=context.correlation_id,
                outcome=cast("Literal['allow', 'deny', 'error']", outcome),
                policy=definition.policy,
                input_summary=input_summary,
                output_summary=output_summary,
                reason_code=reason,
            )
        )

    @staticmethod
    def _summarize_input(arguments: Mapping[str, object]) -> dict[str, object]:
        """Keep audit input structural; values may contain protected content."""
        summary: dict[str, object] = {}
        for index, (key, value) in enumerate(arguments.items()):
            if index >= 32:
                break
            key_text = str(key)
            if key_text.lower() in {
                "secret",
                "token",
                "password",
                "api_key",
                "authorization",
                "cookie",
            }:
                summary[key_text] = "[redacted]"
            elif isinstance(value, str):
                summary[key_text] = {"type": "string", "length": len(value)}
            elif isinstance(value, Mapping):
                summary[key_text] = {"type": "object", "field_count": len(value)}
            elif isinstance(value, (list, tuple)):
                summary[key_text] = {"type": "array", "item_count": len(value)}
            else:
                summary[key_text] = {"type": type(value).__name__}
        return summary

    @staticmethod
    def _bounded_mapping(arguments: object) -> dict[str, object]:
        """Materialize at most 32 top-level items and reject invalid mappings."""
        if not isinstance(arguments, Mapping):
            raise TypeError("tool arguments must be a mapping")
        try:
            declared_size = len(arguments)
        except Exception as exc:
            raise TypeError("tool argument size is unavailable") from exc
        if declared_size > 32:
            raise ValueError("too many top-level arguments")
        bounded: dict[str, object] = {}
        for index, (key, value) in enumerate(arguments.items()):
            if index >= 32:
                raise ValueError("too many top-level arguments")
            if not isinstance(key, str):
                raise ValueError("tool argument keys must be strings")
            bounded[key] = value
        return bounded

    @staticmethod
    def _build_proposal(model: BaseModel) -> ToolResult:
        """Build a proposal in-process; no callback or external side effect exists."""
        values = model.model_dump()
        action = values.get("action")
        parameters = values.get("parameters", {})
        if (
            not isinstance(action, str)
            or not action.strip()
            or not isinstance(parameters, dict)
        ):
            raise ToolInputError("proposal input is invalid")
        payload = json.dumps(values, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        return ToolResult(
            kind=ToolResultKind.PROPOSAL,
            content=(
                f"Action proposed for explicit future human approval: {action.strip()}"
            ),
            proposal_id=f"proposal-{digest}",
        )


class ProposalOnlyHandler:
    """Deprecated marker retained for import compatibility.

    The registry rejects every executable write handler.  Proposal construction is
    performed internally by ``ToolRegistry._build_proposal`` instead.
    """
