"""Add persistent tender milestones for the dashboard calendar.

Revision ID: 0010_tender_milestones
Revises: 0009_embed_cache
Create Date: 2026-11-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_tender_milestones"
down_revision: Union[str, None] = "0009_embed_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tender_milestones",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=40), server_default="other", nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source_reference", sa.String(length=500), nullable=True),
        sa.Column("is_completed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "category IN ('submission_deadline', 'clarification_deadline', 'pre_bid_meeting', 'bid_opening', 'emd_deadline', 'document_deadline', 'other')",
            name="ck_tender_milestones_category",
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_tender_milestones_title_nonempty"),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tender_milestones_due_date", "tender_milestones", ["due_date"], unique=False)
    op.create_index("ix_tender_milestones_tender_due_date", "tender_milestones", ["tender_id", "due_date"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tender_milestones_tender_due_date", table_name="tender_milestones")
    op.drop_index("ix_tender_milestones_due_date", table_name="tender_milestones")
    op.drop_table("tender_milestones")
