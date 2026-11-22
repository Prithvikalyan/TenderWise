"""Persist requirement-to-company-evidence matching snapshots.

Revision ID: 0005_tender_evidence_matching
Revises: 0004_company_document_processing
Create Date: 2026-11-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005_tender_evidence_matching"
down_revision: Union[str, None] = "0004_company_document_processing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "tender_evidence_matching_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tender_id", sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'completed'"), nullable=False),
        sa.Column("eligibility_requirement_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("compliance_requirement_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("candidate_found_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("potential_match_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("company_document_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processed_company_document_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unprocessed_company_document_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("missing_evidence_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expired_evidence_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("manual_review_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processing_required_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("result_json", _JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('completed')", name="ck_evidence_matching_runs_status"),
        sa.CheckConstraint("eligibility_requirement_count >= 0", name="ck_evidence_matching_runs_eligibility_count"),
        sa.CheckConstraint("compliance_requirement_count >= 0", name="ck_evidence_matching_runs_compliance_count"),
        sa.CheckConstraint("candidate_found_count >= 0", name="ck_evidence_matching_runs_candidate_count"),
        sa.CheckConstraint("potential_match_count >= 0", name="ck_evidence_matching_runs_potential_count"),
        sa.CheckConstraint("company_document_count >= 0", name="ck_evidence_matching_runs_document_count"),
        sa.CheckConstraint("processed_company_document_count >= 0", name="ck_evidence_matching_runs_processed_document_count"),
        sa.CheckConstraint("unprocessed_company_document_count >= 0", name="ck_evidence_matching_runs_unprocessed_document_count"),
        sa.CheckConstraint("missing_evidence_count >= 0", name="ck_evidence_matching_runs_missing_count"),
        sa.CheckConstraint("expired_evidence_count >= 0", name="ck_evidence_matching_runs_expired_count"),
        sa.CheckConstraint("manual_review_count >= 0", name="ck_evidence_matching_runs_manual_count"),
        sa.CheckConstraint("processing_required_count >= 0", name="ck_evidence_matching_runs_processing_count"),
    )
    op.create_index(
        "ix_evidence_matching_runs_tender_created",
        "tender_evidence_matching_runs",
        ["tender_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_evidence_matching_runs_company_created",
        "tender_evidence_matching_runs",
        ["company_id", sa.text("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_matching_runs_company_created", table_name="tender_evidence_matching_runs")
    op.drop_index("ix_evidence_matching_runs_tender_created", table_name="tender_evidence_matching_runs")
    op.drop_table("tender_evidence_matching_runs")
