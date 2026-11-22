from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db_session
from app.main import app
from app.models.company import (
    Company,
    CompanyDocument,
    CompanyDocumentCategory,
    CompanyDocumentVersion,
)
from app.models.document_processing import (
    DocumentExtractionUnit,
    DocumentProcessingRun,
    DocumentProcessingRunItem,
)
from app.models.evidence_matching import TenderEvidenceMatchingRun
from app.models.tender import Tender
from app.schemas.compliance import (
    ComplianceAnalysisResponse,
    ComplianceRequirement,
    ComplianceSource,
)
from app.schemas.eligibility import (
    EligibilityAnalysisResponse,
    EligibilityRequirement,
    EligibilitySource,
)
from app.services.evidence_matching import create_evidence_matching_run, read_matching_run


def _analysis_pair():
    eligibility = EligibilityAnalysisResponse(
        tender_id="00000000-0000-0000-0000-000000000001",
        overall_status="incomplete",
        summary="Eligibility requirements extracted.",
        requirements=[
            EligibilityRequirement(
                requirement="Bidder must demonstrate minimum annual turnover through audited financial statements.",
                category="financial",
                status="evidence_missing",
                explanation="No bidder-side tender upload was assessed.",
                required_evidence=["Audited financial statements", "Turnover certificate"],
                tender_source_ids=["T1"],
                bidder_source_ids=[],
            )
        ],
        sources=[
            EligibilitySource(
                source_id="T1",
                document_name="Tender Notice.pdf",
                document_role="tender",
                unit_type="text",
                location={"page": 2, "block_index": 4},
                citation="Page 2, block 4",
            )
        ],
        evidence_found=True,
        bidder_evidence_found=False,
    )
    compliance = ComplianceAnalysisResponse(
        tender_id="00000000-0000-0000-0000-000000000001",
        overall_status="incomplete",
        summary="Compliance requirements extracted.",
        requirements=[
            ComplianceRequirement(
                requirement="The bidder shall submit a valid GST registration certificate.",
                category="documents",
                status="evidence_missing",
                explanation="No bidder-side tender upload was assessed.",
                required_action="Provide GST registration certificate.",
                tender_source_ids=["T2"],
                bidder_source_ids=[],
            )
        ],
        tender_information=[],
        post_award_obligations=[],
        sources=[
            ComplianceSource(
                source_id="T2",
                document_name="Eligibility Annexure.docx",
                document_role="tender",
                unit_type="text",
                location={"page": 1, "paragraph_index": 6},
                citation="Page 1, paragraph 6",
            )
        ],
        evidence_found=True,
        bidder_evidence_found=False,
    )
    return eligibility, compliance


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _seed_company_document(
    session: Session,
    company: Company,
    *,
    text: str | None,
    category: CompanyDocumentCategory = CompanyDocumentCategory.TURNOVER_CERTIFICATE,
    expiry_date: date | None = None,
    title: str = "Sample company document",
    filename: str = "sample-evidence.pdf",
    reference_number: str | None = None,
    table_data: dict | list | None = None,
):
    document = CompanyDocument(
        company_id=company.id,
        title=title,
        category=category,
    )
    version = CompanyDocumentVersion(
        version_number=1,
        original_filename=filename,
        storage_key=f"company/{uuid4()}.pdf",
        extension=".pdf",
        content_type="application/pdf",
        size_bytes=100,
        issue_date=date(2024, 1, 1),
        expiry_date=expiry_date,
        reference_number=reference_number,
    )
    document.versions.append(version)
    session.add(document)
    session.flush()

    run = DocumentProcessingRun(
        tender_id=None,
        company_id=company.id,
        status="completed",
        requested_document_count=1,
    )
    item = DocumentProcessingRunItem(
        run=run,
        company_document_version_id=version.id,
        status="completed",
        attempt_count=1,
        max_attempts=3,
        fencing_token=1,
    )
    session.add(run)
    session.flush()
    if text is not None or table_data is not None:
        session.add(
            DocumentExtractionUnit(
                run_item_id=item.id,
                unit_index=0,
                unit_type="table" if table_data is not None else "text",
                source_ref={"page": 1, "filename": version.original_filename, "source_scope": "company"},
                content_text=text,
                table_data=table_data,
                metadata_json={"format": "pdf"},
            )
        )
    session.flush()
    return document, version


