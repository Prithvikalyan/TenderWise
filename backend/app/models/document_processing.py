from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.tender import utc_now


_JSONB = JSON().with_variant(JSONB, "postgresql")


class DocumentProcessingRun(Base):
    __tablename__ = "document_processing_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'partially_completed', 'failed')",
            name="ck_document_processing_runs_status",
        ),
        CheckConstraint("requested_document_count > 0", name="ck_document_processing_runs_document_count"),
        CheckConstraint(
            "(tender_id IS NOT NULL AND company_id IS NULL) OR "
            "(tender_id IS NULL AND company_id IS NOT NULL)",
            name="ck_document_processing_runs_one_owner",
        ),
        Index("ix_document_processing_runs_tender_created", "tender_id", text("created_at DESC"), text("id DESC")),
        Index("ix_document_processing_runs_company_created", "company_id", text("created_at DESC"), text("id DESC")),
        Index("ix_document_processing_runs_tender_status", "tender_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tender_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), nullable=True
    )
    company_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default=text("'pending'"))
    requested_document_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now()
    )

    tender: Mapped["Tender | None"] = relationship(back_populates="processing_runs")
    company: Mapped["Company | None"] = relationship(back_populates="processing_runs")
    items: Mapped[list["DocumentProcessingRunItem"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentProcessingRunItem(Base):
    __tablename__ = "document_processing_run_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_document_processing_run_items_status",
        ),
        CheckConstraint(
            "failure_class IS NULL OR failure_class IN ('retryable', 'permanent')",
            name="ck_document_processing_run_items_failure_class",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_document_processing_run_items_attempt_count"),
        CheckConstraint("max_attempts >= 1", name="ck_document_processing_run_items_max_attempts"),
        CheckConstraint("fencing_token >= 0", name="ck_document_processing_run_items_fencing_token"),
        UniqueConstraint("run_id", "document_id", name="uq_document_processing_run_items_run_document"),
        UniqueConstraint("run_id", "company_document_version_id", name="uq_document_processing_run_items_run_company_version"),
        CheckConstraint(
            "company_document_version_id IS NULL OR document_id IS NULL",
            name="ck_document_processing_run_items_one_source",
        ),
        Index("ix_document_processing_run_items_claim", "status", "next_attempt_at", "lease_expires_at"),
        Index(
            "ix_document_processing_run_items_run_created",
            "run_id",
            "created_at",
            "id",
        ),
        Index("ix_document_processing_run_items_document_created", "document_id", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_processing_runs.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tender_documents.id", ondelete="SET NULL"), nullable=True
    )
    company_document_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("company_document_versions.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default=text("'pending'"))
    outcome_code: Mapped[str | None] = mapped_column(String(48))
    failure_class: Mapped[str | None] = mapped_column(String(24))
    attempt_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3, server_default=text("3"))
    worker_id: Mapped[str | None] = mapped_column(String(128))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    safe_message: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[DocumentProcessingRun] = relationship(back_populates="items")
    document: Mapped["TenderDocument | None"] = relationship(back_populates="processing_run_items")
    company_document_version: Mapped["CompanyDocumentVersion | None"] = relationship(
        back_populates="processing_run_items"
    )
    extraction_units: Mapped[list["DocumentExtractionUnit"]] = relationship(
        back_populates="run_item", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentExtractionUnit(Base):
    __tablename__ = "document_extraction_units"
    __table_args__ = (
        CheckConstraint("unit_type IN ('text', 'table', 'ocr_text')", name="ck_document_extraction_units_type"),
        CheckConstraint(
            "(unit_type = 'table' AND table_data IS NOT NULL) OR "
            "(unit_type IN ('text', 'ocr_text') AND content_text IS NOT NULL)",
            name="ck_document_extraction_units_content",
        ),
        UniqueConstraint("run_item_id", "unit_index", name="uq_document_extraction_units_item_index"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_processing_run_items.id", ondelete="CASCADE"), nullable=False
    )
    unit_index: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_ref: Mapped[dict] = mapped_column(_JSONB, nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text)
    table_data: Mapped[dict | list | None] = mapped_column(_JSONB)
    metadata_json: Mapped[dict] = mapped_column("metadata", _JSONB, nullable=False, default=dict, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )

    run_item: Mapped[DocumentProcessingRunItem] = relationship(back_populates="extraction_units")
