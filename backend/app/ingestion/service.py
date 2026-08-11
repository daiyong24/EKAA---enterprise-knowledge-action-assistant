"""Application service that atomically ingests one validated public document."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.models import Document, DocumentChunk
from backend.app.ingestion.errors import (
    DocumentParseError,
    EmptyDocumentError,
    IngestionStorageError,
)
from backend.app.ingestion.models import IngestionResult
from backend.app.ingestion.pipeline import (
    normalize_and_chunk,
    parse_document,
    validate_upload,
)
from backend.app.ingestion.repository import DocumentRepository

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]


class IngestionService:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        max_file_size_bytes: int,
        max_pdf_pages: int,
        max_extracted_characters: int,
        chunk_size: int,
        chunk_overlap: int,
        repository: DocumentRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._max_file_size_bytes = max_file_size_bytes
        self._max_pdf_pages = max_pdf_pages
        self._max_extracted_characters = max_extracted_characters
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._repository = repository or DocumentRepository()

    @property
    def max_file_size_bytes(self) -> int:
        """Expose the bounded read limit without leaking mutable configuration."""
        return self._max_file_size_bytes

    def ingest(
        self, *, filename: str | None, content_type: str | None, payload: bytes
    ) -> IngestionResult:
        extension, mime_type = validate_upload(
            filename=filename,
            content_type=content_type,
            payload=payload,
            max_size_bytes=self._max_file_size_bytes,
        )
        # The validator establishes filename exists without trusting it as a path.
        assert filename is not None
        checksum = hashlib.sha256(payload).hexdigest()
        try:
            chunks = normalize_and_chunk(
                parse_document(
                    extension=extension,
                    payload=payload,
                    max_pdf_pages=self._max_pdf_pages,
                    max_extracted_characters=self._max_extracted_characters,
                ),
                chunk_size=self._chunk_size,
                overlap=self._chunk_overlap,
            )
        except DocumentParseError:
            logger.warning("document_parse_failed format=%s", extension)
            raise
        if not chunks:
            raise EmptyDocumentError

        session = self._session_factory()
        try:
            try:
                with session.begin():
                    existing = self._repository.get_by_checksum(session, checksum)
                    if existing is not None:
                        return _result_from_document(existing, duplicate=True)
                    completed_at = datetime.now(UTC)
                    document = Document(
                        filename=filename,
                        mime_type=mime_type,
                        checksum=checksum,
                        status="completed",
                        completed_at=completed_at,
                        metadata_json={
                            "source_size_bytes": len(payload),
                            "format": extension[1:],
                        },
                    )
                    document.chunks = [
                        DocumentChunk(
                            chunk_index=index,
                            content=chunk.content,
                            checksum=chunk.checksum,
                            page_number=chunk.page_number,
                            section=chunk.section,
                            metadata_json={"source": "parsed"},
                        )
                        for index, chunk in enumerate(chunks)
                    ]
                    self._repository.add(session, document)
                    session.flush()
                    result = _result_from_document(document, duplicate=False)
                return result
            except IntegrityError as exc:
                session.rollback()
                existing = self._repository.get_by_checksum(session, checksum)
                if existing is not None:
                    return _result_from_document(existing, duplicate=True)
                logger.error("document_persistence_integrity_error")
                raise IngestionStorageError from exc
        except SQLAlchemyError as exc:
            session.rollback()
            logger.error(
                "document_persistence_failed error_type=%s", type(exc).__name__
            )
            raise IngestionStorageError from exc
        finally:
            session.close()


def _result_from_document(document: Document, *, duplicate: bool) -> IngestionResult:
    return IngestionResult(
        document_id=document.id,
        filename=document.filename,
        mime_type=document.mime_type,
        checksum=document.checksum,
        status=document.status,
        chunk_count=len(document.chunks),
        created_at=document.created_at,
        duplicate=duplicate,
    )
