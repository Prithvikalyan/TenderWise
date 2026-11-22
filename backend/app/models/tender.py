from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum as SQLAlchemyEnum, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TenderStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


class Tender(Base):
    __tablename__ = "tenders"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'active', 'archived')", name="ck_tenders_status"),
        Index("ix_tenders_updated_at", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    reference_number: Mapped[str | None] = mapped_column(String(255))
    issuing_organization: Mapped[str | None] = mapped_column(String(255))
    department: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TenderStatus] = mapped_column(
        SQLAlchemyEnum(
            TenderStatus,
            name="tender_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_type: [member.value for member in enum_type],
            length=20,
        ),
        nullable=False,
        default=TenderStatus.DRAFT,
        server_default=text("'draft'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now()
    )

    documents: Mapped[list["TenderDocument"]] = relationship(
        back_populates="tender",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    processing_runs: Mapped[list["DocumentProcessingRun"]] = relationship(
        back_populates="tender",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
