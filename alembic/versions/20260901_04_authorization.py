"""Add document classification and group ACL metadata for Stage 9."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260901_04"
down_revision: str | None = "20260814_03"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "classification",
            sa.String(length=32),
            nullable=False,
            server_default="public",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "allowed_groups", sa.JSON(), nullable=False, server_default="[]"
        ),
    )
    op.create_index(
        "ix_documents_classification", "documents", ["classification"], unique=False
    )
    # SQLite cannot ALTER a table to add a check constraint; batch mode performs
    # the portable copy-and-move operation while PostgreSQL uses native DDL.
    with op.batch_alter_table("documents") as batch:
        batch.create_check_constraint(
            "ck_document_classification",
            "classification IN ('public', 'internal', 'confidential', 'restricted')",
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("ck_document_classification", type_="check")
    op.drop_index("ix_documents_classification", table_name="documents")
    op.drop_column("documents", "allowed_groups")
    op.drop_column("documents", "classification")
