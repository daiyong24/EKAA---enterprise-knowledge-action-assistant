from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from backend.app.authorization.models import Principal
from backend.app.tools.audit import InMemoryToolAuditSink
from backend.app.tools.builtins import (
    ChangeProposalInput,
    KnowledgeLookupInput,
    PublicUrlInput,
    PublicUrlReaderHandler,
    register_baseline_tools,
)
from backend.app.tools.errors import (
    ToolAuthorizationError,
    ToolInputError,
    ToolTimeoutError,
)
from backend.app.tools.models import (
    ToolDefinition,
    ToolExecutionContext,
    ToolPolicy,
    ToolResult,
    ToolResultKind,
)
from backend.app.tools.protocol import ToolRegistry


def context(*, checked: bool = True) -> ToolExecutionContext:
    return ToolExecutionContext(
        principal=Principal("alice", roles=frozenset({"employee"})),
        correlation_id="corr-1",
        authorization_checked=checked,
    )


@pytest.mark.anyio
async def test_registry_executes_tool_without_agent_selection() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(audit_sink=sink)

    async def handler(
        _context: ToolExecutionContext, _arguments: BaseModel
    ) -> ToolResult:
        return ToolResult(kind=ToolResultKind.DATA, content="safe data")

    registry.register(
        ToolDefinition(
            name="read",
            description="read",
            policy=ToolPolicy.READ_ONLY,
            input_model=KnowledgeLookupInput,
        ),
        handler,
    )
    result = await registry.execute("read", {"query": "status"}, context=context())
    assert result.content == "safe data"
    assert sink.snapshot()[0].outcome == "allow"


@pytest.mark.anyio
async def test_schema_rejects_extra_and_invalid_input() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(audit_sink=sink)
    register_baseline_tools(registry)
    with pytest.raises(Exception) as error:
        await registry.execute(
            "knowledge_lookup", {"query": "", "unexpected": "x"}, context=context()
        )
    assert isinstance(error.value, ToolInputError)


@pytest.mark.anyio
async def test_authorization_is_required_before_tool() -> None:
    registry = ToolRegistry(audit_sink=InMemoryToolAuditSink())
    register_baseline_tools(registry)
    with pytest.raises(ToolAuthorizationError):
        await registry.execute(
            "knowledge_lookup", {"query": "status"}, context=context(checked=False)
        )


@pytest.mark.anyio
async def test_write_tool_only_returns_proposal_and_is_deterministic() -> None:
    registry = ToolRegistry(audit_sink=InMemoryToolAuditSink())
    register_baseline_tools(registry)
    first = await registry.execute(
        "change_proposal",
        {"action": "restart synthetic service", "parameters": {"service": "demo"}},
        context=context(),
    )
    second = await registry.execute(
        "change_proposal",
        {"action": "restart synthetic service", "parameters": {"service": "demo"}},
        context=context(),
    )
    assert first.kind == ToolResultKind.PROPOSAL
    assert first.proposal_id == second.proposal_id
    assert first.content.startswith("Action proposed")


def test_write_registration_rejects_executable_handler() -> None:
    registry = ToolRegistry(audit_sink=InMemoryToolAuditSink())

    async def forbidden(
        _context: ToolExecutionContext, _arguments: BaseModel
    ) -> ToolResult:
        return ToolResult(kind=ToolResultKind.PROPOSAL, content="x", proposal_id="p")

    with pytest.raises(ValueError, match="no executable handler"):
        registry.register(
            ToolDefinition(
                name="write",
                description="write",
                policy=ToolPolicy.PROPOSE_WRITE,
                input_model=KnowledgeLookupInput,
            ),
            forbidden,
        )


