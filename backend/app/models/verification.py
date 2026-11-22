"""Persistent, manually completed pre-submission verification checklists."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
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

_JSON = JSON().with_variant(JSONB, "postgresql")


class BidVerificationChecklist(Base):
    __tablename__ = "bid_verification_checklists"
    __table_args__ = (
        CheckConstraint(
            "readiness_status IN ('ready', 'not_ready', 'incomplete', 'needs_review', 'insufficient_evidence')",
            name="ck_bid_verification_checklists_readiness_status",
        ),
        Index("ix_bid_verification_checklists_tender_created", "tender_id", text("created_at DESC")),
        Index("ix_bid_verification_checklists_company_created", "company_id", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tender_id: Mapped[UUID] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))
    evidence_matching_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tender_evidence_matching_runs.id", ondelete="SET NULL")
    )
    readiness_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now())

    items: Mapped[list["BidVerificationItem"]] = relationship(
        back_populates="checklist", cascade="all, delete-orphan", passive_deletes=True,
        order_by="BidVerificationItem.created_at.asc()",
    )


class BidVerificationItem(Base):
    __tablename__ = "bid_verification_items"
    __table_args__ = (
        CheckConstraint("length(trim(requirement)) > 0", name="ck_bid_verification_items_requirement_nonempty"),
        CheckConstraint(
            "verification_status IN ('pending', 'in_progress', 'verified', 'blocked', 'not_applicable')",
            name="ck_bid_verification_items_status",
        ),
        UniqueConstraint("checklist_id", "requirement_key", name="uq_bid_verification_items_requirement"),
        Index("ix_bid_verification_items_checklist_status", "checklist_id", "verification_status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    checklist_id: Mapped[UUID] = mapped_column(ForeignKey("bid_verification_checklists.id", ondelete="CASCADE"), nullable=False)
    requirement_key: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    requirement: Mapped[str] = mapped_column(Text, nullable=False)
    required_action: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_status: Mapped[str] = mapped_column(String(40), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default=text("'pending'"))
    source_ids: Mapped[list] = mapped_column(_JSON, nullable=False, default=list, server_default=text("'[]'"))
    candidate_documents: Mapped[list] = mapped_column(_JSON, nullable=False, default=list, server_default=text("'[]'"))
    verification_notes: Mapped[str | None] = mapped_column(Text)
    reviewer_name: Mapped[str | None] = mapped_column(String(255))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now())

    checklist: Mapped[BidVerificationChecklist] = relationship(back_populates="items")
