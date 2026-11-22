
"""Tender compliance analysis API route."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.schemas.compliance import ComplianceAnalysisResponse
from app.services.ai import AIServiceError
from app.services.compliance import analyze_compliance

router = APIRouter(
    prefix="/tenders/{tender_id}",
    tags=["tender compliance"],
)


@router.post(
    "/compliance",
    response_model=ComplianceAnalysisResponse,
)
def generate_compliance_analysis(
    tender_id: UUID,
    session: Session = Depends(get_db_session),
) -> ComplianceAnalysisResponse:
    try:
        return analyze_compliance(session, tender_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail="Tender not found",
        ) from exc
    except AIServiceError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc
