from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum as SQLAlchemyEnum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.tender import utc_now


class DocumentCategory(str, Enum):
    MAIN_TENDER = "main_tender"
    TECHNICAL_SPECIFICATION = "technical_specification"
    BOQ = "boq"
    ANNEXURE_FORM = "annexure_form"
    CORRIGENDUM_AMENDMENT = "corrigendum_amendment"
    PRE_BID_CLARIFICATION = "pre_bid_clarification"
    SUPPORTING_DOCUMENT = "supporting_document"
    OTHER = "other"


class TenderDocument(Base):
    __tablename__ = "tender_documents"
    __table_args__ = (
        CheckConstraint(
            "category IN ('main_tender', 'technical_specification', 'boq', 'annexure_form', "
            "'corrigendum_amendment', 'pre_bid_clarification', 'supporting_document', 'other')",
            name="ck_tender_documents_category",
        ),
        Index("ix_tender_documents_tender_id", "tender_id"),
        Index("ix_tender_documents_tender_created_at", "tender_id", "created_at"),
        Index("ix_tender_documents_tender_category", "tender_id", "category"),
        UniqueConstraint("storage_key", name="uq_tender_documents_storage_key"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tender_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str] = mapped_column(String(32), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[DocumentCategory] = mapped_column(
        SQLAlchemyEnum(
            DocumentCategory,
            name="document_category",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_type: [member.value for member in enum_type],
            length=40,
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )

    tender: Mapped["Tender"] = relationship(back_populates="documents")
    processing_run_items: Mapped[list["DocumentProcessingRunItem"]] = relationship(
        back_populates="document",
        passive_deletes="all",
    )
