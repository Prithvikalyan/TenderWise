"""Tender requirement to reusable company evidence matching endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.models.company import Company
from app.models.tender import Tender
from app.schemas.evidence_matching import (
    EvidenceMatchingRunListResponse,
    EvidenceMatchingRunResponse,
)
from app.services.ai import AIServiceError
from app.services.compliance import analyze_compliance
from app.services.eligibility import analyze_eligibility
from app.services import evidence_matching as matching_service

router = APIRouter(
    prefix="/tenders/{tender_id}/companies/{company_id}/evidence-matching-runs",
    tags=["tender evidence matching"],
)
MAX_PAGE = 1_000_000


def _check_scope(session: Session, tender_id: UUID, company_id: UUID) -> None:
    if session.get(Tender, tender_id) is None:
        raise HTTPException(status_code=404, detail="Tender not found")
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    if company.is_archived:
        raise HTTPException(status_code=409, detail="Archived companies cannot be used for evidence matching")


@router.post("", response_model=EvidenceMatchingRunResponse, status_code=status.HTTP_201_CREATED)
def create_evidence_matching_run(
    tender_id: UUID,
    company_id: UUID,
    session: Session = Depends(get_db_session),
) -> EvidenceMatchingRunResponse:
    _check_scope(session, tender_id, company_id)
    try:
        # These agents provide the requirement lists and tender-side citations.
        # Existing bidder-side status fields are deliberately not used as proof.
        eligibility = analyze_eligibility(session, tender_id)
        compliance = analyze_compliance(session, tender_id)
        return matching_service.create_evidence_matching_run(
            session, tender_id, company_id, eligibility, compliance
        )
    except matching_service.TenderOrCompanyNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Tender or company not found") from exc
    except matching_service.ArchivedCompanyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail="Evidence matching could not be saved") from exc


@router.get("", response_model=EvidenceMatchingRunListResponse)
def list_evidence_matching_runs(
    tender_id: UUID,
    company_id: UUID,
    page: int = Query(1, ge=1, le=MAX_PAGE),
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> EvidenceMatchingRunListResponse:
    _check_scope(session, tender_id, company_id)
    items, total = matching_service.list_matching_runs(
        session, tender_id, company_id, page=page, limit=limit
    )
    return EvidenceMatchingRunListResponse(
        items=items,
        total=total,
        page=page,
        limit=limit,
        offset=(page - 1) * limit,
    )


@router.get("/{run_id}", response_model=EvidenceMatchingRunResponse)
def read_evidence_matching_run(
    tender_id: UUID,
    company_id: UUID,
    run_id: UUID,
    session: Session = Depends(get_db_session),
) -> EvidenceMatchingRunResponse:
    _check_scope(session, tender_id, company_id)
    run = matching_service.get_matching_run(session, tender_id, company_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Evidence matching run not found")
    return matching_service.read_matching_run(run)