def test_company_matching_reads_xlsx_value_and_formula_cell_shapes():
    from app.services.evidence_matching import _unit_text

    unit = SimpleNamespace(
        content_text=None,
        table_data={"rows": [
            {"cells": [
                {"coordinate": "A1", "data_type": "s", "value": "Financial year"},
                {"coordinate": "B1", "data_type": "s", "value": "Gross receipts (INR)"},
            ]},
            {"cells": [
                {"coordinate": "A2", "data_type": "s", "value": "2024-25"},
                {"coordinate": "B2", "data_type": "n", "value": 50000000},
                {"coordinate": "C2", "data_type": "formula", "formula": "=B2+100", "cached_value": None},
            ]},
        ]},
    )
    text = _unit_text(unit)
    assert "Financial year" in text
    assert "Gross receipts (INR)" in text
    assert "2024-25" in text
    assert "50000000" in text
    assert "formula not calculated: =B2+100" in text


def test_turnover_xlsx_table_values_become_searchable_company_evidence(db_session):
    tender = Tender(name="XLSX turnover table evidence")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session,
        company,
        text=None,
        category=CompanyDocumentCategory.TURNOVER_CERTIFICATE,
        title="Sample Turnover Schedule",
        filename="06_SAMPLE_TURNOVER_SCHEDULE.xlsx",
        table_data={"rows": [
            {"cells": [
                {"coordinate": "A1", "data_type": "s", "value": "SAMPLE - NOT VALID - FOR SOFTWARE TESTING ONLY"},
            ]},
            {"cells": [
                {"coordinate": "A2", "data_type": "s", "value": "Financial year"},
                {"coordinate": "B2", "data_type": "s", "value": "Gross receipts (INR)"},
                {"coordinate": "D2", "data_type": "s", "value": "Illustrative turnover"},
            ]},
            {"cells": [
                {"coordinate": "A3", "data_type": "s", "value": "2024-25"},
                {"coordinate": "B3", "data_type": "n", "value": 50000000},
                {"coordinate": "D3", "data_type": "formula", "formula": "=B3+C3", "cached_value": None},
            ]},
        ]},
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    financial = next(item for item in result.results if item.requirement_type == "eligibility")
    assert financial.status == "manual_verification_required"
    assert financial.candidates[0].original_filename == "06_SAMPLE_TURNOVER_SCHEDULE.xlsx"
    assert financial.candidates[0].synthetic_test_data is True


def test_matching_run_finds_latest_company_document_and_keeps_citations(db_session):
    tender = Tender(name="Sample tender")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session,
        company,
        text="Annual turnover revenue and gross receipts supported by financial statements for the financial year.",
        category=CompanyDocumentCategory.TURNOVER_CERTIFICATE,
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )

    assert result.eligibility_requirement_count == 1
    assert result.compliance_requirement_count == 1
    financial = next(item for item in result.results if item.requirement_type == "eligibility")
    assert financial.status in {"candidate_found", "potential_match"}
    assert financial.candidates[0].category == "turnover_certificate"
    assert financial.tender_sources[0].citation == "Page 2, block 4"
    assert financial.candidates[0].units[0].source_ref["page"] == 1

    saved = db_session.get(TenderEvidenceMatchingRun, result.id)
    assert saved is not None
    reread = read_matching_run(saved)
    assert reread.results[0].requirement_id in {"EL-1", "CO-1"}


def test_expired_candidate_is_not_reported_as_current_evidence(db_session):
    tender = Tender(name="Sample tender")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session,
        company,
        text="Annual turnover revenue gross receipts audited financial statements.",
        category=CompanyDocumentCategory.TURNOVER_CERTIFICATE,
        expiry_date=date(2025, 11, 30),
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    financial = next(item for item in result.results if item.requirement_type == "eligibility")
    assert financial.status == "expired_evidence"
    assert financial.candidates[0].evidence_state == "expired_by_metadata"


