"""Pure, deterministic Stage 2 document validation and text transformation."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable

import fitz

from backend.app.ingestion.errors import (
    DocumentParseError,
    DocumentTooLargeError,
    InvalidDocumentError,
)
from backend.app.ingestion.models import NormalizedChunk, ParsedSegment

ALLOWED_FORMATS: dict[str, frozenset[str]] = {
    ".pdf": frozenset({"application/pdf"}),
    ".txt": frozenset({"text/plain"}),
    ".md": frozenset({"text/markdown", "text/x-markdown"}),
    ".markdown": frozenset({"text/markdown", "text/x-markdown"}),
}


def validate_upload(
    *,
    filename: str | None,
    content_type: str | None,
    payload: bytes,
    max_size_bytes: int,
) -> tuple[str, str]:
    """Validate name, MIME header, bounded bytes, and minimal content signature."""
    if (
        not filename
        or len(filename) > 255
        or any(token in filename for token in ("/", "\\", "..", "\x00"))
    ):
        raise InvalidDocumentError
    extension = filename[filename.rfind(".") :].lower()
    allowed_mime_types = ALLOWED_FORMATS.get(extension)
    declared_mime_type = (content_type or "").split(";", 1)[0].strip().lower()
    if allowed_mime_types is None or declared_mime_type not in allowed_mime_types:
        raise InvalidDocumentError
    if len(payload) > max_size_bytes:
        raise DocumentTooLargeError
    if extension == ".pdf" and not payload.startswith(b"%PDF-"):
        raise InvalidDocumentError
    if extension != ".pdf" and b"\x00" in payload:
        raise InvalidDocumentError
    return extension, declared_mime_type


def parse_document(
    *,
    extension: str,
    payload: bytes,
    max_pdf_pages: int = 200,
    max_extracted_characters: int = 2_000_000,
) -> list[ParsedSegment]:
    """Extract text only; original upload bytes are deliberately not persisted."""
    if extension == ".pdf":
        return _parse_pdf(
            payload,
            max_pages=max_pdf_pages,
            max_extracted_characters=max_extracted_characters,
        )
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentParseError from exc
    return (
        _parse_markdown(text)
        if extension in {".md", ".markdown"}
        else [ParsedSegment(text)]
    )


def normalize_and_chunk(
    segments: Iterable[ParsedSegment], *, chunk_size: int, overlap: int
) -> list[NormalizedChunk]:
    """Produce bounded, stable chunks retaining the most-specific source metadata."""
    chunks: list[NormalizedChunk] = []
    for segment in segments:
        normalized = normalize_text(segment.content)
        if not normalized:
            continue
        for content in _split_text(normalized, chunk_size=chunk_size, overlap=overlap):
            chunks.append(
                NormalizedChunk(
                    content=content,
                    checksum=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    page_number=segment.page_number,
                    section=segment.section,
                )
            )
    return chunks


def normalize_text(value: str) -> str:
    """Canonicalize Unicode and whitespace without changing word order."""
    canonical = (
        unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    )
    lines = [" ".join(line.split()) for line in canonical.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _parse_pdf(
    payload: bytes, *, max_pages: int, max_extracted_characters: int
) -> list[ParsedSegment]:
    try:
        document = fitz.open(stream=payload, filetype="pdf")
    except (RuntimeError, ValueError) as exc:
        raise DocumentParseError from exc
    try:
        if document.needs_pass:
            raise DocumentParseError
        if len(document) > max_pages:
            raise DocumentParseError
        extracted_characters = 0
        segments: list[ParsedSegment] = []
        for page_number, page in enumerate(document, start=1):
            content = page.get_text("text")
            extracted_characters += len(content)
            if extracted_characters > max_extracted_characters:
                raise DocumentParseError
            segments.append(ParsedSegment(content=content, page_number=page_number))
        return segments
    except (RuntimeError, ValueError) as exc:
        raise DocumentParseError from exc
    finally:
        document.close()


def _parse_markdown(text: str) -> list[ParsedSegment]:
    segments: list[ParsedSegment] = []
    section: str | None = None
    buffered: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            if buffered:
                segments.append(ParsedSegment("\n".join(buffered), section=section))
                buffered = []
            section = normalize_text(heading.group(1)) or None
        else:
            buffered.append(line)
    if buffered:
        segments.append(ParsedSegment("\n".join(buffered), section=section))
    return segments


def _split_text(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        candidate_end = min(start + chunk_size, len(text))
        end = candidate_end
        if candidate_end < len(text):
            break_at = text.rfind(" ", start + 1, candidate_end + 1)
            if break_at > start:
                end = break_at
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks
