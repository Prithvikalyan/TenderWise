"""Cached semantic embeddings for tender extraction units."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.tender import utc_now

_JSON = JSON().with_variant(JSONB, "postgresql")


class DocumentExtractionEmbedding(Base):
    """Model-specific cached vector for one immutable extraction unit."""

    __tablename__ = "document_extraction_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "extraction_unit_id",
            "embedding_model",
            name="uq_document_extraction_embeddings_unit_model",
        ),
        Index(
            "ix_document_extraction_embeddings_model",
            "embedding_model",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    extraction_unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_extraction_units.id", ondelete="CASCADE"),
        nullable=False,
    )
    embedding_model: Mapped[str] = mapped_column(String(160), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list] = mapped_column(_JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now,
        onupdate=utc_now, server_default=func.now(),
    )
