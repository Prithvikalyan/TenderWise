"""Add Evidence Matching V2 coverage and requirement classification fields.

Revision ID: 0006_evidence_matching_v2
Revises: 0005_tender_evidence_matching
Create Date: 2026-11-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_evidence_matching_v2"
down_revision: Union[str, None] = "0005_tender_evidence_matching"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tender_evidence_matching_runs", sa.Column("latest_version_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("tender_evidence_matching_runs", sa.Column("documents_without_versions_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("tender_evidence_matching_runs", sa.Column("non_company_evidence_count", sa.Integer(), server_default="0", nullable=False))
    # Swagger's generic example value was sometimes saved literally as "string".
    # Normalize known placeholders so the application never displays them as real metadata.
    placeholders = ("string", "null", "none", "n/a", "na", "not provided", "not applicable", "-")
    for column in ("financial_year", "issuer", "reference_number", "notes"):
        literals = ", ".join("'" + value.replace("'", "''") + "'" for value in placeholders)
        op.execute(sa.text(
            f"UPDATE company_document_versions SET {column} = NULL "
            f"WHERE lower(trim({column})) IN ({literals})"
        ))


def downgrade() -> None:
    op.drop_column("tender_evidence_matching_runs", "non_company_evidence_count")
    op.drop_column("tender_evidence_matching_runs", "documents_without_versions_count")
    op.drop_column("tender_evidence_matching_runs", "latest_version_count")