def test_synthetic_document_is_never_treated_as_genuine_evidence(db_session):
    tender = Tender(name="Sample tender")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session,
        company,
        text="SAMPLE - NOT VALID - FOR SOFTWARE TESTING ONLY. Annual turnover revenue gross receipts.",
        category=CompanyDocumentCategory.TURNOVER_CERTIFICATE,
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    financial = next(item for item in result.results if item.requirement_type == "eligibility")
    assert financial.status == "manual_verification_required"
    assert financial.candidates[0].synthetic_test_data is True


def test_unprocessed_latest_company_version_is_reported_as_processing_required(db_session):
    tender = Tender(name="Sample tender")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(db_session, company, text=None)
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    assert result.unprocessed_company_document_count == 1
    financial = next(item for item in result.results if item.evidence_kind == "financial")
    registration = next(item for item in result.results if item.evidence_kind == "registration")
    assert financial.status == "processing_required"
    assert registration.status == "missing_evidence"


def test_matching_api_persists_and_retrieves_run(db_session, monkeypatch):
    import app.api.routes.evidence_matching as route_module

    tender = Tender(name="Sample tender")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.commit()
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    monkeypatch.setattr(route_module, "analyze_eligibility", lambda *_args: eligibility)
    monkeypatch.setattr(route_module, "analyze_compliance", lambda *_args: compliance)

    def override_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    try:
        with TestClient(app) as client:
            url = f"/api/tenders/{tender.id}/companies/{company.id}/evidence-matching-runs"
            response = client.post(url)
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["status"] == "completed"
            assert len(body["results"]) == 2
            assert body["requirements_with_candidates"] == 0
            assert body["candidate_occurrence_count"] == 0
            assert body["unique_candidate_document_count"] == 0
            detail = client.get(f"{url}/{body['id']}")
            assert detail.status_code == 200
            assert detail.json()["id"] == body["id"]
            assert detail.json()["candidate_occurrence_count"] == 0
            history = client.get(url)
            assert history.status_code == 200
            assert history.json()["total"] == 1
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_unprocessed_latest_version_does_not_fall_back_to_old_processed_version(db_session):
    tender = Tender(name="Sample tender")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    document, _old_version = _seed_company_document(
        db_session,
        company,
        text="Annual turnover revenue gross receipts financial statement.",
        category=CompanyDocumentCategory.TURNOVER_CERTIFICATE,
    )
    newer_version = CompanyDocumentVersion(
        version_number=2,
        original_filename="newer-turnover.pdf",
        storage_key=f"company/{uuid4()}.pdf",
        extension=".pdf",
        content_type="application/pdf",
        size_bytes=120,
    )
    document.versions.append(newer_version)
    db_session.flush()
    db_session.expire(document, ["versions"])
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )

    financial = next(item for item in result.results if item.requirement_type == "eligibility")
    assert financial.status == "processing_required"
    assert financial.candidates == []
    assert result.unprocessed_company_document_count == 1
    assert len(financial.pending_versions) == 1
    assert financial.pending_versions[0].original_filename == "newer-turnover.pdf"
    assert financial.pending_versions[0].processing_state == "not_queued"


def test_registration_requirement_rejects_work_order_even_with_shared_generic_terms(db_session):
    tender = Tender(name="Registration category test")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session,
        company,
        text="SAMPLE work order for legal infrastructure installation and project completion.",
        category=CompanyDocumentCategory.WORK_ORDER,
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )

    registration = next(item for item in result.results if item.requirement_type == "compliance")
    assert registration.evidence_kind == "registration"
    assert registration.candidates == []
    assert registration.status == "missing_evidence"


def test_experience_requirement_only_returns_work_order_and_completion_categories(db_session):
    from app.schemas.eligibility import EligibilityRequirement

    tender = Tender(name="Experience category test")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session,
        company,
        text="SAMPLE work order for supply and installation of infrastructure equipment under contract.",
        category=CompanyDocumentCategory.WORK_ORDER,
    )
    _seed_company_document(
        db_session,
        company,
        text="SAMPLE completion certificate for completed infrastructure project and contract.",
        category=CompanyDocumentCategory.COMPLETION_CERTIFICATE,
    )
    _seed_company_document(
        db_session,
        company,
        text="SAMPLE GST registration certificate for a legally registered business.",
        category=CompanyDocumentCategory.GST_CERTIFICATE,
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    eligibility.requirements = [EligibilityRequirement(
        requirement="Bidder must have completed at least two similar supply-and-installation contracts.",
        category="experience", status="evidence_missing", explanation="Not assessed.",
        required_evidence=["Work orders", "Completion certificates"],
        tender_source_ids=["T1"], bidder_source_ids=[],
    )]

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )

    experience = next(item for item in result.results if item.requirement_type == "eligibility")
    assert experience.evidence_kind == "experience"
    assert {candidate.category for candidate in experience.candidates} == {"work_order", "completion_certificate"}
    assert all(candidate.category != "gst_certificate" for candidate in experience.candidates)


