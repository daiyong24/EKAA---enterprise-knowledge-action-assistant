"""HTTP transport for the narrow Stage 2 document-ingestion capability."""

from datetime import datetime

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from backend.app.core.exceptions import ApplicationError
from backend.app.ingestion.errors import (
    DocumentParseError,
    DocumentTooLargeError,
    EmptyDocumentError,
    IngestionStorageError,
    InvalidDocumentError,
)
from backend.app.ingestion.models import IngestionResult
from backend.app.ingestion.service import IngestionService

router = APIRouter(tags=["documents"])


class DocumentUploadResponse(BaseModel):
    """Traceable ingestion result; no original upload content is returned."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    filename: str
    mime_type: str
    checksum: str
    status: str
    chunk_count: int = Field(ge=1)
    created_at: datetime
    duplicate: bool


def _to_response(result: IngestionResult) -> DocumentUploadResponse:
    return DocumentUploadResponse(**result.__dict__)


@router.post(
    "/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    request: Request, response: Response
) -> DocumentUploadResponse:
    """Validate and atomically store normalized PDF, TXT, or Markdown chunks."""
    service: IngestionService = request.app.state.ingestion_service
    try:
        # Stage 2 accepts exactly one multipart field, named `file`.
        async with request.form(max_files=100, max_fields=100) as form:
            fields = form.multi_items()
            if len(fields) != 1 or fields[0][0] != "file":
                raise InvalidDocumentError
            upload = fields[0][1]
            if not isinstance(upload, UploadFile):
                raise InvalidDocumentError
            # Separate file-byte guard after the request-wide ASGI cap.
            payload = await upload.read(service.max_file_size_bytes + 1)
            result = service.ingest(
                filename=upload.filename,
                content_type=upload.content_type,
                payload=payload,
            )
    except HTTPException as exc:
        raise ApplicationError(
            code="invalid_document",
            message="The uploaded document type or filename is not allowed.",
            status_code=422,
        ) from exc
    except DocumentTooLargeError as exc:
        raise ApplicationError(
            code="document_too_large",
            message="The document exceeds the allowed size.",
            status_code=413,
        ) from exc
    except InvalidDocumentError as exc:
        raise ApplicationError(
            code="invalid_document",
            message="The uploaded document type or filename is not allowed.",
            status_code=422,
        ) from exc
    except DocumentParseError as exc:
        raise ApplicationError(
            code="document_parse_failed",
            message="The document could not be parsed.",
            status_code=422,
        ) from exc
    except EmptyDocumentError as exc:
        raise ApplicationError(
            code="empty_document",
            message="The document contains no usable text.",
            status_code=422,
        ) from exc
    except IngestionStorageError as exc:
        raise ApplicationError(
            code="document_storage_unavailable",
            message="The document could not be stored. Please try again later.",
            status_code=503,
        ) from exc
    result_response = _to_response(result)
    if result_response.duplicate:
        # A checksum duplicate is successful idempotent replay, not a second create.
        response.status_code = status.HTTP_200_OK
    return result_response
