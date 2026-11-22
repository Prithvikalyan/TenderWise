"""Add unique expired document count to evidence matching snapshots.

Revision ID: 0007_evidence_matching_v3
Revises: 0006_evidence_matching_v2
Create Date: 2026-11-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_evidence_matching_v3"
down_revision: Union[str, None] = "0006_evidence_matching_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tender_evidence_matching_runs",
        sa.Column("unique_expired_document_count", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("tender_evidence_matching_runs", "unique_expired_document_count")