def test_product_specification_does_not_match_company_technical_certificate(db_session):
    from app.schemas.compliance import ComplianceRequirement

    tender = Tender(name="Product datasheet category test")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session,
        company,
        text="SAMPLE technical quality certificate for management procedures.",
        category=CompanyDocumentCategory.TECHNICAL_CERTIFICATION,
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    compliance.requirements = [ComplianceRequirement(
        requirement="Interactive flat-panel display: 65 inch minimum, 4K UHD, 20 touch points and 3-year warranty.",
        category="technical", status="evidence_missing", explanation="Not assessed.",
        required_action="Submit product datasheets and technical compliance sheet.",
        tender_source_ids=["T2"], bidder_source_ids=[],
    )]

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )

    product_spec = next(item for item in result.results if item.requirement_type == "compliance")
    assert product_spec.evidence_kind == "product_technical"
    assert product_spec.candidates == []


def test_bid_submission_deadline_is_not_misclassified_as_company_evidence(db_session):
    from app.schemas.compliance import ComplianceRequirement

    tender = Tender(name="Deadline category test")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session,
        company,
        text="SAMPLE work order project contract installation.",
        category=CompanyDocumentCategory.WORK_ORDER,
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    compliance.requirements = [ComplianceRequirement(
        requirement="Bid submission by deadline: 05 December 2026, 15:00 IST.",
        category="submission", status="evidence_missing", explanation="Not assessed.",
        required_action="Submit bid before deadline.", tender_source_ids=["T2"], bidder_source_ids=[],
    )]

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )

    deadline = next(item for item in result.results if item.requirement_type == "compliance")
    assert deadline.status == "not_company_evidence"
    assert deadline.candidates == []
    assert result.non_company_evidence_count == 1


def test_company_document_coverage_separates_missing_versions_from_unprocessed_versions(db_session):
    tender = Tender(name="Coverage accounting test")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(db_session, company, text=None, category=CompanyDocumentCategory.TURNOVER_CERTIFICATE)
    no_version_doc = CompanyDocument(
        company_id=company.id, title="Uploaded record awaiting a version", category=CompanyDocumentCategory.OTHER
    )
    db_session.add(no_version_doc)
    db_session.flush()
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )

    assert result.company_document_count == 2
    assert result.latest_version_count == 1
    assert result.processed_company_document_count == 0
    assert result.unprocessed_company_document_count == 1
    assert result.documents_without_versions_count == 1


def test_matching_never_reads_another_company_documents(db_session):
    tender = Tender(name="Company scope isolation test")
    selected_company = Company(legal_name="Selected Company Ltd")
    other_company = Company(legal_name="Other Company Ltd")
    db_session.add_all([tender, selected_company, other_company])
    db_session.flush()
    _seed_company_document(
        db_session,
        other_company,
        text="Annual turnover revenue gross receipts audited financial statements.",
        category=CompanyDocumentCategory.TURNOVER_CERTIFICATE,
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)

    result = create_evidence_matching_run(
        db_session, tender.id, selected_company.id, eligibility, compliance, today=date(2026, 11, 21)
    )

    financial = next(item for item in result.results if item.requirement_type == "eligibility")
    assert result.company_document_count == 0
    assert financial.candidates == []
    assert financial.status == "missing_evidence"


