"""Create Feature 2 document processing tables.

Revision ID: 0002_document_processing
Revises: 0001_tenders_documents
Create Date: 2026-11-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_document_processing"
down_revision: Union[str, None] = "0001_tenders_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_jsonb = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_run_statuses = "('pending', 'processing', 'completed', 'partially_completed', 'failed')"
_item_statuses = "('pending', 'processing', 'completed', 'failed')"


def upgrade() -> None:
    op.create_table(
        "document_processing_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tender_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("requested_document_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status IN {_run_statuses}", name="ck_document_processing_runs_status"),
        sa.CheckConstraint("requested_document_count > 0", name="ck_document_processing_runs_document_count"),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_processing_runs_tender_created",
        "document_processing_runs",
        ["tender_id", sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_document_processing_runs_tender_status",
        "document_processing_runs",
        ["tender_id", "status"],
        unique=False,
    )

    op.create_table(
        "document_processing_run_items",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("outcome_code", sa.String(length=48), nullable=True),
        sa.Column("failure_class", sa.String(length=24), nullable=True),
        sa.Column("attempt_count", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.SmallInteger(), server_default=sa.text("3"), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("safe_message", sa.String(length=240), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"status IN {_item_statuses}", name="ck_document_processing_run_items_status"),
        sa.CheckConstraint(
            "failure_class IS NULL OR failure_class IN ('retryable', 'permanent')",
            name="ck_document_processing_run_items_failure_class",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_document_processing_run_items_attempt_count"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_document_processing_run_items_max_attempts"),
        sa.CheckConstraint("fencing_token >= 0", name="ck_document_processing_run_items_fencing_token"),
        sa.ForeignKeyConstraint(["run_id"], ["document_processing_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["tender_documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "document_id", name="uq_document_processing_run_items_run_document"),
    )
    op.create_index(
        "ix_document_processing_run_items_claim",
        "document_processing_run_items",
        ["status", "next_attempt_at", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_document_processing_run_items_run_created",
        "document_processing_run_items",
        ["run_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_document_processing_run_items_document_created",
        "document_processing_run_items",
        ["document_id", sa.text("created_at DESC")],
        unique=False,
    )

    op.create_table(
        "document_extraction_units",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("run_item_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("unit_index", sa.Integer(), nullable=False),
        sa.Column("unit_type", sa.String(length=24), nullable=False),
        sa.Column("source_ref", _jsonb, nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("table_data", _jsonb, nullable=True),
        sa.Column("metadata", _jsonb, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("unit_type IN ('text', 'table', 'ocr_text')", name="ck_document_extraction_units_type"),
        sa.CheckConstraint(
            "(unit_type = 'table' AND table_data IS NOT NULL) OR "
            "(unit_type IN ('text', 'ocr_text') AND content_text IS NOT NULL)",
            name="ck_document_extraction_units_content",
        ),
        sa.ForeignKeyConstraint(["run_item_id"], ["document_processing_run_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_item_id", "unit_index", name="uq_document_extraction_units_item_index"),
    )


def downgrade() -> None:
    op.drop_table("document_extraction_units")
    op.drop_index("ix_document_processing_run_items_document_created", table_name="document_processing_run_items")
    op.drop_index("ix_document_processing_run_items_run_created", table_name="document_processing_run_items")
    op.drop_index("ix_document_processing_run_items_claim", table_name="document_processing_run_items")
    op.drop_table("document_processing_run_items")
    op.drop_index("ix_document_processing_runs_tender_status", table_name="document_processing_runs")
    op.drop_index("ix_document_processing_runs_tender_created", table_name="document_processing_runs")
    op.drop_table("document_processing_runs")
