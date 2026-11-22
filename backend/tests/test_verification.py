from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.company import Company
from app.models.evidence_matching import TenderEvidenceMatchingRun
from app.models.tender import Tender
from app.models.verification import BidVerificationChecklist, BidVerificationItem
from app.schemas.analysis import TenderAnalysisResponse
from app.schemas.compliance import (
    ComplianceAnalysisResponse,
    ComplianceRequirement,
    ComplianceSource,
    TenderInformationItem,
)
from app.schemas.eligibility import (
    EligibilityAnalysisResponse,
    EligibilityRequirement,
    EligibilitySource,
)
from app.services import report as report_service
from app.services.verification import (
    create_verification_checklist,
    get_latest_verification_checklist,
    update_verification_item,
)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _analysis_pair(tender_id):
    eligibility = EligibilityAnalysisResponse(
        tender_id=str(tender_id),
        overall_status="incomplete",
        summary="Eligibility assessment complete.",
        requirements=[
            EligibilityRequirement(
                requirement="Bidder must provide a business registration certificate.",
                category="registration",
                status="evidence_missing",
                explanation="No authenticated bidder evidence was verified.",
                required_evidence=["Business registration certificate"],
                tender_source_ids=["T1"],
                bidder_source_ids=[],
            )
        ],
        sources=[EligibilitySource(
            source_id="T1", document_name="Tender.pdf", document_role="tender",
            unit_type="text", location={"page": 2}, citation="Page 2",
        )],
        evidence_found=True,
        bidder_evidence_found=False,
    )
    compliance = ComplianceAnalysisResponse(
        tender_id=str(tender_id),
        overall_status="incomplete",
        summary="Compliance assessment complete.",
        requirements=[
            ComplianceRequirement(
                requirement="The bidder shall submit a signed non-blacklisting declaration.",
                category="declaration",
                status="evidence_missing",
                explanation="No signed declaration was verified.",
                required_action="Obtain and sign the non-blacklisting declaration.",
                tender_source_ids=["T2"],
                bidder_source_ids=[],
            )
        ],
        tender_information=[TenderInformationItem(
            item="Bid submission deadline",
            category="submission_deadline",
            details="5 December 2026 at 15:00 IST",
            tender_source_ids=["T3"],
        )],
        post_award_obligations=[],
        sources=[ComplianceSource(
            source_id="T2", document_name="Annexure.docx", document_role="tender",
            unit_type="paragraph", location={"paragraph_index": 4}, citation="Paragraph 5",
        )],
        evidence_found=True,
        bidder_evidence_found=False,
    )
    return eligibility, compliance


