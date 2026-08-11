import logging
from collections.abc import Iterator
from pathlib import Path

import fitz
import pytest
from httpx import ASGITransport, AsyncByteStream, AsyncClient
from sqlalchemy import Engine, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.db.models import Base, Document, DocumentChunk
from backend.app.ingestion.errors import IngestionStorageError
from backend.app.ingestion.repository import DocumentRepository
from backend.app.ingestion.service import IngestionService
from backend.app.main import create_app


@pytest.fixture
def ingestion_app(tmp_path: Path) -> Iterator[object]:
    database_path = tmp_path / "ingestion-test.db"
    app = create_app(
        Settings(
            environment="test",
            log_level="CRITICAL",
            database_url=f"sqlite:///{database_path.as_posix()}",
            ingestion_max_file_size_bytes=5_000,
            ingestion_max_request_size_bytes=6_000,
            ingestion_max_pdf_pages=1,
            ingestion_max_extracted_characters=2_000,
            ingestion_chunk_size_characters=100,
            ingestion_chunk_overlap_characters=20,
            _env_file=None,
        )
    )
    engine: Engine = app.state.database_engine
    Base.metadata.create_all(engine)
    yield app
    engine.dispose()


async def _upload(
    app: object, *, filename: str, content_type: str, payload: bytes
) -> tuple[int, dict[str, object]]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/documents", files={"file": (filename, payload, content_type)}
        )
    return response.status_code, response.json()


class _SingleChunkStream(AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def __aiter__(self) -> Iterator[bytes]:
        yield self._body

    async def aclose(self) -> None:
        return None


def _pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Synthetic PDF document for Stage 2.")
    payload = document.tobytes()
    document.close()
    return payload


def _empty_pdf_bytes() -> bytes:
    document = fitz.open()
    document.new_page()
    payload = document.tobytes()
    document.close()
    return payload


def _two_page_pdf_bytes() -> bytes:
    document = fitz.open()
    for page_number in range(2):
        page = document.new_page()
        page.insert_text((72, 72), f"Synthetic page {page_number + 1}.")
    payload = document.tobytes()
    document.close()
    return payload


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filename", "content_type", "payload"),
    [
        ("notes.txt", "text/plain", b"Synthetic text document."),
        ("guide.md", "text/markdown", b"# Getting Started\n\nSynthetic markdown text."),
        ("sample.pdf", "application/pdf", _pdf_bytes()),
    ],
)
async def test_uploads_supported_formats_with_traceable_metadata(
    ingestion_app: object, filename: str, content_type: str, payload: bytes
) -> None:
    status, body = await _upload(
        ingestion_app, filename=filename, content_type=content_type, payload=payload
    )

    assert status == 201
    assert body["filename"] == filename
    assert body["mime_type"] == content_type
    assert body["status"] == "completed"
    assert body["chunk_count"] >= 1
    assert body["duplicate"] is False

    engine: Engine = ingestion_app.state.database_engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        document = session.scalar(
            select(Document).where(Document.id == body["document_id"])
        )
        assert document is not None
        chunks = list(
            session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document.id)
                .order_by(DocumentChunk.chunk_index)
            )
        )
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.content.strip() == chunk.content for chunk in chunks)
    assert all(len(chunk.checksum) == 64 for chunk in chunks)
    if filename == "sample.pdf":
        assert chunks[0].page_number == 1
    if filename == "guide.md":
        assert chunks[0].section == "Getting Started"


@pytest.mark.anyio
async def test_duplicate_upload_is_idempotent(ingestion_app: object) -> None:
    first_status, first = await _upload(
        ingestion_app,
        filename="first.txt",
        content_type="text/plain",
        payload=b"Same bytes",
    )
    second_status, second = await _upload(
        ingestion_app,
        filename="renamed.txt",
        content_type="text/plain",
        payload=b"Same bytes",
    )

    assert first_status == 201
    assert second_status == 200
    assert second["duplicate"] is True
    assert second["document_id"] == first["document_id"]
    assert second["filename"] == "first.txt"


@pytest.mark.anyio
async def test_extra_multipart_fields_and_files_are_rejected(
    ingestion_app: object,
) -> None:
    transport = ASGITransport(app=ingestion_app, raise_app_exceptions=False)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        field_response = await client.post(
            "/api/documents",
            data={"note": "untrusted extra field"},
            files={"file": ("notes.txt", b"sample", "text/plain")},
        )
        file_response = await client.post(
            "/api/documents",
            files=[
                ("file", ("first.txt", b"sample", "text/plain")),
                ("file", ("second.txt", b"sample", "text/plain")),
            ],
        )

    for response in (field_response, file_response):
        assert response.status_code == 422
        assert response.json() == {
            "error": {
                "code": "invalid_document",
                "message": "The uploaded document type or filename is not allowed.",
            }
        }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filename", "content_type", "payload", "expected_code"),
    [
        ("../secret.txt", "text/plain", b"sample", "invalid_document"),
        ("document.exe", "application/octet-stream", b"sample", "invalid_document"),
        ("notes.md", "text/plain", b"# Sample", "invalid_document"),
        ("empty.txt", "text/plain", b" \n\t", "empty_document"),
        ("empty.pdf", "application/pdf", _empty_pdf_bytes(), "empty_document"),
        ("broken.pdf", "application/pdf", b"%PDF-not-real", "document_parse_failed"),
    ],
)
async def test_upload_rejections_are_safe(
    ingestion_app: object,
    filename: str,
    content_type: str,
    payload: bytes,
    expected_code: str,
) -> None:
    status, body = await _upload(
        ingestion_app, filename=filename, content_type=content_type, payload=payload
    )

    assert status == 422
    assert body["error"]["code"] == expected_code
    assert filename not in str(body)


