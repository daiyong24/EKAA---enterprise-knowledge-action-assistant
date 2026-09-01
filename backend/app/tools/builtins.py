"""Small deterministic tools used by the Stage 11/12 baseline and tests."""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable
from typing import cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from backend.app.tools.errors import ToolInputError
from backend.app.tools.models import (
    ToolDefinition,
    ToolExecutionContext,
    ToolPolicy,
    ToolResult,
    ToolResultKind,
    validate_nested_value,
)
from backend.app.tools.protocol import ToolRegistry


class KnowledgeLookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=1_000)

    @field_validator("query")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(char) < 32 and char not in "\t\n" for char in value):
            raise ValueError("control characters are not allowed")
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be whitespace")
        return normalized


class PublicUrlInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: HttpUrl


class ChangeProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1, max_length=200)
    parameters: dict[str, object] = Field(default_factory=dict, max_length=32)

    @field_validator("action")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("control characters are not allowed")
        normalized = value.strip()
        if not normalized:
            raise ValueError("action must not be whitespace")
        return normalized

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, object]) -> dict[str, object]:
        return cast(dict[str, object], validate_nested_value(value))


KnowledgeLookup = Callable[[str, ToolExecutionContext], Awaitable[ToolResult]]
PublicUrlReader = Callable[[str, ToolExecutionContext], Awaitable[ToolResult]]


async def _default_lookup(query: str, _context: ToolExecutionContext) -> ToolResult:
    return ToolResult(
        kind=ToolResultKind.DATA,
        content=f"No configured knowledge integration for query: {query}",
    )


async def _default_url_reader(_url: str, _context: ToolExecutionContext) -> ToolResult:
    raise ToolInputError("no public URL reader is configured")


class KnowledgeLookupHandler:
    def __init__(self, callback: KnowledgeLookup) -> None:
        self._callback = callback

    async def __call__(
        self, context: ToolExecutionContext, arguments: BaseModel
    ) -> ToolResult:
        parsed = KnowledgeLookupInput.model_validate(arguments.model_dump())
        return await self._callback(parsed.query, context)


class PublicUrlReaderHandler:
    """Read-only URL seam with an explicit host allowlist.

    The callback is injected so tests never make network calls.  Literal private,
    loopback, link-local, multicast and unspecified addresses are rejected before
    the callback.  Host allowlisting remains mandatory to prevent DNS rebinding;
    callers should resolve and enforce it again at their network boundary.
    """

    def __init__(
        self, callback: PublicUrlReader, *, allowed_hosts: frozenset[str]
    ) -> None:
        self._callback = callback
        self._allowed_hosts = frozenset(
            host.lower().rstrip(".") for host in allowed_hosts
        )

    async def __call__(
        self, context: ToolExecutionContext, arguments: BaseModel
    ) -> ToolResult:
        parsed = PublicUrlInput.model_validate(arguments.model_dump())
        parts = urlsplit(str(parsed.url))
        host = (parts.hostname or "").lower().rstrip(".")
        if parts.scheme != "https" or not host or parts.username or parts.password:
            raise ToolInputError("URL must be HTTPS without credentials")
        if host not in self._allowed_hosts or _is_blocked_address(host):
            raise ToolInputError("URL host is not allowed")
        return await self._callback(str(parsed.url), context)


def _is_blocked_address(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host in {"localhost", "metadata.google.internal", "host.docker.internal"}
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_unspecified,
            address.is_reserved,
        )
    )


def register_baseline_tools(
    registry: ToolRegistry,
    *,
    lookup: KnowledgeLookup = _default_lookup,
    url_reader: PublicUrlReader = _default_url_reader,
    allowed_url_hosts: frozenset[str] = frozenset(),
) -> None:
    registry.register(
        ToolDefinition(
            name="knowledge_lookup",
            description="Look up authorized knowledge-base evidence.",
            policy=ToolPolicy.READ_ONLY,
            input_model=KnowledgeLookupInput,
            timeout_seconds=5.0,
        ),
        KnowledgeLookupHandler(lookup),
    )
    registry.register(
        ToolDefinition(
            name="public_url_read",
            description="Read an allowlisted public HTTPS URL.",
            policy=ToolPolicy.READ_ONLY,
            input_model=PublicUrlInput,
            timeout_seconds=5.0,
        ),
        PublicUrlReaderHandler(url_reader, allowed_hosts=allowed_url_hosts),
    )
    registry.register(
        ToolDefinition(
            name="change_proposal",
            description="Prepare a write proposal for a later approval workflow.",
            policy=ToolPolicy.PROPOSE_WRITE,
            input_model=ChangeProposalInput,
            timeout_seconds=2.0,
        ),
        None,
    )
