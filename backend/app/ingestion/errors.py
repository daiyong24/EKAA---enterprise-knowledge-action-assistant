"""Typed ingestion failures with no untrusted content in their messages."""


class IngestionError(Exception):
    """Base class for expected document-ingestion failures."""


class InvalidDocumentError(IngestionError):
    """The upload does not meet the narrow Stage 2 format contract."""


class DocumentTooLargeError(IngestionError):
    """The upload exceeds the configured bounded in-memory limit."""


class DocumentParseError(IngestionError):
    """A supported format could not be safely converted to text."""


class EmptyDocumentError(IngestionError):
    """No non-whitespace text remains after parsing and normalization."""


class IngestionStorageError(IngestionError):
    """The transaction could not persist a consistent document record."""
