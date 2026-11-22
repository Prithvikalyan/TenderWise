from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.tender import utc_now

_JSONB = JSON().with_variant(JSONB, "postgresql")


class TenderEvidenceMatchingRun(Base):
    """Persisted snapshot of tender requirements matched to company evidence."""

    __tablename__ = "tender_evidence_matching_runs"
    __table_args__ = (
        CheckConstraint("status IN ('completed')", name="ck_evidence_matching_runs_status"),
        CheckConstraint("eligibility_requirement_count >= 0", name="ck_evidence_matching_runs_eligibility_count"),
        CheckConstraint("compliance_requirement_count >= 0", name="ck_evidence_matching_runs_compliance_count"),
        CheckConstraint("candidate_found_count >= 0", name="ck_evidence_matching_runs_candidate_count"),
        CheckConstraint("potential_match_count >= 0", name="ck_evidence_matching_runs_potential_count"),
        CheckConstraint("company_document_count >= 0", name="ck_evidence_matching_runs_document_count"),
        CheckConstraint("processed_company_document_count >= 0", name="ck_evidence_matching_runs_processed_document_count"),
        CheckConstraint("unprocessed_company_document_count >= 0", name="ck_evidence_matching_runs_unprocessed_document_count"),
        CheckConstraint("missing_evidence_count >= 0", name="ck_evidence_matching_runs_missing_count"),
        CheckConstraint("expired_evidence_count >= 0", name="ck_evidence_matching_runs_expired_count"),
        CheckConstraint("manual_review_count >= 0", name="ck_evidence_matching_runs_manual_count"),
        CheckConstraint("processing_required_count >= 0", name="ck_evidence_matching_runs_processing_count"),
        Index("ix_evidence_matching_runs_tender_created", "tender_id", text("created_at DESC")),
        Index("ix_evidence_matching_runs_company_created", "company_id", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tender_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="completed", server_default=text("'completed'"))
    eligibility_requirement_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    compliance_requirement_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    candidate_found_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    potential_match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    company_document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    latest_version_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    documents_without_versions_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    processed_company_document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    unprocessed_company_document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    missing_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    expired_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    unique_expired_document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    manual_review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    processing_required_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    non_company_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[dict] = mapped_column(_JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