def test_placeholder_metadata_values_are_not_returned_as_real_metadata(db_session):
    from app.schemas.eligibility import EligibilityRequirement

    tender = Tender(name="Metadata placeholder test")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _document, version = _seed_company_document(
        db_session,
        company,
        text="Annual turnover revenue gross receipts audited financial statements.",
        category=CompanyDocumentCategory.TURNOVER_CERTIFICATE,
    )
    version.reference_number = "string"
    version.financial_year = "string"
    version.issuer = "N/A"
    db_session.flush()
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    eligibility.requirements = [EligibilityRequirement(
        requirement="Bidder must demonstrate minimum annual turnover through audited financial statements.",
        category="financial", status="evidence_missing", explanation="Not assessed.",
        required_evidence=["Audited financial statements"], tender_source_ids=["T1"], bidder_source_ids=[],
    )]

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    financial = next(item for item in result.results if item.requirement_type == "eligibility")
    assert financial.candidates
    assert financial.candidates[0].reference_number is None
    assert financial.candidates[0].financial_year is None
    assert financial.candidates[0].issuer is None


def test_read_matching_run_accepts_legacy_v1_result_snapshot(db_session):
    tender = Tender(name="Legacy V1 matching run")
    company = Company(legal_name="Legacy Company Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    legacy_result = {
        "requirement_id": "EL-1",
        "requirement_type": "eligibility",
        "requirement": "Bidder must provide registration certificate.",
        "category": "registration",
        "status": "manual_verification_required",
        "explanation": "Legacy V1 result.",
        "required_evidence": ["Registration certificate"],
        "tender_sources": [],
        "candidates": [],
    }
    run = TenderEvidenceMatchingRun(
        tender_id=tender.id,
        company_id=company.id,
        status="completed",
        summary="Legacy snapshot",
        result_json={"results": [legacy_result]},
    )
    db_session.add(run)
    db_session.flush()

    reread = read_matching_run(run)

    assert reread.results[0].evidence_kind == "generic"
    assert reread.results[0].candidates == []


def test_emd_does_not_match_draft_non_blacklisting_declaration(db_session):
    from app.schemas.compliance import ComplianceRequirement

    tender = Tender(name="EMD false-positive regression")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session,
        company,
        text="Draft non-blacklisting declaration dated December 2026. The bidder confirms it is not debarred.",
        category=CompanyDocumentCategory.OTHER,
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    compliance.requirements = [ComplianceRequirement(
        requirement="The bidder shall submit EMD of INR 80,000 through a banker's cheque or demand draft.",
        category="financial", status="evidence_missing", explanation="Not assessed.",
        required_action="Provide earnest money deposit instrument.",
        tender_source_ids=["T2"], bidder_source_ids=[],
    )]

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    emd = next(item for item in result.results if item.requirement_type == "compliance")
    assert emd.evidence_kind == "emd"
    assert emd.candidates == []
    assert emd.status == "missing_evidence"


def test_emd_accepts_only_explicit_payment_instrument_in_other_category(db_session):
    from app.schemas.compliance import ComplianceRequirement

    tender = Tender(name="EMD positive regression")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session,
        company,
        text="Earnest Money Deposit instrument. Banker's cheque number 12345 issued for INR 80,000.",
        category=CompanyDocumentCategory.OTHER,
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    compliance.requirements = [ComplianceRequirement(
        requirement="The bidder shall submit EMD of INR 80,000 through a banker's cheque or demand draft.",
        category="financial", status="evidence_missing", explanation="Not assessed.",
        required_action="Provide earnest money deposit instrument.",
        tender_source_ids=["T2"], bidder_source_ids=[],
    )]

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    emd = next(item for item in result.results if item.requirement_type == "compliance")
    assert emd.evidence_kind == "emd"
    assert len(emd.candidates) == 1
    assert emd.candidates[0].category_match is False


def test_other_category_technical_certificate_does_not_match_product_datasheet_requirement(db_session):
    from app.schemas.compliance import ComplianceRequirement

    tender = Tender(name="Product evidence strictness regression")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session,
        company,
        text="Technical quality certificate for ISO management system certification.",
        category=CompanyDocumentCategory.OTHER,
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    compliance.requirements = [ComplianceRequirement(
        requirement="Interactive display product specifications include 65 inch minimum and 4K UHD resolution.",
        category="technical", status="evidence_missing", explanation="Not assessed.",
        required_action="Submit product datasheets and technical compliance sheet.",
        tender_source_ids=["T2"], bidder_source_ids=[],
    )]

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    product = next(item for item in result.results if item.requirement_type == "compliance")
    assert product.evidence_kind == "product_technical"
    assert product.candidates == []


