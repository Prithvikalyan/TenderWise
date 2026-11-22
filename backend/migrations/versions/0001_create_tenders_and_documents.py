"""Create tender workspace and document metadata tables.

Revision ID: 0001_tenders_documents
Revises:
Create Date: 2026-11-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_tenders_documents"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenders",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("reference_number", sa.String(length=255), nullable=True),
        sa.Column("issuing_organization", sa.String(length=255), nullable=True),
        sa.Column("department", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('draft', 'active', 'archived')", name="ck_tenders_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tenders_updated_at", "tenders", ["updated_at"], unique=False)

    op.create_table(
        "tender_documents",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tender_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("extension", sa.String(length=32), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "category IN ('main_tender', 'technical_specification', 'boq', 'annexure_form', "
            "'corrigendum_amendment', 'pre_bid_clarification', 'supporting_document', 'other')",
            name="ck_tender_documents_category",
        ),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_tender_documents_storage_key"),
    )
    op.create_index("ix_tender_documents_tender_id", "tender_documents", ["tender_id"], unique=False)
    op.create_index(
        "ix_tender_documents_tender_created_at", "tender_documents", ["tender_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_tender_documents_tender_category", "tender_documents", ["tender_id", "category"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_tender_documents_tender_category", table_name="tender_documents")
    op.drop_index("ix_tender_documents_tender_created_at", table_name="tender_documents")
    op.drop_index("ix_tender_documents_tender_id", table_name="tender_documents")
    op.drop_table("tender_documents")
    op.drop_index("ix_tenders_updated_at", table_name="tenders")
    op.drop_table("tenders")