def _saved_matching_run(session, tender, company):
    version_id = uuid4()
    company_document_id = uuid4()
    run = TenderEvidenceMatchingRun(
        tender_id=tender.id,
        company_id=company.id,
        summary="One requirement checked; no company evidence located.",
        result_json={
            "candidate_metrics": {
                "requirements_with_candidates": 0,
                "candidate_occurrence_count": 0,
                "unique_candidate_document_count": 0,
                "non_synthetic_candidate_document_count": 0,
                "synthetic_candidate_document_count": 0,
            },
            "results": [
                {
                    "requirement_id": "EL-1",
                    "requirement_type": "eligibility",
                    "requirement": "Bidder must provide a business registration certificate.",
                    "category": "registration",
                    "evidence_kind": "business_registration",
                    "status": "manual_verification_required",
                    "explanation": "Potentially relevant synthetic test documents were found.",
                    "required_evidence": ["Business registration certificate"],
                    "tender_sources": [{
                        "agent": "eligibility",
                        "source_id": "T1",
                        "document_name": "Tender.pdf",
                        "citation": "Page 2",
                        "location": {"page": 2},
                    }],
                    "candidates": [{
                        "company_document_id": str(company_document_id),
                        "document_title": "Sample GST Profile",
                        "category": "gst_certificate",
                        "version_id": str(version_id),
                        "version_number": 1,
                        "original_filename": "01_SAMPLE_GST_PROFILE.pdf",
                        "relevance_score": 1.0,
                        "matched_terms": ["business", "registration"],
                        "evidence_kind": "registration",
                        "category_match": True,
                        "synthetic_test_data": True,
                        "evidence_state": "validity_not_recorded",
                        "review_note": "Synthetic test data; not genuine bidder evidence.",
                        "units": [],
                    }],
                    "pending_versions": [],
                    "experience_evidence": None,
                },
                {
                    "requirement_id": "CO-1",
                    "requirement_type": "compliance",
                    "requirement": "The bidder shall submit a business registration certificate.",
                    "category": "registration",
                    "evidence_kind": "registration",
                    "status": "manual_verification_required",
                    "explanation": "Potentially relevant synthetic test documents were found.",
                    "required_evidence": ["Registration certificate"],
                    "tender_sources": [{
                        "agent": "compliance",
                        "source_id": "T2",
                        "document_name": "Annexure.docx",
                        "citation": "Paragraph 4",
                        "location": {"paragraph_index": 4},
                    }],
                    "candidates": [{
                        "company_document_id": str(company_document_id),
                        "document_title": "Sample GST Profile",
                        "category": "gst_certificate",
                        "version_id": str(version_id),
                        "version_number": 1,
                        "original_filename": "01_SAMPLE_GST_PROFILE.pdf",
                        "relevance_score": 1.0,
                        "matched_terms": ["registration"],
                        "evidence_kind": "registration",
                        "category_match": True,
                        "synthetic_test_data": True,
                        "evidence_state": "validity_not_recorded",
                        "review_note": "Synthetic test data; not genuine bidder evidence.",
                        "units": [],
                    }],
                    "pending_versions": [],
                    "experience_evidence": None,
                },
            ],
        },
    )
    session.add(run)
    session.commit()
    return run


