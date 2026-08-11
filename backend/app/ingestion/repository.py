"""SQLAlchemy persistence operations owned by the ingestion service."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import Document


class DocumentRepository:
    def get_by_checksum(self, session: Session, checksum: str) -> Document | None:
        return session.scalar(select(Document).where(Document.checksum == checksum))

    def add(self, session: Session, document: Document) -> None:
        session.add(document)
