"""Add cached semantic embeddings for tender extraction units.

Revision ID: 0009_embed_cache
Revises: 0008_bid_verification_checklists
Create Date: 2026-11-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0009_embed_cache"
down_revision: Union[str, None] = "0008_bid_verification_checklists"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "document_extraction_embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("extraction_unit_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_model", sa.String(length=160), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("embedding", _json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["extraction_unit_id"],
            ["document_extraction_units.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "extraction_unit_id",
            "embedding_model",
            name="uq_document_extraction_embeddings_unit_model",
        ),
    )
    op.create_index(
        "ix_document_extraction_embeddings_model",
        "document_extraction_embeddings",
        ["embedding_model"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_extraction_embeddings_model",
        table_name="document_extraction_embeddings",
    )
    op.drop_table("document_extraction_embeddings")
