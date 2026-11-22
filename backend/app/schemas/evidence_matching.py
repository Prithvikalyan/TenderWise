from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


EvidenceMatchStatus = Literal[
    "candidate_found",
    "potential_match",
    "missing_evidence",
    "expired_evidence",
    "manual_verification_required",
    "processing_required",
    "not_company_evidence",
]


class TenderEvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: Literal["eligibility", "compliance"]
    source_id: str
    document_name: str
    citation: str
    location: dict[str, Any] = Field(default_factory=dict)


class CompanyEvidenceUnitReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction_unit_id: UUID
    unit_index: int
    unit_type: str
    source_ref: dict[str, Any]
    excerpt: str = Field(max_length=700)


class CompanyEvidencePendingVersion(BaseModel):
    """Latest company document version that is relevant but not yet usable."""

    company_document_id: UUID
    document_title: str
    category: str
    version_id: UUID
    version_number: int
    original_filename: str
    inferred_evidence_kind: str
    processing_state: Literal[
        "not_queued", "pending", "processing", "failed", "completed_without_text"
    ] = "not_queued"


class ExperienceEvidenceDocument(BaseModel):
    version_id: UUID
    document_title: str
    original_filename: str
    category: str
    version_number: int
    evidence_role: Literal["order_document", "completion_certificate"]
    synthetic_test_data: bool = False


class ExperienceContractGroup(BaseModel):
    """Documents linked by the same explicit contract/order reference."""

    reference_number: str
    documents: list[ExperienceEvidenceDocument] = Field(default_factory=list)
    has_order_document: bool = False
    has_completion_certificate: bool = False


class ExperienceEvidenceSummary(BaseModel):
    """Reference-based grouping, not a finding that contracts are qualifying."""

    distinct_referenced_contract_count: int = 0
    unreferenced_document_count: int = 0
    contract_groups: list[ExperienceContractGroup] = Field(default_factory=list)
    unreferenced_documents: list[ExperienceEvidenceDocument] = Field(default_factory=list)
    note: str


class CompanyEvidenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("financial_year", "issuer", "reference_number", mode="before")
    @classmethod
    def clean_placeholder_metadata(cls, value):
        if value is None:
            return None
        cleaned = str(value).strip()
        if cleaned.casefold() in {"", "string", "null", "none", "n/a", "na", "not provided", "not applicable", "-"}:
            return None
        return cleaned

    company_document_id: UUID
    document_title: str
    category: str
    version_id: UUID
    version_number: int
    original_filename: str
    issue_date: date | None = None
    expiry_date: date | None = None
    financial_year: str | None = None
    issuer: str | None = None
    reference_number: str | None = None
    relevance_score: float = Field(ge=0, le=1)
    matched_terms: list[str] = Field(default_factory=list)
    evidence_kind: str = "generic"
    category_match: bool = False
    synthetic_test_data: bool = False
    evidence_state: Literal[
        "not_expired_by_metadata",
        "expired_by_metadata",
        "validity_not_recorded",
    ]
    review_note: str
    units: list[CompanyEvidenceUnitReference] = Field(default_factory=list, max_length=5)


class EvidenceRequirementMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    requirement_type: Literal["eligibility", "compliance"]
    requirement: str = Field(min_length=3, max_length=1200)
    category: str
    evidence_kind: str = "generic"
    status: EvidenceMatchStatus
    explanation: str
    required_evidence: list[str] = Field(default_factory=list)
    tender_sources: list[TenderEvidenceCitation] = Field(default_factory=list)
    candidates: list[CompanyEvidenceCandidate] = Field(default_factory=list, max_length=5)
    pending_versions: list[CompanyEvidencePendingVersion] = Field(default_factory=list)
    experience_evidence: ExperienceEvidenceSummary | None = None


class EvidenceMatchingRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    tender_id: UUID
    company_id: UUID
    status: Literal["completed"]
    eligibility_requirement_count: int
    compliance_requirement_count: int
    candidate_found_count: int
    potential_match_count: int
    requirements_with_candidates: int = 0
    candidate_occurrence_count: int = 0
    unique_candidate_document_count: int = 0
    non_synthetic_candidate_document_count: int = 0
    synthetic_candidate_document_count: int = 0
    company_document_count: int
    latest_version_count: int = 0
    documents_without_versions_count: int = 0
    processed_company_document_count: int
    unprocessed_company_document_count: int
    missing_evidence_count: int
    expired_evidence_count: int
    unique_expired_document_count: int = 0
    manual_review_count: int
    processing_required_count: int
    non_company_evidence_count: int = 0
    summary: str
    created_at: datetime
    results: list[EvidenceRequirementMatch]


class EvidenceMatchingRunListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tender_id: UUID
    company_id: UUID
    status: str
    eligibility_requirement_count: int
    compliance_requirement_count: int
    candidate_found_count: int
    potential_match_count: int
    company_document_count: int
    latest_version_count: int = 0
    documents_without_versions_count: int = 0
    processed_company_document_count: int
    unprocessed_company_document_count: int
    missing_evidence_count: int
    expired_evidence_count: int
    unique_expired_document_count: int = 0
    manual_review_count: int
    processing_required_count: int
    non_company_evidence_count: int = 0
    summary: str
    created_at: datetime


class EvidenceMatchingRunListResponse(BaseModel):
    items: list[EvidenceMatchingRunListItem]
    total: int
    page: int
    limit: int
    offset: int