def test_expired_metrics_distinguish_requirement_count_from_unique_versions(db_session):
    from app.schemas.eligibility import EligibilityRequirement

    tender = Tender(name="Expired metric regression")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session,
        company,
        text="Annual turnover revenue gross receipts audited financial statements.",
        category=CompanyDocumentCategory.TURNOVER_CERTIFICATE,
        expiry_date=date(2025, 1, 1),
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    eligibility.requirements = [
        EligibilityRequirement(
            requirement="Bidder must demonstrate annual turnover through audited financial statements.",
            category="financial", status="evidence_missing", explanation="Not assessed.",
            required_evidence=["Audited financial statements"], tender_source_ids=["T1"], bidder_source_ids=[],
        ),
        EligibilityRequirement(
            requirement="Bidder must demonstrate annual revenue through audited financial statements.",
            category="financial", status="evidence_missing", explanation="Not assessed.",
            required_evidence=["Audited financial statements"], tender_source_ids=["T1"], bidder_source_ids=[],
        ),
    ]

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    assert result.expired_evidence_count == 2
    assert result.unique_expired_document_count == 1


def test_placeholder_metadata_is_cleaned_when_reading_legacy_snapshots():
    from app.schemas.evidence_matching import CompanyEvidenceCandidate

    candidate = CompanyEvidenceCandidate(
        company_document_id=uuid4(),
        document_title="Legacy doc",
        category="turnover_certificate",
        version_id=uuid4(),
        version_number=1,
        original_filename="legacy.pdf",
        relevance_score=0.7,
        matched_terms=["turnover"],
        evidence_state="validity_not_recorded",
        review_note="Review",
        financial_year="string",
        issuer="N/A",
        reference_number="null",
    )
    assert candidate.financial_year is None
    assert candidate.issuer is None
    assert candidate.reference_number is None


def test_experience_lookback_preceding_bid_deadline_is_not_administrative(db_session):
    from app.schemas.eligibility import EligibilityRequirement

    tender = Tender(name="Experience lookback classification")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    eligibility.requirements = [EligibilityRequirement(
        requirement=(
            "Bidder must have completed at least two similar supply-and-installation "
            "contracts during the five financial years preceding the bid deadline"
        ),
        category="experience", status="evidence_missing", explanation="Not assessed.",
        required_evidence=["Purchase orders or work orders", "Completion certificates"],
        tender_source_ids=["T1"], bidder_source_ids=[],
    )]

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    experience = next(item for item in result.results if item.requirement_type == "eligibility")
    assert experience.evidence_kind == "experience"
    assert experience.status == "missing_evidence"


@pytest.mark.parametrize("requirement", [
    "Interactive flat-panel displays: minimum 65-inch diagonal size",
    "Interactive flat-panel displays: built-in speakers",
    "Interactive flat-panel displays: at least 20 simultaneous touch points",
    "Mini PC: at least Intel Core i5 or equivalent processor",
    "Mini PC: minimum 8 GB RAM",
    "Mini PC: minimum 256 GB SSD",
])
def test_individual_product_attributes_are_classified_as_product_technical(db_session, requirement):
    from app.schemas.compliance import ComplianceRequirement

    tender = Tender(name="Product attribute classification")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    compliance.requirements = [ComplianceRequirement(
        requirement=requirement,
        category="technical", status="evidence_missing", explanation="Not assessed.",
        required_action="Bidder to demonstrate the product specification in technical bid",
        tender_source_ids=["T2"], bidder_source_ids=[],
    )]

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    product = next(item for item in result.results if item.requirement_type == "compliance")
    assert product.evidence_kind == "product_technical"


