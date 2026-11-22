"""API contracts for the consolidated TenderWise bid-readiness report."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.analysis import TenderAnalysisResponse
from app.schemas.compliance import (
    ComplianceRequirement,
    PostAwardObligation,
    TenderInformationItem,
)
from app.schemas.eligibility import EligibilityRequirement
from app.schemas.evidence_matching import EvidenceMatchingRunResponse
from app.schemas.verification import VerificationChecklistResponse


ReadinessStatus = Literal[
    "ready",
    "not_ready",
    "incomplete",
    "needs_review",
    "insufficient_evidence",
]


class ReportSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    agent: Literal["tender_analysis", "eligibility", "compliance"]
    original_source_id: str
    document_name: str
    document_role: str
    unit_type: str
    location: dict[str, Any]
    citation: str


class ReportAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=3, max_length=1200)
    category: Literal["eligibility", "compliance", "combined"]
    status: str
    source_ids: list[str] = Field(default_factory=list)
    covered_requirements: list[str] = Field(default_factory=list)


class BidReadinessReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tender_id: str
    overall_status: ReadinessStatus
    summary: str
    report_markdown: str
    tender_analysis: TenderAnalysisResponse
    eligibility_requirements: list[EligibilityRequirement]
    compliance_requirements: list[ComplianceRequirement]
    tender_information: list[TenderInformationItem]
    post_award_obligations: list[PostAwardObligation]
    action_items: list[ReportAction]
    sources: list[ReportSource]
    company_name: str | None = None
    evidence_matching: EvidenceMatchingRunResponse | None = None
    verification_checklist: VerificationChecklistResponse | None = None
    orchestration_engine: Literal["langgraph", "sequential_fallback"] = "sequential_fallback"
    evidence_found: bool
    bidder_evidence_found: bool