def test_required_inputs_and_result_metadata_are_strictly_bounded() -> None:
    with pytest.raises(ValueError):
        KnowledgeLookupInput(query="   ")
    with pytest.raises(ValueError):
        ChangeProposalInput(action=" \t ")
    with pytest.raises(ValueError):
        ToolResult(
            kind=ToolResultKind.DATA,
            content="ok",
            source_ids=("not safe id with spaces",),
        )
    with pytest.raises(ValueError):
        ToolDefinition(
            name="finite",
            description="finite",
            policy=ToolPolicy.READ_ONLY,
            input_model=KnowledgeLookupInput,
            timeout_seconds=float("inf"),
        )


def test_authorization_proof_is_strictly_boolean() -> None:
    with pytest.raises(TypeError):
        ToolExecutionContext(
            principal=Principal("alice"),
            correlation_id="corr",
            authorization_checked=1,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("requires_authentication", [0, 1, "true", None])
def test_tool_authentication_requirement_is_strictly_boolean(
    requires_authentication: object,
) -> None:
    with pytest.raises(TypeError):
        ToolDefinition(
            name="strict-auth",
            description="strict auth",
            policy=ToolPolicy.READ_ONLY,
            input_model=KnowledgeLookupInput,
            requires_authentication=requires_authentication,  # type: ignore[arg-type]
        )


@pytest.mark.anyio
async def test_registry_rejects_oversized_and_non_mapping_input() -> None:
    registry = ToolRegistry(audit_sink=InMemoryToolAuditSink())
    register_baseline_tools(registry)
    with pytest.raises(ToolInputError):
        await registry.execute(
            "knowledge_lookup",
            ["not", "an", "object"],  # type: ignore[arg-type]
            context=context(),
        )
    with pytest.raises(ToolInputError):
        await registry.execute(
            "knowledge_lookup",
            {f"key-{index}": "x" for index in range(33)},
            context=context(),
        )


@pytest.mark.anyio
async def test_url_tool_rejects_ssrf_targets_before_callback() -> None:
    called = False

    async def reader(_url: str, _context: ToolExecutionContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(kind=ToolResultKind.DATA, content="unexpected")

    handler = PublicUrlReaderHandler(reader, allowed_hosts=frozenset({"example.com"}))
    with pytest.raises(ToolInputError):
        await handler(context(), PublicUrlInput(url="http://127.0.0.1/metadata"))
    assert called is False


@pytest.mark.anyio
async def test_tool_timeout_maps_to_safe_error() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(audit_sink=sink)

    async def slow(_context: ToolExecutionContext, _arguments: BaseModel) -> ToolResult:
        await asyncio.sleep(0.05)
        return ToolResult(kind=ToolResultKind.DATA, content="late")

    registry.register(
        ToolDefinition(
            name="slow",
            description="slow",
            policy=ToolPolicy.READ_ONLY,
            input_model=KnowledgeLookupInput,
            timeout_seconds=0.001,
        ),
        slow,
    )
    with pytest.raises(ToolTimeoutError):
        await registry.execute("slow", {"query": "x"}, context=context())
    assert sink.snapshot()[0].reason_code == "tool_timeout"


@pytest.mark.anyio
async def test_audit_redacts_secret_input_and_output() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(audit_sink=sink)

    class AuditInput(BaseModel):
        query: str
        api_key: str
        header: str

    async def handler(
        _context: ToolExecutionContext, _arguments: BaseModel
    ) -> ToolResult:
        return ToolResult(
            kind=ToolResultKind.DATA,
            content="safe secret embedded",
        )

    registry.register(
        ToolDefinition(
            name="audit",
            description="audit",
            policy=ToolPolicy.READ_ONLY,
            input_model=AuditInput,
        ),
        handler,
    )
    await registry.execute(
        "audit",
        {"query": "x", "api_key": "super-secret", "header": "Bearer secret"},
        context=context(),
    )
    event = sink.snapshot()[0]
    assert event.input_summary["api_key"] == "[redacted]"
    assert event.input_summary["header"] == {"type": "string", "length": 13}
    assert event.output_summary == {
        "kind": "data",
        "content_length": 20,
        "source_count": 0,
        "has_proposal_id": False,
    }
    assert "safe" not in str(event.output_summary)