def test_unprocessed_registration_document_does_not_trigger_processing_for_product_or_emd(db_session):
    from app.schemas.compliance import ComplianceRequirement

    tender = Tender(name="Relevant processing status")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(db_session, company, text=None, category=CompanyDocumentCategory.GST_CERTIFICATE)
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    compliance.requirements = [
        ComplianceRequirement(
            requirement="EMD INR 80,000 via banker's cheque or demand draft",
            category="financial", status="evidence_missing", explanation="Not assessed.",
            required_action="Submit EMD instrument details", tender_source_ids=["T2"], bidder_source_ids=[],
        ),
        ComplianceRequirement(
            requirement="Interactive flat-panel displays: minimum 65-inch diagonal size",
            category="technical", status="evidence_missing", explanation="Not assessed.",
            required_action="Demonstrate display specification", tender_source_ids=["T2"], bidder_source_ids=[],
        ),
    ]

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    emd = next(item for item in result.results if item.evidence_kind == "emd")
    product = next(item for item in result.results if item.evidence_kind == "product_technical")
    assert emd.status == "missing_evidence"
    assert product.status == "missing_evidence"


def test_unprocessed_other_document_uses_title_and_filename_to_detect_financial_evidence(db_session):
    tender = Tender(name="Unprocessed document title inference")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session, company, text=None, category=CompanyDocumentCategory.OTHER,
        title="Turnover Schedule", filename="turnover_schedule.xlsx",
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    financial = next(item for item in result.results if item.evidence_kind == "financial")
    registration = next(item for item in result.results if item.evidence_kind == "registration")
    assert financial.status == "processing_required"
    assert registration.status == "missing_evidence"
    assert [item.original_filename for item in financial.pending_versions] == ["turnover_schedule.xlsx"]
    assert registration.pending_versions == []


def test_v5_candidate_metrics_count_synthetic_and_non_synthetic_unique_versions(db_session):
    from app.schemas.compliance import ComplianceRequirement
    from app.schemas.eligibility import EligibilityRequirement

    tender = Tender(name="Candidate metrics and contract grouping")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session,
        company,
        text=(
            "SAMPLE - NOT VALID - FOR SOFTWARE TESTING ONLY. Work order WO-2025-001 "
            "for a similar supply and installation contract experience project."
        ),
        category=CompanyDocumentCategory.WORK_ORDER,
        title="Sample work order",
        filename="sample-work-order.pdf",
        reference_number=None,
    )
    _seed_company_document(
        db_session,
        company,
        text=(
            "Completion certificate for contract WO-2025-001 certifies completed "
            "supply and installation work under the project."
        ),
        category=CompanyDocumentCategory.COMPLETION_CERTIFICATE,
        title="Completion certificate",
        filename="completion-certificate.pdf",
        reference_number=None,
    )

    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    experience_text = (
        "Bidder must have completed at least two similar supply-and-installation contracts "
        "during the five financial years preceding the bid deadline"
    )
    eligibility.requirements = [EligibilityRequirement(
        requirement=experience_text,
        category="experience",
        status="evidence_missing",
        explanation="Not assessed.",
        required_evidence=["Purchase orders or work orders", "Completion certificates"],
        tender_source_ids=["T1"],
        bidder_source_ids=[],
    )]
    compliance.requirements = [ComplianceRequirement(
        requirement="Bidder must submit evidence of two similar supply and installation contracts.",
        category="experience",
        status="evidence_missing",
        explanation="Not assessed.",
        required_action="Submit work orders and completion certificates for similar contracts.",
        tender_source_ids=["T2"],
        bidder_source_ids=[],
    )]

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )

    assert result.requirements_with_candidates == 2
    # Two matching documents appear against each of two requirements.
    assert result.candidate_occurrence_count == 4
    # The same two immutable versions are counted once in the unique total.
    assert result.unique_candidate_document_count == 2
    assert result.synthetic_candidate_document_count == 1
    assert result.non_synthetic_candidate_document_count == 1
    saved_run = db_session.query(TenderEvidenceMatchingRun).filter_by(
        tender_id=tender.id, company_id=company.id
    ).one()
    reread = read_matching_run(saved_run)
    assert reread.candidate_occurrence_count == 4
    assert reread.unique_candidate_document_count == 2
    assert reread.synthetic_candidate_document_count == 1
    for match in result.results:
        if match.evidence_kind == "experience":
            assert match.experience_evidence is not None
            # The work order and completion certificate share one explicit reference.
            assert match.experience_evidence.distinct_referenced_contract_count == 1
            group = match.experience_evidence.contract_groups[0]
            assert group.reference_number == "WO-2025-001"
            assert group.has_order_document is True
            assert group.has_completion_certificate is True


