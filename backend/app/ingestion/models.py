"""Format-independent values owned by the ingestion application service."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ParsedSegment:
    content: str
    page_number: int | None = None
    section: str | None = None


@dataclass(frozen=True)
class NormalizedChunk:
    content: str
    checksum: str
    page_number: int | None
    section: str | None


@dataclass(frozen=True)
class IngestionResult:
    document_id: str
    filename: str
    mime_type: str
    checksum: str
    status: str
    chunk_count: int
    created_at: datetime
    duplicate: bool