def test_checklist_is_persisted_and_manual_verification_requires_reviewer(session):
    tender = Tender(name="Verification test tender")
    company = Company(legal_name="Example Bidder Ltd")
    session.add_all([tender, company])
    session.flush()
    eligibility, compliance = _analysis_pair(tender.id)
    run = _saved_matching_run(session, tender, company)

    matching_response = report_service.read_matching_run(run)
    checklist = create_verification_checklist(
        session,
        tender_id=tender.id,
        company_id=company.id,
        matching_run_id=run.id,
        readiness_status="incomplete",
        eligibility=eligibility,
        compliance=compliance,
        evidence_matching=matching_response,
        now=datetime(2026, 11, 21, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
    )

    assert checklist.company_id == company.id
    assert checklist.evidence_matching_run_id == run.id
    # The checklist now unions matching rows with agent-only requirements;
    # the compliance declaration must not disappear just because matching
    # only returned a registration row.
    assert checklist.item_count == 3
    assert checklist.pending_count == 3
    assert all(item.verification_status == "pending" for item in checklist.items)
    evidence_item = next(item for item in checklist.items if item.category == "eligibility_compliance")
    schedule_item = next(item for item in checklist.items if item.category == "tender_schedule")
    assert evidence_item.evidence_status == "manual_verification_required"
    assert set(evidence_item.source_ids) == {"eligibility:T1", "compliance:T2"}
    assert len(evidence_item.candidate_documents) == 1
    assert evidence_item.candidate_documents[0]["synthetic_test_data"] is True
    assert "5 December 2026" in schedule_item.requirement
    assert schedule_item.source_ids == ["CO:T3"]

    with pytest.raises(ValueError, match="reviewer name"):
        update_verification_item(
            session, tender_id=tender.id, checklist_id=checklist.id,
            item_id=evidence_item.id, verification_status="verified",
            verification_notes="Checked original certificate.", reviewer_name=None,
        )

    updated = update_verification_item(
        session, tender_id=tender.id, checklist_id=checklist.id,
        item_id=evidence_item.id, verification_status="verified",
        verification_notes="Original document checked by procurement.", reviewer_name="Reviewer A",
    )
    assert updated.verified_count == 1
    assert updated.pending_count == 2
    # The API orders checklist items by creation time. SQLite can assign the
    # same timestamp to several rows, so do not assume the verified item is
    # always the first item in the returned collection.
    updated_evidence_item = next(
        item for item in updated.items if item.id == evidence_item.id
    )
    assert updated_evidence_item.verified_at is not None
    assert updated_evidence_item.reviewer_name == "Reviewer A"

    loaded = get_latest_verification_checklist(session, tender.id)
    assert loaded is not None
    assert loaded.id == checklist.id
    loaded_evidence_item = next(item for item in loaded.items if item.category == "eligibility_compliance")
    assert loaded_evidence_item.verification_status == "verified"


def test_checklist_marks_passed_deadline_blocked_and_skips_reference_dates(session):
    tender = Tender(name="Past milestone test")
    session.add(tender)
    session.flush()
    eligibility, compliance = _analysis_pair(tender.id)
    compliance.tender_information = [
        TenderInformationItem(
            item="Bid submission deadline",
            category="submission_deadline",
            details="10 November 2026, 15:00 IST",
            tender_source_ids=["T3"],
        ),
        TenderInformationItem(
            item="Bid opening date",
            category="bid_opening",
            details="12 November 2026, 11:00 IST",
            tender_source_ids=["T4"],
        ),
        TenderInformationItem(
            item="Issue Date",
            category="other",
            details="10 November 2026",
            tender_source_ids=["T5"],
        ),
    ]

    checklist = create_verification_checklist(
        session,
        tender_id=tender.id,
        company_id=None,
        matching_run_id=None,
        readiness_status="incomplete",
        eligibility=eligibility,
        compliance=compliance,
        evidence_matching=None,
        now=datetime(2026, 11, 21, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
    )

    schedule_items = [item for item in checklist.items if item.category == "tender_schedule"]
    assert len(schedule_items) == 1
    assert schedule_items[0].verification_status == "blocked"
    assert "has passed" in (schedule_items[0].verification_notes or "")
    assert "Bid opening" not in schedule_items[0].requirement
    assert "Issue Date" not in schedule_items[0].requirement


def test_report_includes_selected_matching_run_and_creates_checklist(session, monkeypatch):
    tender = Tender(name="Integrated report test")
    company = Company(legal_name="Integrated Bidder Ltd")
    session.add_all([tender, company])
    session.flush()
    eligibility, compliance = _analysis_pair(tender.id)
    matching_run = _saved_matching_run(session, tender, company)

    analysis = TenderAnalysisResponse(
        tender_id=str(tender.id),
        report="# Tender overview\nTender reference: TEST-01 [S1]",
        sources=[{
            "source_id": "S1", "document_name": "Tender.pdf", "unit_type": "text",
            "location": {"page": 1}, "citation": "Page 1",
        }],
        evidence_found=True,
    )
    monkeypatch.setattr(report_service, "analyze_tender", lambda *_: analysis)
    monkeypatch.setattr(report_service, "analyze_eligibility", lambda *_: eligibility)
    monkeypatch.setattr(report_service, "analyze_compliance", lambda *_: compliance)

    result = report_service.generate_bid_readiness_report(
        session, tender.id, company_id=company.id, matching_run_id=matching_run.id,
    )

    assert result.evidence_matching is not None
    assert result.evidence_matching.id == matching_run.id
    assert result.company_name == company.legal_name
    assert "Company Evidence Matching" in result.report_markdown
    assert "Tender.pdf: Page 2" in result.report_markdown
    assert result.verification_checklist is not None
    assert result.verification_checklist.company_id == company.id
    assert result.verification_checklist.item_count == 3
    assert all(item.verification_status == "pending" for item in result.verification_checklist.items)
    matched_registration = result.eligibility_requirements[0]
    assert matched_registration.status == "evidence_missing"
    assert matched_registration.company_evidence_status == "manual_verification_required"
    assert "synthetic/test documents" in matched_registration.company_evidence_note
    assert matched_registration.company_evidence_candidate_names == ["01_SAMPLE_GST_PROFILE.pdf"]
    assert result.bidder_evidence_found is False
    assert "No bidder supporting documents have been uploaded" not in matched_registration.explanation
    assert "Consolidated Eligibility and Bid-stage Compliance" in result.report_markdown
    assert "01_SAMPLE_GST_PROFILE.pdf" in result.report_markdown


def test_report_groups_duplicate_agent_requirements_and_actions():
    tender_id = uuid4()
    eligibility, compliance = _analysis_pair(tender_id)
    compliance.requirements = [ComplianceRequirement(
        requirement="The bidder shall submit its legal business registration certificate.",
        category="registration",
        status="evidence_missing",
        explanation="No bidder registration evidence was verified.",
        required_action="Provide business registration certificate.",
        tender_source_ids=["T2"],
        bidder_source_ids=[],
    )]
    groups = report_service._combined_assessment_groups(eligibility, compliance)
    registration = next(group for group in groups if group["key"] == "business_registration")
    assert registration["agents"] == ["Eligibility", "Compliance"]
    assert registration["source_ids"] == ["EL:T1", "CO:T2"]

    repeated_action = "Provide product datasheets and a clause-by-clause technical compliance statement."
    compliance.requirements = [
        ComplianceRequirement(
            requirement=f"Product requirement {index}: demonstrate technical compliance.",
            category="technical",
            status="evidence_missing",
            explanation="No product evidence supplied.",
            required_action=repeated_action,
            tender_source_ids=[f"T{index}"],
            bidder_source_ids=[],
        )
        for index in range(1, 8)
    ]
    actions = report_service._build_actions(
        EligibilityAnalysisResponse(
            tender_id=str(tender_id), overall_status="incomplete", summary="None.",
            requirements=[], sources=[], evidence_found=True, bidder_evidence_found=False,
        ),
        compliance,
    )
    assert len(actions) == 1
    assert len(actions[0].covered_requirements) == 7
    assert len(actions[0].source_ids) == 7


def test_report_rejects_matching_run_from_another_company(session):
    tender = Tender(name="Scoped report tender")
    company = Company(legal_name="Correct Bidder Ltd")
    other_company = Company(legal_name="Different Bidder Ltd")
    session.add_all([tender, company, other_company])
    session.flush()
    run = _saved_matching_run(session, tender, other_company)

    with pytest.raises(ValueError, match="No saved Evidence Matching run"):
        report_service.get_matching_run(session, tender.id, company.id, run.id)
        # Report's explicit run lookup is scoped by both tender and company.
        report_service.generate_bid_readiness_report(
            session, tender.id, company_id=company.id, matching_run_id=run.id,
        )


def test_requirement_normalization_keeps_turnover_and_experience_separate():
    from app.services.requirement_normalization import (
        canonical_requirement_key,
        evidence_requirement_family,
    )

    turnover = "Average annual turnover of INR 40,00,000 supported by audited financial statements."
    experience = "At least two similar supply-and-installation contracts with work orders and completion certificates."
    assert canonical_requirement_key("eligibility", turnover) == "turnover_evidence"
    assert canonical_requirement_key("compliance", experience) == "similar_contract_experience"
    assert evidence_requirement_family("Purchase orders or work orders for two similar contracts") == "similar_contract_experience"
    assert evidence_requirement_family("Audited financial statements or CA certificate showing turnover") == "turnover_evidence"


def test_action_grouping_uses_requirement_family_not_shared_keywords():
    from app.schemas.report import ReportAction

    experience = ReportAction(
        action="Provide work orders and completion certificates.", category="eligibility",
        status="evidence_missing", source_ids=["EL:T1"],
        covered_requirements=["At least two similar contracts with work orders and completion certificates."],
    )
    turnover = ReportAction(
        action="Provide audited statements and CA certificate.", category="eligibility",
        status="evidence_missing", source_ids=["EL:T2"],
        covered_requirements=["Average annual turnover supported by audited financial statements."],
    )
    merged = report_service._merge_duplicate_actions([experience, turnover])
    assert len(merged) == 2
    assert {item.covered_requirements[0] for item in merged} == {
        experience.covered_requirements[0], turnover.covered_requirements[0]
    }


def test_checklist_includes_agent_only_product_requirements_when_matching_run_exists(session):
    tender = Tender(name="Checklist union test")
    company = Company(legal_name="Checklist Bidder Ltd")
    session.add_all([tender, company])
    session.flush()
    eligibility, compliance = _analysis_pair(tender.id)
    product_requirements = [
        ("Interactive display", "Interactive display must be 65 inch, 4K UHD and 20 touch points."),
        ("Mini PC", "Mini PC must include Core i5, 8 GB RAM and 256 GB SSD."),
        ("Wall-mount", "Provide compatible heavy-duty wall mount and fittings."),
        ("Wireless keyboard and mouse", "Provide compatible wireless keyboard and mouse."),
        ("Cables and accessories", "Provide all cables and accessories for installation."),
    ]
    compliance.requirements.extend(
        ComplianceRequirement(
            requirement=requirement,
            category="technical",
            status="evidence_missing",
            explanation="No matching technical bidder evidence was located.",
            required_action="Submit technical compliance evidence.",
            tender_source_ids=[f"T{index + 10}"],
            bidder_source_ids=[],
        )
        for index, (_, requirement) in enumerate(product_requirements)
    )
    run = _saved_matching_run(session, tender, company)
    matching_response = report_service.read_matching_run(run)

    checklist = create_verification_checklist(
        session,
        tender_id=tender.id,
        company_id=company.id,
        matching_run_id=run.id,
        readiness_status="incomplete",
        eligibility=eligibility,
        compliance=compliance,
        evidence_matching=matching_response,
        now=datetime(2026, 11, 21, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
    )

    requirements = "\\n".join(item.requirement.casefold() for item in checklist.items)
    for label, _ in product_requirements:
        assert label.casefold() in requirements
    assert len([item for item in checklist.items if item.category == "tender_schedule"]) == 1
    assert all(item.verification_status == "pending" for item in checklist.items)


def test_merged_actions_use_one_clean_family_action_instead_of_concatenating_agent_text():
    from app.schemas.report import ReportAction

    first = ReportAction(
        action="Replace synthetic test-only candidate documents with authentic bidder evidence for: legal registration.",
        category="eligibility", status="evidence_missing", source_ids=["EL:T1"],
        covered_requirements=["Bidder must be a legally registered business in India."],
    )
    second = ReportAction(
        action="Replace synthetic test-only candidate documents with authentic bidder evidence for: registration certificate.",
        category="compliance", status="evidence_missing", source_ids=["CO:T2"],
        covered_requirements=["Bidder must provide a legal business registration certificate."],
    )
    merged = report_service._merge_duplicate_actions([first, second])
    assert len(merged) == 1
    assert merged[0].category == "combined"
    assert merged[0].source_ids == ["EL:T1", "CO:T2"]
    assert merged[0].action.count("Provide an authentic business registration certificate") == 1
    assert "Replace synthetic test-only" in merged[0].action


def test_checklist_action_templates_do_not_hardcode_tender_amounts_or_merge_duplicate_instructions():
    from app.services.verification import _canonical_checklist_action

    payload = {"candidate_documents": []}
    turnover = _canonical_checklist_action("turnover_evidence", payload)
    emd = _canonical_checklist_action("emd_submission", payload)
    assert turnover and "INR 40,00,000" not in turnover
    assert emd and "INR 80,000" not in emd
    assert "tender-specified amount" in emd
