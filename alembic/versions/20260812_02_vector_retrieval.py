"""Add versioned pgvector embeddings for Stage 3 semantic retrieval.

Revision ID: 20260812_02
Revises: 20260812_01
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "20260812_02"
down_revision: str | None = "20260812_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "chunk_embeddings",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("chunk_id", sa.String(length=32), nullable=False),
        sa.Column("embedding_model", sa.String(length=255), nullable=False),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("content_checksum", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "embedding_dimensions > 0", name="ck_embedding_dimensions_positive"
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["document_chunks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chunk_id",
            "embedding_model",
            "embedding_dimensions",
            name="uq_chunk_embedding_model_dimension",
        ),
    )
    op.create_index(
        "ix_chunk_embeddings_chunk_id", "chunk_embeddings", ["chunk_id"], unique=False
    )
    op.create_index(
        "ix_chunk_embeddings_model_dimension",
        "chunk_embeddings",
        ["embedding_model", "embedding_dimensions"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chunk_embeddings_model_dimension", table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_chunk_id", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
