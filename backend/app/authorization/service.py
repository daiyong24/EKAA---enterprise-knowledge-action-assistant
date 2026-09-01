"""Authorization policy and pre-retrieval document-ID resolution."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from contextvars import ContextVar
from typing import Protocol, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.authorization.audit import AuditSink
from backend.app.authorization.errors import (
    AuthorizationDeniedError,
    AuthorizationUnavailableError,
    DocumentNotFoundError,
    InvalidDocumentPolicyError,
)
from backend.app.authorization.models import (
    ANONYMOUS,
    AuthorizationAuditEvent,
    Principal,
)
from backend.app.db.models import Document
from backend.app.embeddings.models import HybridSearchResult, RetrievedChunk


class DocumentPolicyReader(Protocol):
    def list_policies(self, session: Session) -> Sequence[tuple[str, str, object]]: ...


class RetrievalWithAuthorization(Protocol):
    def search(
        self,
        *,
        query: str,
        top_k: int,
        allowed_document_ids: Sequence[str] | None = None,
    ) -> list[RetrievedChunk]: ...


class AuthorizationService:
    """Resolve only authorized IDs before either retrieval branch executes."""

    def __init__(self, *, audit_sink: AuditSink) -> None:
        self._audit_sink = audit_sink

    def can_read(
        self,
        principal: Principal,
        *,
        classification: str,
        allowed_groups: Iterable[str],
    ) -> bool:
        if classification not in {"public", "internal", "confidential", "restricted"}:
            return False
        if classification == "public":
            return True
        if not principal.authenticated:
            return False
        if principal.is_admin:
            return True
        if classification == "internal":
            return True
        return bool(principal.groups & frozenset(allowed_groups))

    def authorized_document_ids(
        self,
        session: Session,
        *,
        principal: Principal = ANONYMOUS,
        correlation_id: str | None = None,
    ) -> tuple[str, ...]:
        correlation = correlation_id or uuid4().hex
        try:
            rows = session.execute(
                select(
                    Document.id, Document.classification, Document.allowed_groups
                ).order_by(Document.id.asc())
            )
            allowed: list[str] = []
            for document_id, classification, groups in rows:
                normalized_groups = self._groups(groups)
                if self.can_read(
                    principal,
                    classification=classification,
                    allowed_groups=normalized_groups,
                ):
                    allowed.append(document_id)
            self._audit_sink.record(
                AuthorizationAuditEvent.now(
                    action="retrieval_authorization",
                    actor=principal.subject or "anonymous",
                    outcome="allow",
                    correlation_id=correlation,
                    reason_code="authorized_document_set_resolved",
                )
            )
            return tuple(allowed)
        except SQLAlchemyError as exc:
            self._audit_sink.record(
                AuthorizationAuditEvent.now(
                    action="retrieval_authorization",
                    actor=principal.subject or "anonymous",
                    outcome="error",
                    correlation_id=correlation,
                    reason_code="authorization_metadata_unavailable",
                )
            )
            raise AuthorizationUnavailableError from exc

    def record_admin_change(
        self,
        *,
        principal: Principal,
        correlation_id: str,
        resource_id: str,
        classification: str,
        allowed_groups: Sequence[str],
    ) -> None:
        self._audit_sink.record(
            AuthorizationAuditEvent.now(
                action="document_access_policy_update",
                actor=principal.subject or "anonymous",
                outcome="allow",
                correlation_id=correlation_id,
                resource_id=resource_id,
                reason_code=f"classification={classification};groups={len(allowed_groups)}",
            )
        )

    def update_document_policy(
        self,
        session: Session,
        *,
        principal: Principal,
        document_id: str,
        classification: str,
        allowed_groups: Sequence[str],
        correlation_id: str,
    ) -> tuple[str, ...]:
        if not principal.is_admin:
            self._audit_sink.record(
                AuthorizationAuditEvent.now(
                    action="document_access_policy_update",
                    actor=principal.subject or "anonymous",
                    outcome="deny",
                    correlation_id=correlation_id,
                    resource_id=document_id,
                    reason_code="admin_role_required",
                )
            )
            raise AuthorizationDeniedError
        if classification not in {"public", "internal", "confidential", "restricted"}:
            raise InvalidDocumentPolicyError
        normalized = tuple(dict.fromkeys(group.strip() for group in allowed_groups))
        if any(not group or len(group) > 128 for group in normalized):
            raise InvalidDocumentPolicyError
        document = session.get(Document, document_id)
        if document is None:
            raise DocumentNotFoundError
        document.classification = classification
        document.allowed_groups = list(normalized)
        self.record_admin_change(
            principal=principal,
            correlation_id=correlation_id,
            resource_id=document_id,
            classification=classification,
            allowed_groups=normalized,
        )
        return normalized

    @staticmethod
    def _groups(value: object) -> frozenset[str]:
        if not isinstance(value, list | tuple):
            return frozenset()
        return frozenset(item for item in value if isinstance(item, str) and item)


class AuthorizedRetrievalService:
    """Adapter that resolves ACLs before delegating to vector/keyword retrieval."""

    requires_authorization = True

    def __init__(
        self,
        *,
        retrieval_service: RetrievalWithAuthorization,
        authorization_service: AuthorizationService,
        session_factory: Callable[[], Session],
    ) -> None:
        self._retrieval_service = retrieval_service
        self._authorization_service = authorization_service
        self._session_factory = session_factory
        self.supports_diagnostics = hasattr(
            retrieval_service, "search_with_diagnostics"
        )

    def _allowed_ids(self) -> tuple[str, ...]:
        session = self._session_factory()
        try:
            return self._authorization_service.authorized_document_ids(
                session,
                principal=current_principal(),
                correlation_id=current_correlation_id(),
            )
        finally:
            session.close()

    def search(self, *, query: str, top_k: int) -> list[RetrievedChunk]:
        return self._retrieval_service.search(
            query=query, top_k=top_k, allowed_document_ids=self._allowed_ids()
        )

    def search_with_diagnostics(self, *, query: str, top_k: int) -> HybridSearchResult:
        if not hasattr(self._retrieval_service, "search_with_diagnostics"):
            raise AttributeError("diagnostics are available only for hybrid retrieval")
        method = cast(
            Callable[..., HybridSearchResult],
            self._retrieval_service.search_with_diagnostics,
        )
        return method(
            query=query, top_k=top_k, allowed_document_ids=self._allowed_ids()
        )


_principal_context: ContextVar[Principal] = ContextVar(
    "ekaa_authorization_principal", default=ANONYMOUS
)
_correlation_context: ContextVar[str | None] = ContextVar(
    "ekaa_authorization_correlation", default=None
)


class PrincipalContext:
    """Request-scoped identity context isolated across concurrent requests."""

    def __init__(self, principal: Principal, correlation_id: str | None = None) -> None:
        self.principal = principal
        self.correlation_id = correlation_id
        self._token: object | None = None
        self._correlation_token: object | None = None

    def __enter__(self) -> PrincipalContext:
        self._token = _principal_context.set(self.principal)
        self._correlation_token = _correlation_context.set(self.correlation_id)
        return self

    def __exit__(self, *_: object) -> None:
        if self._token is not None:
            _principal_context.reset(self._token)  # type: ignore[arg-type]
        if self._correlation_token is not None:
            _correlation_context.reset(self._correlation_token)  # type: ignore[arg-type]


def current_principal() -> Principal:
    return _principal_context.get()


def current_correlation_id() -> str | None:
    return _correlation_context.get()
