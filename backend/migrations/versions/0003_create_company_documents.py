"""Create reusable company document repository and immutable file versions.

Revision ID: 0003_company_documents
Revises: 0002_document_processing
Create Date: 2026-11-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_company_documents"
down_revision: Union[str, None] = "0002_document_processing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_categories = (
    "('business_registration', 'gst_certificate', 'pan_card', "
    "'audited_financial_statements', 'turnover_certificate', "
    "'purchase_order', 'work_order', 'completion_certificate', "
    "'non_blacklisting_declaration', 'technical_certification', 'license', 'other')"
)


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("registration_number", sa.String(length=80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_archived", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(legal_name)) > 0", name="ck_companies_legal_name_nonempty"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_companies_legal_name", "companies", ["legal_name"], unique=False)
    op.create_index(
        "ix_companies_archived_updated", "companies", ["is_archived", "updated_at"], unique=False
    )

    op.create_table(
        "company_documents",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=48), nullable=False),
        sa.Column("is_archived", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"category IN {_categories}", name="ck_company_documents_category"),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_company_documents_title_nonempty"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_company_documents_company_created", "company_documents", ["company_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_company_documents_company_category", "company_documents", ["company_id", "category"], unique=False
    )
    op.create_index(
        "ix_company_documents_company_archived", "company_documents", ["company_id", "is_archived"], unique=False
    )

    op.create_table(
        "company_document_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("extension", sa.String(length=32), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("financial_year", sa.String(length=20), nullable=True),
        sa.Column("issuer", sa.String(length=255), nullable=True),
        sa.Column("reference_number", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version_number >= 1", name="ck_company_document_versions_number"),
        sa.CheckConstraint("size_bytes > 0", name="ck_company_document_versions_size"),
        sa.CheckConstraint(
            "expiry_date IS NULL OR issue_date IS NULL OR expiry_date >= issue_date",
            name="ck_company_document_versions_dates",
        ),
        sa.ForeignKeyConstraint(["document_id"], ["company_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "version_number", name="uq_company_document_versions_number"
        ),
        sa.UniqueConstraint("storage_key", name="uq_company_document_versions_storage_key"),
    )
    op.create_index(
        "ix_company_document_versions_document_created",
        "company_document_versions",
        ["document_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_company_document_versions_expiry",
        "company_document_versions",
        ["expiry_date"],
        unique=False,
    )
    op.create_index(
        "ix_company_document_versions_financial_year",
        "company_document_versions",
        ["financial_year"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_company_document_versions_financial_year", table_name="company_document_versions")
    op.drop_index("ix_company_document_versions_expiry", table_name="company_document_versions")
    op.drop_index("ix_company_document_versions_document_created", table_name="company_document_versions")
    op.drop_table("company_document_versions")
    op.drop_index("ix_company_documents_company_archived", table_name="company_documents")
    op.drop_index("ix_company_documents_company_category", table_name="company_documents")
    op.drop_index("ix_company_documents_company_created", table_name="company_documents")
    op.drop_table("company_documents")
    op.drop_index("ix_companies_archived_updated", table_name="companies")
    op.drop_index("ix_companies_legal_name", table_name="companies")
    op.drop_table("companies")