def test_v5_different_explicit_contract_references_are_grouped_separately(db_session):
    from app.schemas.eligibility import EligibilityRequirement

    tender = Tender(name="Distinct contract references")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    for reference in ("WO-2024-101", "WO-2025-202"):
        _seed_company_document(
            db_session,
            company,
            text=f"Work order {reference} for a similar supply installation contract and experience project.",
            category=CompanyDocumentCategory.WORK_ORDER,
            title=f"Work order {reference}",
            filename=f"{reference}.pdf",
            reference_number=reference,
        )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    eligibility.requirements = [EligibilityRequirement(
        requirement="Two similar completed supply-and-installation contracts in the previous five financial years",
        category="experience",
        status="evidence_missing",
        explanation="Not assessed.",
        required_evidence=["Work orders and completion certificates"],
        tender_source_ids=["T1"],
        bidder_source_ids=[],
    )]

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    experience = next(item for item in result.results if item.requirement_type == "eligibility")
    assert experience.experience_evidence is not None
    assert experience.experience_evidence.distinct_referenced_contract_count == 2
    assert {group.reference_number for group in experience.experience_evidence.contract_groups} == {
        "WO-2024-101", "WO-2025-202"
    }


def test_v5_unreferenced_experience_documents_are_not_counted_as_distinct_contracts(db_session):
    from app.schemas.eligibility import EligibilityRequirement

    tender = Tender(name="Unreferenced work order")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session,
        company,
        text="Work order for a similar supply and installation contract experience project.",
        category=CompanyDocumentCategory.WORK_ORDER,
        title="Work order without a reference",
        filename="unreferenced-work-order.pdf",
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    eligibility.requirements = [EligibilityRequirement(
        requirement="Two similar supply-and-installation contracts in the previous five financial years",
        category="experience",
        status="evidence_missing",
        explanation="Not assessed.",
        required_evidence=["Work orders"],
        tender_source_ids=["T1"],
        bidder_source_ids=[],
    )]

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    experience = next(item for item in result.results if item.requirement_type == "eligibility")
    assert experience.experience_evidence is not None
    assert experience.experience_evidence.distinct_referenced_contract_count == 0
    assert experience.experience_evidence.unreferenced_document_count == 1
    assert "not counted as distinct contracts" in experience.experience_evidence.note


def test_v5_processing_required_lists_only_exactly_relevant_unprocessed_versions(db_session):
    from app.schemas.compliance import ComplianceRequirement

    tender = Tender(name="Exact pending versions")
    company = Company(legal_name="Example Infrastructure Ltd")
    db_session.add_all([tender, company])
    db_session.flush()
    _seed_company_document(
        db_session,
        company,
        text=None,
        category=CompanyDocumentCategory.OTHER,
        title="Interactive display product datasheet",
        filename="display-product-datasheet.pdf",
    )
    _seed_company_document(
        db_session,
        company,
        text=None,
        category=CompanyDocumentCategory.TURNOVER_CERTIFICATE,
        title="Annual turnover certificate",
        filename="turnover-certificate.pdf",
    )
    eligibility, compliance = _analysis_pair()
    eligibility.tender_id = str(tender.id)
    compliance.tender_id = str(tender.id)
    compliance.requirements = [
        ComplianceRequirement(
            requirement="Interactive flat-panel display: minimum 65-inch size, 4K UHD resolution",
            category="technical",
            status="evidence_missing",
            explanation="Not assessed.",
            required_action="Provide product datasheet",
            tender_source_ids=["T2"],
            bidder_source_ids=[],
        )
    ]

    result = create_evidence_matching_run(
        db_session, tender.id, company.id, eligibility, compliance, today=date(2026, 11, 21)
    )
    product = next(item for item in result.results if item.evidence_kind == "product_technical")
    financial = next(item for item in result.results if item.evidence_kind == "financial")
    assert product.status == "processing_required"
    assert [item.original_filename for item in product.pending_versions] == ["display-product-datasheet.pdf"]
    assert product.pending_versions[0].inferred_evidence_kind == "product_technical"
    assert financial.status == "processing_required"
    assert [item.original_filename for item in financial.pending_versions] == ["turnover-certificate.pdf"]
