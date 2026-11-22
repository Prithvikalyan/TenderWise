from datetime import date, datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SQLAlchemyEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.tender import utc_now


class CompanyDocumentCategory(str, Enum):
    BUSINESS_REGISTRATION = "business_registration"
    GST_CERTIFICATE = "gst_certificate"
    PAN_CARD = "pan_card"
    AUDITED_FINANCIAL_STATEMENTS = "audited_financial_statements"
    TURNOVER_CERTIFICATE = "turnover_certificate"
    PURCHASE_ORDER = "purchase_order"
    WORK_ORDER = "work_order"
    COMPLETION_CERTIFICATE = "completion_certificate"
    NON_BLACKLISTING_DECLARATION = "non_blacklisting_declaration"
    TECHNICAL_CERTIFICATION = "technical_certification"
    LICENSE = "license"
    OTHER = "other"


class Company(Base):
    """Reusable company profile; ownership authorization is added with authentication."""

    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint("length(trim(legal_name)) > 0", name="ck_companies_legal_name_nonempty"),
        Index("ix_companies_legal_name", "legal_name"),
        Index("ix_companies_archived_updated", "is_archived", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    registration_number: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(Text)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now()
    )

    documents: Mapped[list["CompanyDocument"]] = relationship(
        back_populates="company", cascade="all, delete-orphan", passive_deletes=True
    )
    processing_runs: Mapped[list["DocumentProcessingRun"]] = relationship(
        back_populates="company", cascade="all, delete-orphan", passive_deletes=True
    )


class CompanyDocument(Base):
    """Logical reusable document; uploaded revisions are immutable version records."""

    __tablename__ = "company_documents"
    __table_args__ = (
        CheckConstraint(
            "category IN ("
            "'business_registration', 'gst_certificate', 'pan_card', "
            "'audited_financial_statements', 'turnover_certificate', "
            "'purchase_order', 'work_order', 'completion_certificate', "
            "'non_blacklisting_declaration', 'technical_certification', 'license', 'other')",
            name="ck_company_documents_category",
        ),
        CheckConstraint("length(trim(title)) > 0", name="ck_company_documents_title_nonempty"),
        Index("ix_company_documents_company_created", "company_id", "created_at"),
        Index("ix_company_documents_company_category", "company_id", "category"),
        Index("ix_company_documents_company_archived", "company_id", "is_archived"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[CompanyDocumentCategory] = mapped_column(
        SQLAlchemyEnum(
            CompanyDocumentCategory,
            name="company_document_category",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_type: [member.value for member in enum_type],
            length=48,
        ),
        nullable=False,
    )
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now()
    )

    company: Mapped[Company] = relationship(back_populates="documents")
    versions: Mapped[list["CompanyDocumentVersion"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CompanyDocumentVersion.version_number.desc()",
    )

    @property
    def current_version(self) -> "CompanyDocumentVersion | None":
        return self.versions[0] if self.versions else None

    @property
    def version_count(self) -> int:
        return len(self.versions)


class CompanyDocumentVersion(Base):
    """Immutable uploaded file revision plus revision-specific evidence metadata."""

    __tablename__ = "company_document_versions"
    __table_args__ = (
        CheckConstraint("version_number >= 1", name="ck_company_document_versions_number"),
        CheckConstraint("size_bytes > 0", name="ck_company_document_versions_size"),
        CheckConstraint(
            "expiry_date IS NULL OR issue_date IS NULL OR expiry_date >= issue_date",
            name="ck_company_document_versions_dates",
        ),
        UniqueConstraint("document_id", "version_number", name="uq_company_document_versions_number"),
        UniqueConstraint("storage_key", name="uq_company_document_versions_storage_key"),
        Index("ix_company_document_versions_document_created", "document_id", "created_at"),
        Index("ix_company_document_versions_expiry", "expiry_date"),
        Index("ix_company_document_versions_financial_year", "financial_year"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("company_documents.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str] = mapped_column(String(32), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    issue_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    financial_year: Mapped[str | None] = mapped_column(String(20))
    issuer: Mapped[str | None] = mapped_column(String(255))
    reference_number: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )

    document: Mapped[CompanyDocument] = relationship(back_populates="versions")
    processing_run_items: Mapped[list["DocumentProcessingRunItem"]] = relationship(
        back_populates="company_document_version", passive_deletes="all"
    )
