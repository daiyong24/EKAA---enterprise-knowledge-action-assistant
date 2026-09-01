"""Minimal authorization administration controls."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.app.authorization.errors import (
    AuthorizationDeniedError,
    AuthorizationUnavailableError,
    DocumentNotFoundError,
    IdentityError,
    InvalidDocumentPolicyError,
    InvalidIdentityError,
)
from backend.app.authorization.http import correlation_id, resolve_principal
from backend.app.authorization.models import DocumentClassification
from backend.app.core.exceptions import ApplicationError

router = APIRouter(prefix="/admin", tags=["authorization-admin"])


class DocumentPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    classification: Literal["public", "internal", "confidential", "restricted"]
    allowed_groups: list[str] = Field(default_factory=list, max_length=100)


class DocumentPolicyResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    classification: DocumentClassification
    allowed_groups: list[str]


@router.put("/documents/{document_id}/access", response_model=DocumentPolicyResponse)
async def update_document_access(
    request: Request, document_id: str, body: DocumentPolicyRequest
) -> DocumentPolicyResponse:
    """Update ACL metadata without returning document content."""
    try:
        principal = resolve_principal(request)
        service = request.app.state.authorization_service
        with request.app.state.session_factory() as session:
            with session.begin():
                normalized_groups = service.update_document_policy(
                    session,
                    principal=principal,
                    document_id=document_id,
                    classification=body.classification,
                    allowed_groups=body.allowed_groups,
                    correlation_id=correlation_id(request),
                )
            return DocumentPolicyResponse(
                document_id=document_id,
                classification=body.classification,
                allowed_groups=list(normalized_groups),
            )
    except InvalidIdentityError as exc:
        raise ApplicationError(
            code="invalid_identity",
            message="Identity verification failed.",
            status_code=401,
        ) from exc
    except IdentityError as exc:
        raise ApplicationError(
            code="identity_unavailable",
            message="Identity verification is temporarily unavailable.",
            status_code=503,
        ) from exc
    except AuthorizationDeniedError as exc:
        raise ApplicationError(
            code="authorization_denied",
            message="You are not authorized to perform this action.",
            status_code=403,
        ) from exc
    except (DocumentNotFoundError, InvalidDocumentPolicyError) as exc:
        raise ApplicationError(
            code="invalid_document_access_policy",
            message="The document access policy is invalid.",
            status_code=422,
        ) from exc
    except AuthorizationUnavailableError as exc:
        raise ApplicationError(
            code="authorization_unavailable",
            message="Authorization is temporarily unavailable.",
            status_code=503,
        ) from exc
