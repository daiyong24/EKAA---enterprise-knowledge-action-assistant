"""HTTP adapters for identity resolution and request-scoped authorization."""

from collections.abc import Mapping
from uuid import uuid4

from fastapi import Request

from backend.app.authorization.errors import IdentityError, InvalidIdentityError
from backend.app.authorization.identity import IdentityProvider
from backend.app.authorization.models import AuthorizationAuditEvent, Principal
from backend.app.authorization.service import PrincipalContext
from backend.app.observability.tracing import current_correlation_id


def correlation_id(request: Request) -> str:
    """Use a bounded caller correlation ID or generate one; never use request text."""
    active = current_correlation_id()
    if active:
        return active
    value = request.headers.get("x-correlation-id", "")
    if len(value) <= 128 and all(
        character.isalnum() or character in "-_" for character in value
    ):
        return value or uuid4().hex
    return uuid4().hex


def resolve_principal(request: Request) -> Principal:
    provider: IdentityProvider = request.app.state.identity_provider
    headers: Mapping[str, str] = request.headers
    try:
        principal = provider.resolve(headers)
        principal.validate()
        return principal
    except IdentityError:
        request.app.state.audit_sink.record(
            AuthorizationAuditEvent.now(
                action="identity_resolution",
                actor="anonymous",
                outcome="error",
                correlation_id=correlation_id(request),
                reason_code="identity_provider_rejected",
            )
        )
        raise
    except (AttributeError, ValueError) as exc:
        request.app.state.audit_sink.record(
            AuthorizationAuditEvent.now(
                action="identity_resolution",
                actor="anonymous",
                outcome="error",
                correlation_id=correlation_id(request),
                reason_code="identity_claims_invalid",
            )
        )
        raise InvalidIdentityError("identity claims were rejected") from exc


def principal_context(request: Request) -> PrincipalContext:
    return PrincipalContext(
        resolve_principal(request), correlation_id=correlation_id(request)
    )
