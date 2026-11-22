"""Eligibility analysis API route."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.schemas.eligibility import EligibilityAnalysisResponse
from app.services.ai import AIServiceError
from app.services.eligibility import analyze_eligibility

router = APIRouter(
    prefix="/tenders/{tender_id}",
    tags=["tender eligibility"],
)


@router.post(
    "/eligibility",
    response_model=EligibilityAnalysisResponse,
)
def generate_eligibility_analysis(
    tender_id: UUID,
    session: Session = Depends(get_db_session),
) -> EligibilityAnalysisResponse:
    try:
        return analyze_eligibility(session, tender_id)
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
