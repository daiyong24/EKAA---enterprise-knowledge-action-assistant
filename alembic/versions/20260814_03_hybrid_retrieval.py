"""Add the PostgreSQL full-text index used by Stage 6 hybrid retrieval.

Revision ID: 20260814_03
Revises: 20260812_02
Create Date: 2026-08-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260814_03"
down_revision: str | None = "20260812_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_INDEX_NAME = "ix_document_chunks_content_fts_simple"


def upgrade() -> None:
    # SQLite stays an isolated relational-test backend; it cannot truthfully model
    # PostgreSQL full-text search or its execution plan.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_document_chunks_content_fts_simple "
            "ON document_chunks USING gin (to_tsvector('simple', content))"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
