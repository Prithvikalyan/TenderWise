
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.schemas.analysis import TenderAnalysisResponse
from app.services.ai import AIServiceError
from app.services.analysis import analyze_tender

router = APIRouter(
    prefix="/tenders/{tender_id}",
    tags=["tender analysis"],
)


@router.post(
    "/analysis",
    response_model=TenderAnalysisResponse,
)
def generate_tender_analysis(
    tender_id: UUID,
    session: Session = Depends(get_db_session),
) -> TenderAnalysisResponse:

    try:
        return analyze_tender(session, tender_id)

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
