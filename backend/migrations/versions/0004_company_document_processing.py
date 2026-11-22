"""Allow the shared processing queue to process reusable company versions.

Revision ID: 0004_company_document_processing
Revises: 0003_company_documents
Create Date: 2026-11-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_company_document_processing"
down_revision: Union[str, None] = "0003_company_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A processing run is owned by exactly one tender or one company. Batch
    # mode keeps this migration compatible with SQLite-based migration tests.
    with op.batch_alter_table("document_processing_runs") as batch:
        batch.alter_column(
            "tender_id",
            existing_type=sa.Uuid(as_uuid=True),
            nullable=True,
        )
        batch.add_column(sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.create_foreign_key(
            "fk_document_processing_runs_company_id_companies",
            "companies",
            ["company_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_check_constraint(
            "ck_document_processing_runs_one_owner",
            "(tender_id IS NOT NULL AND company_id IS NULL) OR "
            "(tender_id IS NULL AND company_id IS NOT NULL)",
        )

    op.create_index(
        "ix_document_processing_runs_company_created",
        "document_processing_runs",
        ["company_id", sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
    )

    # Existing tender items keep document_id; company processing items use the
    # immutable company_document_version_id. Both may be NULL only after the
    # referenced source has been deleted (ON DELETE SET NULL).
    with op.batch_alter_table("document_processing_run_items") as batch:
        batch.add_column(sa.Column("company_document_version_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.create_foreign_key(
            "fk_document_processing_run_items_company_version",
            "company_document_versions",
            ["company_document_version_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_document_processing_run_items_run_company_version",
            ["run_id", "company_document_version_id"],
        )
        batch.create_check_constraint(
            "ck_document_processing_run_items_one_source",
            "company_document_version_id IS NULL OR document_id IS NULL",
        )

    op.create_index(
        "ix_document_processing_run_items_company_version_created",
        "document_processing_run_items",
        ["company_document_version_id", sa.text("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_processing_run_items_company_version_created",
        table_name="document_processing_run_items",
    )
    with op.batch_alter_table("document_processing_run_items") as batch:
        batch.drop_constraint("ck_document_processing_run_items_one_source", type_="check")
        batch.drop_constraint(
            "uq_document_processing_run_items_run_company_version", type_="unique"
        )
        batch.drop_constraint(
            "fk_document_processing_run_items_company_version", type_="foreignkey"
        )
        batch.drop_column("company_document_version_id")

    op.drop_index(
        "ix_document_processing_runs_company_created",
        table_name="document_processing_runs",
    )
    with op.batch_alter_table("document_processing_runs") as batch:
        batch.drop_constraint("ck_document_processing_runs_one_owner", type_="check")
        batch.drop_constraint(
            "fk_document_processing_runs_company_id_companies", type_="foreignkey"
        )
        batch.drop_column("company_id")
        batch.alter_column(
            "tender_id",
            existing_type=sa.Uuid(as_uuid=True),
            nullable=False,
        )
