import hashlib

import pytest

from backend.app.ingestion.errors import (
    DocumentParseError,
    DocumentTooLargeError,
    InvalidDocumentError,
)
from backend.app.ingestion.models import ParsedSegment
from backend.app.ingestion.pipeline import (
    normalize_and_chunk,
    parse_document,
    validate_upload,
)


def test_validation_rejects_traversal_mismatch_and_oversized_uploads() -> None:
    with pytest.raises(InvalidDocumentError):
        validate_upload(
            filename="../private.txt",
            content_type="text/plain",
            payload=b"public sample",
            max_size_bytes=10,
        )
    with pytest.raises(InvalidDocumentError):
        validate_upload(
            filename="notes.md",
            content_type="text/plain",
            payload=b"# Sample",
            max_size_bytes=100,
        )
    with pytest.raises(DocumentTooLargeError):
        validate_upload(
            filename="notes.txt",
            content_type="text/plain",
            payload=b"123456",
            max_size_bytes=5,
        )


def test_text_parsing_rejects_non_utf8_and_corrupt_pdf() -> None:
    with pytest.raises(DocumentParseError):
        parse_document(extension=".txt", payload=b"\xff\xfe")
    with pytest.raises(DocumentParseError):
        parse_document(extension=".pdf", payload=b"%PDF-not-a-real-pdf")


def test_normalization_and_chunking_are_deterministic_and_metadata_preserving() -> None:
    source = "  Caf\u00e9\r\n\r\n" + "alpha beta gamma delta epsilon " * 3
    first = normalize_and_chunk(
        [ParsedSegment(source, page_number=2, section="Overview")],
        chunk_size=32,
        overlap=8,
    )
    second = normalize_and_chunk(
        [ParsedSegment(source, page_number=2, section="Overview")],
        chunk_size=32,
        overlap=8,
    )

    assert first == second
    assert len(first) > 1
    assert all(len(chunk.content) <= 32 for chunk in first)
    assert all(
        chunk.page_number == 2 and chunk.section == "Overview" for chunk in first
    )
    assert all(
        chunk.checksum == hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
        for chunk in first
    )


def test_empty_segments_do_not_create_empty_chunks() -> None:
    assert (
        normalize_and_chunk(
            [ParsedSegment(" \n\t "), ParsedSegment("")], chunk_size=100, overlap=0
        )
        == []
    )