@pytest.mark.anyio
async def test_oversized_upload_has_bounded_safe_error(ingestion_app: object) -> None:
    payload = b"sensitive-" * 600

    status, body = await _upload(
        ingestion_app, filename="large.txt", content_type="text/plain", payload=payload
    )

    assert status == 413
    assert body == {
        "error": {
            "code": "document_too_large",
            "message": "The document exceeds the allowed size.",
        }
    }
    assert payload.decode() not in str(body)


@pytest.mark.anyio
@pytest.mark.parametrize("content_length", [None, "1"])
async def test_request_boundary_rejects_missing_or_forged_content_length(
    ingestion_app: object,
    content_length: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    headers = {"content-type": "multipart/form-data; boundary=boundary"}
    if content_length is not None:
        headers["content-length"] = content_length
    transport = ASGITransport(app=ingestion_app, raise_app_exceptions=False)  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/documents",
                headers=headers,
                content=_SingleChunkStream(b"x" * 6_001),
            )

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "document_too_large",
            "message": "The document exceeds the allowed size.",
        }
    }
    assert "document_request_too_large" in caplog.text
    assert "x" * 100 not in caplog.text


@pytest.mark.anyio
async def test_storage_failure_maps_to_safe_503(ingestion_app: object) -> None:
    class FailingStorageService:
        max_file_size_bytes = 5_000

        def ingest(self, **_: object) -> None:
            raise IngestionStorageError

    ingestion_app.state.ingestion_service = FailingStorageService()  # type: ignore[attr-defined]
    status, body = await _upload(
        ingestion_app,
        filename="notes.txt",
        content_type="text/plain",
        payload=b"synthetic document",
    )

    assert status == 503
    assert body == {
        "error": {
            "code": "document_storage_unavailable",
            "message": "The document could not be stored. Please try again later.",
        }
    }


@pytest.mark.anyio
async def test_pdf_page_limit_returns_safe_parse_error(ingestion_app: object) -> None:
    status, body = await _upload(
        ingestion_app,
        filename="two-pages.pdf",
        content_type="application/pdf",
        payload=_two_page_pdf_bytes(),
    )

    assert status == 422
    assert body["error"]["code"] == "document_parse_failed"


@pytest.mark.anyio
async def test_long_markdown_heading_is_stored_losslessly(
    ingestion_app: object,
) -> None:
    heading = "A" * 800
    status, body = await _upload(
        ingestion_app,
        filename="long-heading.md",
        content_type="text/markdown",
        payload=f"# {heading}\n\nSynthetic text.".encode(),
    )

    assert status == 201
    engine: Engine = ingestion_app.state.database_engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        chunk = session.scalar(
            select(DocumentChunk).where(
                DocumentChunk.document_id == body["document_id"]
            )
        )
    assert chunk is not None
    assert chunk.section == heading


def test_transaction_failure_rolls_back_all_document_rows(
    ingestion_app: object,
) -> None:
    engine: Engine = ingestion_app.state.database_engine  # type: ignore[attr-defined]
    session_factory = ingestion_app.state.ingestion_service._session_factory  # type: ignore[attr-defined]

    def raise_before_flush(*_: object) -> None:
        raise SQLAlchemyError("synthetic database detail")

    event.listen(Session, "before_flush", raise_before_flush)
    try:
        with pytest.raises(IngestionStorageError):
            IngestionService(
                session_factory=session_factory,
                max_file_size_bytes=5_000,
                max_pdf_pages=1,
                max_extracted_characters=2_000,
                chunk_size=100,
                chunk_overlap=20,
            ).ingest(
                filename="rollback.txt",
                content_type="text/plain",
                payload=b"rollback test",
            )
    finally:
        event.remove(Session, "before_flush", raise_before_flush)

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 0
        assert session.scalar(select(func.count()).select_from(DocumentChunk)) == 0


def test_checksum_race_returns_existing_document_deterministically(
    ingestion_app: object,
) -> None:
    class RaceRepository(DocumentRepository):
        def __init__(self) -> None:
            self.calls = 0

        def get_by_checksum(self, session: Session, checksum: str) -> Document | None:
            self.calls += 1
            if self.calls == 1:
                return None
            return super().get_by_checksum(session, checksum)

    payload = b"same concurrent bytes"
    existing = ingestion_app.state.ingestion_service.ingest(  # type: ignore[attr-defined]
        filename="initial.txt", content_type="text/plain", payload=payload
    )
    session_factory = ingestion_app.state.ingestion_service._session_factory  # type: ignore[attr-defined]
    raced = IngestionService(
        session_factory=session_factory,
        max_file_size_bytes=5_000,
        max_pdf_pages=1,
        max_extracted_characters=2_000,
        chunk_size=100,
        chunk_overlap=20,
        repository=RaceRepository(),
    ).ingest(filename="replay.txt", content_type="text/plain", payload=payload)

    assert raced.duplicate is True
    assert raced.document_id == existing.document_id
