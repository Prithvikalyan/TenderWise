from datetime import date, datetime, UTC
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class MilestoneCategory(str, Enum):
    SUBMISSION_DEADLINE = "submission_deadline"
    CLARIFICATION_DEADLINE = "clarification_deadline"
    PRE_BID_MEETING = "pre_bid_meeting"
    BID_OPENING = "bid_opening"
    EMD_DEADLINE = "emd_deadline"
    DOCUMENT_DEADLINE = "document_deadline"
    OTHER = "other"


def milestone_now() -> datetime:
    return datetime.now(UTC)


class TenderMilestone(Base):
    """A user-confirmed date or milestone associated with a tender workspace."""

    __tablename__ = "tender_milestones"
    __table_args__ = (
        CheckConstraint(
            "category IN ('submission_deadline', 'clarification_deadline', 'pre_bid_meeting', "
            "'bid_opening', 'emd_deadline', 'document_deadline', 'other')",
            name="ck_tender_milestones_category",
        ),
        CheckConstraint("length(trim(title)) > 0", name="ck_tender_milestones_title_nonempty"),
        Index("ix_tender_milestones_due_date", "due_date"),
        Index("ix_tender_milestones_tender_due_date", "tender_id", "due_date"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tender_id: Mapped[UUID] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[MilestoneCategory] = mapped_column(
        String(40), nullable=False, default=MilestoneCategory.OTHER, server_default=text("'other'")
    )
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    source_reference: Mapped[str | None] = mapped_column(String(500))
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=milestone_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=milestone_now, onupdate=milestone_now, server_default=func.now())

    tender: Mapped["Tender"] = relationship()
