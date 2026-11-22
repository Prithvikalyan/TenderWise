"""Consolidated bid-readiness report endpoint."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.schemas.report import BidReadinessReportResponse
from app.services.ai import AIServiceError
from app.services.report import generate_bid_readiness_report

router = APIRouter(
    prefix="/tenders/{tender_id}",
    tags=["bid readiness report"],
)


@router.post(
    "/report",
    response_model=BidReadinessReportResponse,
)
def create_bid_readiness_report(
    tender_id: UUID,
    company_id: UUID | None = Query(default=None),
    matching_run_id: UUID | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> BidReadinessReportResponse:
    try:
        return generate_bid_readiness_report(
            session,
            tender_id,
            company_id=company_id,
            matching_run_id=matching_run_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AIServiceError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc
