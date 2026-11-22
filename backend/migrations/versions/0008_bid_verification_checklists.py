"""Add persistent pre-submission verification checklists.

Revision ID: 0008_bid_verification_checklists
Revises: 0007_evidence_matching_v3
Create Date: 2026-11-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0008_bid_verification_checklists"
down_revision: Union[str, None] = "0007_evidence_matching_v3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "bid_verification_checklists",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_matching_run_id", sa.Uuid(), nullable=True),
        sa.Column("readiness_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "readiness_status IN ('ready', 'not_ready', 'incomplete', 'needs_review', 'insufficient_evidence')",
            name="ck_bid_verification_checklists_readiness_status",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["evidence_matching_run_id"], ["tender_evidence_matching_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bid_verification_checklists_tender_created",
        "bid_verification_checklists", ["tender_id", sa.text("created_at DESC")], unique=False,
    )
    op.create_index(
        "ix_bid_verification_checklists_company_created",
        "bid_verification_checklists", ["company_id", sa.text("created_at DESC")], unique=False,
    )
    op.create_table(
        "bid_verification_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("checklist_id", sa.Uuid(), nullable=False),
        sa.Column("requirement_key", sa.String(length=80), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("requirement", sa.Text(), nullable=False),
        sa.Column("required_action", sa.Text(), nullable=False),
        sa.Column("evidence_status", sa.String(length=40), nullable=False),
        sa.Column("verification_status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("source_ids", _json_type(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("candidate_documents", _json_type(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("verification_notes", sa.Text(), nullable=True),
        sa.Column("reviewer_name", sa.String(length=255), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(requirement)) > 0", name="ck_bid_verification_items_requirement_nonempty"),
        sa.CheckConstraint(
            "verification_status IN ('pending', 'in_progress', 'verified', 'blocked', 'not_applicable')",
            name="ck_bid_verification_items_status",
        ),
        sa.ForeignKeyConstraint(["checklist_id"], ["bid_verification_checklists.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checklist_id", "requirement_key", name="uq_bid_verification_items_requirement"),
    )
    op.create_index(
        "ix_bid_verification_items_checklist_status",
        "bid_verification_items", ["checklist_id", "verification_status"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_bid_verification_items_checklist_status", table_name="bid_verification_items")
    op.drop_table("bid_verification_items")
    op.drop_index("ix_bid_verification_checklists_company_created", table_name="bid_verification_checklists")
    op.drop_index("ix_bid_verification_checklists_tender_created", table_name="bid_verification_checklists")
    op.drop_table("bid_verification_checklists")
